"""Доменные модели. Чистые данные, без зависимостей от биржи."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Side(StrEnum):
    """Направление позиции."""

    LONG = "long"
    SHORT = "short"


@dataclass(frozen=True, slots=True)
class Candle:
    """OHLCV-свеча. ts — время открытия в миллисекундах (UTC)."""

    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class Ticker:
    """Снимок состояния пары для фильтра ликвидности."""

    symbol: str
    last: float
    quote_volume_24h: float
    change_pct_24h: float
    bid: float = 0.0
    ask: float = 0.0
    open_interest: float = 0.0   # holdVol: открытый интерес в контрактах
    funding_rate: float = 0.0    # текущая ставка финансирования


@dataclass(frozen=True, slots=True)
class PumpSignal:
    """Обнаруженный памп: кандидат на вход в шорт."""

    symbol: str
    ts: int
    pump_pct: float
    volume_mult: float
    rsi: float
    score: float
    atr_pct: float = 0.0
    adx: float = 0.0


class ExitReason(StrEnum):
    """Причина закрытия позиции."""

    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    LIQUIDATION = "liquidation"
    END_OF_DATA = "end_of_data"


@dataclass(frozen=True, slots=True)
class ClosedTrade:
    """Завершённая сделка (результат симуляции)."""

    symbol: str
    side: Side
    entries: int
    opened_ts: int
    closed_ts: int
    avg_entry: float
    exit_price: float
    qty: float
    gross_pnl: float
    fees: float
    net_pnl: float
    return_pct: float
    exit_reason: ExitReason
    max_adverse_pct: float = 0.0
    funding: float = 0.0