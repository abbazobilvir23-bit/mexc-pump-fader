"""Живая машина состояний стратегии: одна логика для бэктеста и paper-trading.

`LiveState.on_candle(symbol, candle)` обрабатывает свечи по мере поступления
(в порядке времени). Бэктест просто «кормит» её историей, paper-trading —
реальными свечами. Это исключает расхождение логики между режимами.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from mpf.config import StrategyParams
from mpf.core.pump_detector import detect_pump
from mpf.core.risk import entry_sizes, total_qty_for_notional, total_qty_for_risk
from mpf.core.strategy import Decision, Position, decide
from mpf.models import Candle, ClosedTrade, ExitReason, Side


def _adverse_pct(high: float, reference: float) -> float:
    """Насколько высоко цена ушла против короткой позиции, %."""
    if reference <= 0:
        return 0.0
    return (high - reference) / reference * 100.0


@dataclass(slots=True)
class _OpenState:
    position: Position
    planned_sizes: list[float]
    fees: float
    next_size_idx: int
    opened_ts: int
    max_adverse_pct: float = 0.0
    funding: float = 0.0


@dataclass(slots=True)
class _Armed:
    """Сигнал сработал, ждём откат от пика для входа."""

    peak: float
    start_idx: int


class LiveState:
    """Инкрементальная стратегия. Держит окно свечей, позиции и «взведённые» сигналы."""

    def __init__(
        self,
        params: StrategyParams,
        initial_equity: float = 1000.0,
        max_concurrent: int = 3,
        *,
        self_detect: bool = True,
    ) -> None:
        params.validate()
        self.params = params
        self.initial_equity = initial_equity
        self.equity = initial_equity
        self.max_concurrent = max_concurrent
        self.self_detect = self_detect
        self.trades: list[ClosedTrade] = []
        self.equity_curve: list[tuple[int, float]] = []

        self._window_len = (
            max(
                params.volume_lookback,
                params.rsi_period * 10,
                params.pump_lookback,
                params.atr_period * 3,
                params.adx_period * 3,
            )
            + 1
        )
        self._candles: dict[str, deque[Candle]] = {}
        self._idx: dict[str, int] = {}
        self._positions: dict[str, _OpenState] = {}
        self._armed: dict[str, _Armed] = {}

    # --- публичный интерфейс ---

    @property
    def open_symbols(self) -> list[str]:
        return list(self._positions)

    @property
    def armed_symbols(self) -> list[str]:
        return list(self._armed)

    @property
    def positions(self) -> dict[str, _OpenState]:
        """Открытые позиции (для мониторинга и сохранения состояния)."""
        return self._positions

    def on_candle(self, symbol: str, candle: Candle) -> None:
        window = self._candles.get(symbol)
        if window is None:
            window = deque(maxlen=self._window_len)
            self._candles[symbol] = window
        window.append(candle)
        idx = self._idx.get(symbol, 0)
        self._idx[symbol] = idx + 1

        if symbol in self._positions:
            self._process_open(symbol, candle)
            return
        if symbol in self._armed:
            self._try_armed(symbol, candle, idx)
            return
        # В режиме внешних сигналов входы не ищем сами — только управляем позицией.
        if not self.self_detect:
            return
        if len(self._positions) >= self.max_concurrent:
            return

        if detect_pump(symbol, list(window), self.params) is not None:
            self._armed[symbol] = _Armed(peak=candle.high, start_idx=idx)

    def open_position(
        self, symbol: str, candle: Candle, entry_price: float | None = None
    ) -> bool:
        """Открыть шорт вручную (по внешнему сигналу). False — слот занят/уже есть."""
        if symbol in self._positions or len(self._positions) >= self.max_concurrent:
            return False
        self._open(symbol, candle, entry_price)
        return symbol in self._positions

    def apply_funding(self, symbol: str, amount: float) -> None:
        """Начислить funding по открытой позиции (для шорта + при rate>0)."""
        open_state = self._positions.get(symbol)
        if open_state is not None:
            open_state.funding += amount

    def finalize(self, last_candles: dict[str, Candle]) -> None:
        """Закрыть оставшиеся позиции по последней известной свече."""
        for symbol in list(self._positions):
            candle = last_candles.get(symbol)
            if candle is not None:
                self._close(symbol, candle, candle.close, ExitReason.END_OF_DATA)

    def prime(self, symbol: str, candles: list[Candle]) -> None:
        """Заполнить окно истории без исполнения сигналов (прогрев перед live)."""
        window: deque[Candle] = deque(maxlen=self._window_len)
        for candle in sorted(candles, key=lambda c: c.ts):
            window.append(candle)
        self._candles[symbol] = window
        self._idx[symbol] = len(candles)

    # --- внутреннее ---

    def _try_armed(self, symbol: str, candle: Candle, idx: int) -> None:
        params = self.params
        arm = self._armed[symbol]
        arm.peak = max(arm.peak, candle.high)
        trigger = arm.peak * (1.0 - params.entry_pullback_pct / 100.0)

        if candle.low <= trigger:
            if len(self._positions) >= self.max_concurrent:
                return  # слот занят — остаёмся взведёнными
            del self._armed[symbol]
            self._open(symbol, candle, trigger)
            return

        if idx - arm.start_idx >= params.entry_timeout_candles:
            del self._armed[symbol]

    def _open(self, symbol: str, candle: Candle, entry_price: float | None = None) -> None:
        params = self.params
        raw = entry_price if entry_price is not None else candle.close
        maker = params.maker_entries
        slip = 0.0 if maker else params.slippage_pct
        fee_rate = params.maker_fee_pct if maker else params.fee_pct
        fill = raw * (1.0 - slip / 100.0)  # шорт: продаём дешевле
        if params.use_stop_loss:
            qty_total = total_qty_for_risk(self.equity, fill, params)
        else:
            qty_total = total_qty_for_notional(self.equity, fill, params)
        sizes = entry_sizes(qty_total, params)
        if qty_total <= 0 or not sizes:
            return

        position = Position(symbol=symbol, params=params, side=Side.SHORT)
        position.add(candle.ts, fill, sizes[0])
        fee = sizes[0] * fill * fee_rate / 100.0
        self._positions[symbol] = _OpenState(
            position=position,
            planned_sizes=sizes,
            fees=fee,
            next_size_idx=1,
            opened_ts=candle.ts,
            max_adverse_pct=_adverse_pct(candle.high, fill),
        )

    def _process_open(self, symbol: str, candle: Candle) -> None:
        params = self.params
        open_state = self._positions[symbol]
        adverse = _adverse_pct(candle.high, open_state.position.avg_entry)
        open_state.max_adverse_pct = max(open_state.max_adverse_pct, adverse)
        action = decide(open_state.position, candle, params)

        if action is Decision.EXIT_LIQUIDATION:
            self._close(symbol, candle, open_state.position.liquidation_price, ExitReason.LIQUIDATION)
        elif action is Decision.EXIT_STOP_LOSS:
            self._close(symbol, candle, open_state.position.stop_price, ExitReason.STOP_LOSS)
        elif action is Decision.EXIT_TAKE_PROFIT:
            self._close(
                symbol, candle, open_state.position.take_profit_price, ExitReason.TAKE_PROFIT
            )
        elif action is Decision.AVERAGE and open_state.next_size_idx < len(open_state.planned_sizes):
            price = open_state.position.next_average_price
            maker = params.maker_entries
            slip = 0.0 if maker else params.slippage_pct
            fee_rate = params.maker_fee_pct if maker else params.fee_pct
            fill = price * (1.0 - slip / 100.0)
            size = open_state.planned_sizes[open_state.next_size_idx]
            open_state.position.add(candle.ts, fill, size)
            open_state.fees += size * fill * fee_rate / 100.0
            open_state.next_size_idx += 1

    def _close(
        self,
        symbol: str,
        candle: Candle,
        raw_exit_price: float,
        reason: ExitReason,
    ) -> None:
        params = self.params
        open_state = self._positions.pop(symbol)
        position = open_state.position

        # Шорт закрываем покупкой. Тейк — лимитка (maker), стоп/ликвидация — маркет.
        maker_exit = params.maker_entries and reason is ExitReason.TAKE_PROFIT
        slip = 0.0 if maker_exit else params.slippage_pct
        fee_rate = params.maker_fee_pct if maker_exit else params.fee_pct
        fill = raw_exit_price * (1.0 + slip / 100.0)
        qty = position.qty
        gross = (position.avg_entry - fill) * qty
        exit_fee = qty * fill * fee_rate / 100.0
        fees = open_state.fees + exit_fee
        net = gross - fees + open_state.funding
        notional = position.avg_entry * qty
        return_pct = (net / notional * 100.0) if notional > 0 else 0.0

        self.trades.append(
            ClosedTrade(
                symbol=symbol,
                side=position.side,
                entries=len(position.entries),
                opened_ts=open_state.opened_ts,
                closed_ts=candle.ts,
                avg_entry=round(position.avg_entry, 8),
                exit_price=round(fill, 8),
                qty=qty,
                gross_pnl=round(gross, 6),
                fees=round(fees, 6),
                net_pnl=round(net, 6),
                return_pct=round(return_pct, 4),
                exit_reason=reason,
                max_adverse_pct=round(open_state.max_adverse_pct, 4),
                funding=round(open_state.funding, 6),
            )
        )
        self.equity += net
        self.equity_curve.append((candle.ts, self.equity))