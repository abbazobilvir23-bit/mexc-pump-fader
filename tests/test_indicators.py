"""Тесты индикаторов."""

from __future__ import annotations

import pytest

from mpf.core.indicators import (
    adx,
    atr,
    atr_pct,
    ema,
    median,
    pct_change,
    rsi,
    sma,
    volume_ratio,
)
from mpf.models import Candle


def test_pct_change_basic() -> None:
    assert pct_change([100.0, 110.0], 1) == pytest.approx(10.0)
    assert pct_change([100.0, 50.0], 1) == pytest.approx(-50.0)


def test_pct_change_insufficient_data() -> None:
    assert pct_change([100.0], 1) == 0.0
    assert pct_change([], 5) == 0.0


def test_median() -> None:
    assert median([3.0, 1.0, 2.0]) == 2.0
    assert median([1.0, 2.0, 3.0, 4.0]) == 2.5
    assert median([]) == 0.0


def test_sma_and_ema() -> None:
    assert sma([1.0, 2.0, 3.0], 3) == pytest.approx(2.0)
    assert ema([1.0, 2.0, 3.0], 3) == pytest.approx(2.0)
    # На нелинейном ряду EMA реагирует на последний скачок сильнее SMA.
    jumpy = [1.0, 1.0, 1.0, 1.0, 10.0]
    assert ema(jumpy, 3) > sma(jumpy, 3)


def test_rsi_extremes() -> None:
    rising = [float(i) for i in range(1, 30)]
    assert rsi(rising, 14) == pytest.approx(100.0)
    falling = [float(i) for i in range(30, 1, -1)]
    assert rsi(falling, 14) == pytest.approx(0.0, abs=1e-6)


def test_rsi_neutral_on_short_series() -> None:
    assert rsi([1.0, 2.0], 14) == 50.0


def test_volume_ratio() -> None:
    volumes = [100.0] * 10 + [1000.0]
    assert volume_ratio(volumes, 10) == pytest.approx(10.0)
    assert volume_ratio([0.0, 0.0], 1) == 0.0


def _trend_candles(n: int, step: float = 1.0) -> list[Candle]:
    out: list[Candle] = []
    price = 100.0
    for i in range(n):
        close = price + step
        out.append(
            Candle(ts=i * 60_000, open=price, high=close + 0.5, low=price - 0.5, close=close, volume=1000.0)
        )
        price = close
    return out


def _flat_candles(n: int) -> list[Candle]:
    return [
        Candle(ts=i * 60_000, open=100.0, high=100.0, low=100.0, close=100.0, volume=1000.0)
        for i in range(n)
    ]


def test_atr_pct() -> None:
    assert atr_pct(_flat_candles(40), 14) == 0.0
    assert atr_pct(_trend_candles(40), 14) > 0.0


def test_adx_flat_is_zero() -> None:
    assert adx(_flat_candles(60), 14) == 0.0


def test_adx_strong_trend_is_high() -> None:
    assert adx(_trend_candles(60), 14) > 25.0


def test_adx_insufficient_data() -> None:
    assert adx(_trend_candles(10), 14) == 0.0


def test_atr_basic() -> None:
    assert atr(_flat_candles(30), 14) == 0.0
    assert atr(_trend_candles(30), 14) > 0.0