"""Сигнальный paper-раннер: торгует по сигналам Telegram-канала.

Логика:
  1. Читает новые сигналы из публичного превью канала.
  2. Фильтрует их (PUMP, биржа, вероятности отката, флаги индикаторов).
  3. Открывает ВИРТУАЛЬНЫЙ шорт по текущей цене с размером от риска.
  4. Управляет позицией той же логикой (`LiveState`), что и бэктест:
     усреднение, тейк, стоп, ликвидация.

Деньги не используются.
"""

from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from mpf.config import StrategyParams
from mpf.data.collector import timeframe_to_ms
from mpf.exchange.base import HistoryExchange
from mpf.live.state import LiveState
from mpf.models import ClosedTrade, Ticker
from mpf.signals.telegram import TelegramSignal, fetch_signals


@dataclass(frozen=True, slots=True)
class SignalFilter:
    """Какие сигналы канала принимать."""

    pumps_only: bool = True
    require_exchange_ok: bool = True
    min_prob_fp: int = 0
    min_prob_oi: int = 0
    require_indicators_ok: bool = False   # RSI_5m, ATR_K, ADX — все ✅

    def accepts(self, signal: TelegramSignal) -> bool:
        if self.pumps_only and not signal.is_pump:
            return False
        if self.require_exchange_ok and not signal.exchange_ok:
            return False
        if (signal.prob_fp or 0) < self.min_prob_fp:
            return False
        if (signal.prob_oi or 0) < self.min_prob_oi:
            return False
        return not self.require_indicators_ok or bool(
            signal.rsi_ok and signal.atr_ok and signal.adx_ok
        )


@dataclass(slots=True)
class SignalPaperConfig:
    params: StrategyParams
    filter: SignalFilter = field(default_factory=SignalFilter)
    initial_equity: float = 1000.0
    max_concurrent: int = 3
    timeframe: str = "5m"
    warmup: int = 200
    url: str | None = None
    take_existing: int = 0    # сколько последних сигналов взять при старте
    min_funding_rate: float | None = None  # None = фильтр выключен
    max_oi_growth_pct: float = 0.0  # 0 = фильтр выключен
    oi_window_min: int = 15
    funding_interval_min: int = 480  # период выплаты funding (8ч)
    trades_path: str = "data/follow_trades.jsonl"
    state_path: str = "data/follow_state.json"


@dataclass(slots=True)
class SignalPaperTrader:
    exchange: HistoryExchange
    config: SignalPaperConfig
    fetch_signals_fn: Callable[..., list[TelegramSignal]] = fetch_signals
    state: LiveState = field(init=False)
    last_ts: dict[str, int] = field(default_factory=dict, init=False)
    seen: set[int] = field(default_factory=set, init=False)
    _seeded: bool = field(default=False, init=False)
    _tickers: dict[str, Ticker] = field(default_factory=dict, init=False)
    _oi_history: dict[str, deque[tuple[int, float]]] = field(default_factory=dict, init=False)
    _funding_ts: dict[str, int] = field(default_factory=dict, init=False)
    stats: dict[str, int] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.state = LiveState(
            self.config.params,
            self.config.initial_equity,
            self.config.max_concurrent,
            self_detect=False,
        )
        self.stats = {
            "signals": 0,
            "entered": 0,
            "blocked_filter": 0,
            "blocked_funding": 0,
            "blocked_oi": 0,
            "blocked_hours": 0,
            "blocked_slot": 0,
            "would_block_funding": 0,
            "would_block_oi": 0,
        }
        self._resume()

    def _resume(self) -> None:
        """Восстановить сделки/equity из trades.jsonl после перезапуска."""
        from mpf.stats import load_trades

        for trade in load_trades(self.config.trades_path):
            self.state.trades.append(trade)
            self.state.equity += trade.net_pnl

    def poll_once(self, now_ms: int | None = None) -> list[ClosedTrade]:
        now = now_ms if now_ms is not None else self.exchange.server_time_ms()
        interval = timeframe_to_ms(self.config.timeframe)
        before = len(self.state.trades)

        self._refresh_tickers(now)
        self._refresh_open_positions(now, interval)
        self._accrue_funding(now)
        self._process_signals(now, interval)

        new_trades = self.state.trades[before:]
        self._persist(new_trades)
        return new_trades

    def run(
        self,
        *,
        poll_seconds: int = 30,
        iterations: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
        on_tick: Callable[[int, list[ClosedTrade]], None] | None = None,
    ) -> LiveState:
        tick = 0
        while iterations is None or tick < iterations:
            new_trades = self.poll_once()
            tick += 1
            if on_tick is not None:
                on_tick(tick, new_trades)
            if iterations is not None and tick >= iterations:
                break
            sleep(poll_seconds)
        return self.state

    # --- внутреннее ---

    def _refresh_tickers(self, now: int) -> None:
        """Обновить снимки MEXC: funding и Open Interest по всем парам."""
        try:
            tickers = self.exchange.fetch_futures_tickers()
        except Exception:
            return
        self._tickers = {t.symbol: t for t in tickers}
        for symbol, ticker in self._tickers.items():
            history = self._oi_history.get(symbol)
            if history is None:
                history = deque(maxlen=60)
                self._oi_history[symbol] = history
            history.append((now, ticker.open_interest))

    def _oi_growth_pct(self, symbol: str, now: int) -> float | None:
        """Рост Open Interest за окно `oi_window_min`, % (None — мало данных)."""
        history = self._oi_history.get(symbol)
        if not history or len(history) < 2:
            return None
        window_ms = self.config.oi_window_min * 60_000
        past: float | None = None
        for ts, oi in history:
            if now - ts <= window_ms:
                past = oi
                break
        current = history[-1][1]
        if past is None or past <= 0:
            return None
        return (current / past - 1.0) * 100.0

    def _accrue_funding(self, now: int) -> None:
        """Начислить funding открытым позициям (шорт получает при rate>0)."""
        interval_ms = self.config.funding_interval_min * 60_000
        for symbol in self.state.open_symbols:
            ticker = self._tickers.get(symbol)
            if ticker is None:
                continue
            last = self._funding_ts.get(symbol)
            if last is None:
                self._funding_ts[symbol] = now
                continue
            elapsed = now - last
            if elapsed < interval_ms:
                continue
            periods = elapsed // interval_ms
            position = self.state.positions[symbol].position
            notional = position.avg_entry * position.qty
            self.state.apply_funding(symbol, notional * ticker.funding_rate * periods)
            self._funding_ts[symbol] = last + periods * interval_ms

    def _refresh_open_positions(self, now: int, interval: int) -> None:
        for symbol in list(self.state.open_symbols):
            last = self.last_ts.get(symbol, 0)
            try:
                candles = self.exchange.fetch_ohlcv(symbol, self.config.timeframe, limit=5)
            except Exception:  # одна пара не должна ронять цикл
                continue
            for candle in sorted(candles, key=lambda c: c.ts):
                if candle.ts + interval <= now and candle.ts > last:
                    self.state.on_candle(symbol, candle)
                    self.last_ts[symbol] = candle.ts

    def _process_signals(self, now: int, interval: int) -> None:
        url = self.config.url
        signals = (
            self.fetch_signals_fn(url) if url else self.fetch_signals_fn()
        )
        if not self._seeded:
            # На старте не торгуем по старым сигналам — только запоминаем их.
            self.seen.update(s.post_id for s in signals)
            self._seeded = True
            if self.config.take_existing > 0:
                accepted = [s for s in signals if self.config.filter.accepts(s)]
                for signal in accepted[-self.config.take_existing:]:
                    self._try_enter(signal, now, interval)
            return

        for signal in signals:
            if signal.post_id in self.seen:
                continue
            self.seen.add(signal.post_id)
            self._try_enter(signal, now, interval)

    def _try_enter(self, signal: TelegramSignal, now: int, interval: int) -> None:
        self.stats["signals"] += 1
        if not self.config.filter.accepts(signal):
            self.stats["blocked_filter"] += 1
            return
        symbol = signal.symbol
        if symbol in self.state.open_symbols:
            return
        ticker = self._tickers.get(symbol)
        growth = self._oi_growth_pct(symbol, now)
        # Наблюдательные счётчики — считаем всегда, даже если фильтр выключен.
        if ticker is not None and ticker.funding_rate < 0:
            self.stats["would_block_funding"] += 1
        if growth is not None and growth > 8.0:
            self.stats["would_block_oi"] += 1
        reason = self._context_reason(symbol, now, ticker, growth)
        if reason is not None:
            self.stats[f"blocked_{reason}"] += 1
            return
        try:
            candles = self.exchange.fetch_ohlcv(symbol, self.config.timeframe, self.config.warmup)
        except Exception:
            return
        closed = [c for c in candles if c.ts + interval <= now]
        if not closed:
            return
        self.state.prime(symbol, closed)
        self.last_ts[symbol] = closed[-1].ts
        if self.state.open_position(symbol, closed[-1]):
            self.stats["entered"] += 1
            self._funding_ts[symbol] = now
        else:
            self.stats["blocked_slot"] += 1

    def _context_reason(
        self, symbol: str, now: int, ticker: Ticker | None, growth: float | None
    ) -> str | None:
        """Причина блокировки входа или None. Фильтры применяются только если включены."""
        hours = self.config.params.trading_hours_utc
        if hours and (now // 3_600_000) % 24 not in hours:
            return "hours"
        if ticker is None:
            return None
        if (
            self.config.min_funding_rate is not None
            and ticker.funding_rate < self.config.min_funding_rate
        ):
            return "funding"
        if (
            self.config.max_oi_growth_pct > 0
            and growth is not None
            and growth > self.config.max_oi_growth_pct
        ):
            return "oi"
        return None

    def _persist(self, new_trades: list[ClosedTrade]) -> None:
        trades_path = Path(self.config.trades_path)
        trades_path.parent.mkdir(parents=True, exist_ok=True)
        if new_trades:
            with trades_path.open("a", encoding="utf-8") as fh:
                for trade in new_trades:
                    fh.write(json.dumps(asdict(trade), ensure_ascii=False) + "\n")

        state = {
            "equity": round(self.state.equity, 4),
            "trades_total": len(self.state.trades),
            "signals_seen": len(self.seen),
            "stats": self.stats,
            "open_positions": [
                {
                    "symbol": s,
                    "entries": len(st.position.entries),
                    "avg_entry": round(st.position.avg_entry, 8),
                    "qty": round(st.position.qty, 8),
                }
                for s, st in self.state.positions.items()
            ],
        }
        state_path = Path(self.config.state_path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")