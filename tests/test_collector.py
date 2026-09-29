"""Тесты сбора истории и отбора пар."""

from __future__ import annotations

import pytest

from mpf.config import StrategyParams
from mpf.data.collector import collect_history, select_low_liquidity, timeframe_to_ms
from mpf.data.store import merge_candles
from mpf.models import Candle, Ticker


def test_timeframe_to_ms() -> None:
    assert timeframe_to_ms("1m") == 60_000
    assert timeframe_to_ms("5m") == 300_000
    assert timeframe_to_ms("1h") == 3_600_000
    assert timeframe_to_ms("1d") == 86_400_000
    with pytest.raises(ValueError):
        timeframe_to_ms("1x")


def test_select_low_liquidity_filters_and_sorts() -> None:
    params = StrategyParams(min_quote_volume_24h=100.0, max_quote_volume_24h=1000.0)
    tickers = [
        Ticker("A", 1.0, 50.0, 5.0),     # слишком мёртвая
        Ticker("B", 1.0, 500.0, 10.0),   # ок
        Ticker("C", 1.0, 900.0, 40.0),   # ок, сильнее растёт
        Ticker("D", 1.0, 5000.0, 90.0),  # слишком ликвидная
    ]
    selected = select_low_liquidity(tickers, params, top_n=10, sort_by="change")
    assert [t.symbol for t in selected] == ["C", "B"]

    by_volume = select_low_liquidity(tickers, params, top_n=1, sort_by="volume")
    assert [t.symbol for t in by_volume] == ["C"]


class _FakeExchange:
    """Отдаёт по одной свече в минуту от `since` до now."""

    def __init__(self, now: int, step: int = 60_000) -> None:
        self._now = now
        self._step = step

    def server_time_ms(self) -> int:
        return self._now

    def fetch_ohlcv_since(
        self, symbol: str, timeframe: str, since: int, limit: int = 1000
    ) -> list[Candle]:
        available = max(0, (self._now - since) // self._step)
        count = min(limit, available)
        return [
            Candle(ts=since + i * self._step, open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0)
            for i in range(count)
        ]


def test_collect_history_paginates_and_dedups() -> None:
    now = 1_700_000_000_000
    exchange = _FakeExchange(now)
    # 5 минут истории
    result = collect_history(exchange, ["X/USDT"], days=5 * 60_000 / 86_400_000)
    candles = result.series["X/USDT"]
    assert len(candles) == 5
    assert candles == sorted(candles, key=lambda c: c.ts)
    assert len({c.ts for c in candles}) == len(candles)


def test_collect_history_records_failures() -> None:
    class _Broken(_FakeExchange):
        def fetch_ohlcv_since(self, symbol: str, timeframe: str, since: int, limit: int = 1000) -> list[Candle]:
            raise RuntimeError("network down")

    result = collect_history(_Broken(1_700_000_000_000), ["X/USDT"], days=1.0)
    assert "X/USDT" in result.failures
    assert result.series == {}


def test_merge_candles_dedups_by_ts() -> None:
    def candle(ts: int) -> Candle:
        return Candle(ts=ts, open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0)

    old = {"A": [candle(1), candle(2)], "B": [candle(1)]}
    new = {"A": [candle(2), candle(3)], "C": [candle(5)]}
    merged = merge_candles(old, new)
    assert [c.ts for c in merged["A"]] == [1, 2, 3]
    assert [c.ts for c in merged["B"]] == [1]
    assert [c.ts for c in merged["C"]] == [5]