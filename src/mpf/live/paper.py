"""Paper-trading раннер: виртуальные сделки на живых данных MEXC.

Деньги не используются. Раннер опрашивает биржу, кормит закрытые свечи в
`LiveState` (та же логика, что в бэктесте) и ведёт лог сделок и состояния.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from mpf.config import StrategyParams
from mpf.data.collector import timeframe_to_ms
from mpf.exchange.base import HistoryExchange
from mpf.live.state import LiveState
from mpf.models import ClosedTrade


@dataclass(slots=True)
class PaperConfig:
    symbols: list[str]
    params: StrategyParams
    initial_equity: float = 1000.0
    max_concurrent: int = 3
    timeframe: str = "1m"
    warmup: int = 200
    trades_path: str = "data/paper_trades.jsonl"
    state_path: str = "data/paper_state.json"


@dataclass(slots=True)
class PaperTrader:
    exchange: HistoryExchange
    config: PaperConfig
    state: LiveState = field(init=False)
    last_ts: dict[str, int] = field(default_factory=dict, init=False)
    _logged: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        from mpf.stats import load_trades

        self.state = LiveState(
            self.config.params,
            self.config.initial_equity,
            self.config.max_concurrent,
        )
        for trade in load_trades(self.config.trades_path):
            self.state.trades.append(trade)
            self.state.equity += trade.net_pnl

    # --- прогрев ---

    def warmup(self) -> int:
        """Загрузить историю для расчёта индикаторов (без исполнения сигналов)."""
        loaded = 0
        for symbol in self.config.symbols:
            candles = self.exchange.fetch_ohlcv(
                symbol, self.config.timeframe, self.config.warmup
            )
            if not candles:
                continue
            self.state.prime(symbol, candles)
            self.last_ts[symbol] = candles[-1].ts
            loaded += 1
        return loaded

    # --- один цикл опроса ---

    def poll_once(self, now_ms: int | None = None) -> list[ClosedTrade]:
        """Скачать новые закрытые свечи по всем символам и обработать их."""
        now = now_ms if now_ms is not None else self.exchange.server_time_ms()
        interval = timeframe_to_ms(self.config.timeframe)
        before = len(self.state.trades)

        for symbol in self.config.symbols:
            last = self.last_ts.get(symbol, 0)
            try:
                candles = self.exchange.fetch_ohlcv(symbol, self.config.timeframe, limit=5)
            except Exception:
                continue
            for candle in sorted(candles, key=lambda c: c.ts):
                is_closed = candle.ts + interval <= now
                if is_closed and candle.ts > last:
                    self.state.on_candle(symbol, candle)
                    self.last_ts[symbol] = candle.ts

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
        """Основной цикл. `iterations=None` — бесконечно."""
        self.warmup()
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

    # --- сохранение ---

    def _persist(self, new_trades: list[ClosedTrade]) -> None:
        trades_path = Path(self.config.trades_path)
        trades_path.parent.mkdir(parents=True, exist_ok=True)
        if new_trades:
            with trades_path.open("a", encoding="utf-8") as fh:
                for trade in new_trades:
                    fh.write(json.dumps(asdict(trade), ensure_ascii=False) + "\n")
            self._logged += len(new_trades)

        state = {
            "equity": round(self.state.equity, 4),
            "trades_total": len(self.state.trades),
            "open_positions": [
                {
                    "symbol": s,
                    "entries": len(st.position.entries),
                    "avg_entry": round(st.position.avg_entry, 8),
                    "qty": round(st.position.qty, 8),
                }
                for s, st in self.state.positions.items()
            ],
            "armed": self.state.armed_symbols,
        }
        state_path = Path(self.config.state_path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")