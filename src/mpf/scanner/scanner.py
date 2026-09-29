"""Сканер рынка: находит пампы среди низколиквидных фьючерсов."""

from __future__ import annotations

from dataclasses import dataclass, field

from mpf.config import StrategyParams
from mpf.core.pump_detector import detect_pump
from mpf.exchange.base import Exchange
from mpf.models import PumpSignal, Ticker


@dataclass(slots=True)
class ScanResult:
    signals: list[PumpSignal] = field(default_factory=list)
    fetched_tickers: int = 0
    liquid_candidates: int = 0
    analyzed: int = 0

    def top(self, n: int) -> list[PumpSignal]:
        return sorted(self.signals, key=lambda s: s.score, reverse=True)[:n]


def filter_liquid_candidates(
    tickers: list[Ticker],
    params: StrategyParams,
) -> list[Ticker]:
    """Оставить низколиквидные пары в заданном коридоре объёма за 24ч."""
    return [
        t
        for t in tickers
        if params.min_quote_volume_24h <= t.quote_volume_24h <= params.max_quote_volume_24h
    ]


def scan_market(
    exchange: Exchange,
    params: StrategyParams,
    *,
    timeframe: str = "1m",
    candles: int = 200,
    top_n: int = 60,
) -> ScanResult:
    """Просканировать рынок и вернуть сигналы, отсортированные по скору.

    `top_n` ограничивает число пар для запроса свечей — сначала берём
    кандидатов с наибольшим ростом за 24ч, чтобы не дёргать API зря.
    """
    tickers = exchange.fetch_futures_tickers()
    result = ScanResult(fetched_tickers=len(tickers))

    candidates = filter_liquid_candidates(tickers, params)
    result.liquid_candidates = len(candidates)
    # Приоритет тем, кто уже растёт за сутки.
    candidates.sort(key=lambda t: t.change_pct_24h, reverse=True)

    for ticker in candidates[:top_n]:
        series = exchange.fetch_ohlcv(ticker.symbol, timeframe=timeframe, limit=candles)
        result.analyzed += 1
        if not series:
            continue
        signal = detect_pump(ticker.symbol, series, params)
        if signal is not None:
            result.signals.append(signal)

    result.signals.sort(key=lambda s: s.score, reverse=True)
    return result