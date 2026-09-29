"""Абстракция биржи: ядро и сканер не зависят от конкретной реализации."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from mpf.models import Candle, Ticker


@runtime_checkable
class Exchange(Protocol):
    """Минимальный интерфейс биржи, нужный стратегии."""

    def fetch_futures_tickers(self) -> list[Ticker]:
        """Снимки всех фьючерсных пар (для фильтра ликвидности)."""
        ...

    def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1m", limit: int = 200
    ) -> list[Candle]:
        """Исторические свечи по паре (по возрастанию времени)."""
        ...


@runtime_checkable
class HistoryExchange(Exchange, Protocol):
    """Биржа с возможностью постранично качать историю назад во времени."""

    def fetch_ohlcv_since(
        self, symbol: str, timeframe: str, since: int, limit: int = 1000
    ) -> list[Candle]:
        """Свечи, начиная с `since` (ms), по возрастанию времени."""
        ...

    def server_time_ms(self) -> int:
        """Текущее время сервера биржи в миллисекундах."""
        ...