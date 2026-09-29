"""Машина состояний позиции: вход, усреднение, тейк, стоп.

Вся логика — чистые функции над свечами. Никакого ввода-вывода.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from mpf.config import StrategyParams
from mpf.models import Candle, Side


class Decision(StrEnum):
    """Что делать с позицией на текущей свече."""

    HOLD = "hold"
    AVERAGE = "average"
    EXIT_TAKE_PROFIT = "exit_take_profit"
    EXIT_STOP_LOSS = "exit_stop_loss"
    EXIT_LIQUIDATION = "exit_liquidation"


@dataclass(slots=True)
class Entry:
    """Один вход (базовый или усредняющий)."""

    ts: int
    price: float
    qty: float


@dataclass(slots=True)
class Position:
    """Открытая короткая позиция с возможностью усреднения."""

    symbol: str
    params: StrategyParams
    side: Side = Side.SHORT
    entries: list[Entry] = field(default_factory=list)

    # --- агрегаты ---
    @property
    def qty(self) -> float:
        return sum(e.qty for e in self.entries)

    @property
    def avg_entry(self) -> float:
        total = self.qty
        if total <= 0:
            return 0.0
        return sum(e.price * e.qty for e in self.entries) / total

    @property
    def first_entry_price(self) -> float:
        return self.entries[0].price

    @property
    def last_entry_price(self) -> float:
        return self.entries[-1].price

    @property
    def can_average(self) -> bool:
        return len(self.entries) < 1 + self.params.max_averages

    # --- цены-триггеры ---
    @property
    def stop_price(self) -> float:
        """Стоп считается от ПЕРВОГО входа: усреднение не должно приближать стоп."""
        return self.first_entry_price * (1.0 + self.params.stop_loss_pct / 100.0)

    @property
    def take_profit_price(self) -> float:
        """Тейк — от средней цены (после усреднений она смещается)."""
        return self.avg_entry * (1.0 - self.params.take_profit_pct / 100.0)

    @property
    def next_average_price(self) -> float:
        return self.last_entry_price * (1.0 + self.params.avg_step_pct / 100.0)

    @property
    def liquidation_price(self) -> float:
        """Цена принудительной ликвидации для шорта при текущем плече.

        Изолированная маржа: запас ≈ 1/leverage − поддерживающая маржа.
        При leverage <= 1 ликвидации нет (возвращаем бесконечность).
        """
        if self.params.leverage <= 1.0:
            return float("inf")
        distance = 1.0 / self.params.leverage - self.params.maintenance_margin_pct / 100.0
        return self.avg_entry * (1.0 + max(distance, 0.0))

    # --- PnL ---
    def unrealized_pnl(self, price: float) -> float:
        """Нереализованный PnL в котируемой валюте (для шорта)."""
        return (self.avg_entry - price) * self.qty

    def unrealized_pct(self, price: float) -> float:
        if self.avg_entry <= 0:
            return 0.0
        return (self.avg_entry - price) / self.avg_entry * 100.0

    def add(self, ts: int, price: float, qty: float) -> None:
        if qty <= 0:
            raise ValueError("qty должна быть > 0")
        self.entries.append(Entry(ts=ts, price=price, qty=qty))


def decide(position: Position, candle: Candle, params: StrategyParams) -> Decision:
    """Решение по открытой позиции на текущей свече.

    Порядок проверок — консервативный (худший сценарий первым):
      1. Стоп по high (цена выросла против шорта).
      2. Тейк по low (цена упала в нашу сторону).
      3. Усреднение по high.

    Внутри одной свечи мы не знаем порядок движения, поэтому при
    неоднозначности выбираем худший исход для стопа и лучший для тейка.
    """
    liq_price = position.liquidation_price
    stop_price = position.stop_price
    has_stop = params.use_stop_loss
    has_liq = params.leverage > 1.0

    # Первым срабатывает БЛИЖАЙШИЙ уровень: для шорта стоп обычно ниже
    # ликвидации, значит стоп исполняется раньше.
    if has_stop and (not has_liq or stop_price <= liq_price) and candle.high >= stop_price:
        return Decision.EXIT_STOP_LOSS

    if has_liq and candle.high >= liq_price:
        return Decision.EXIT_LIQUIDATION

    if has_stop and candle.high >= stop_price:
        return Decision.EXIT_STOP_LOSS

    if candle.low <= position.take_profit_price:
        return Decision.EXIT_TAKE_PROFIT

    if position.can_average and candle.high >= position.next_average_price:
        return Decision.AVERAGE

    return Decision.HOLD