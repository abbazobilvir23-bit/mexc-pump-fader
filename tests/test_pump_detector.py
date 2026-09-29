"""Тесты детектора пампов."""

from __future__ import annotations

from mpf.config import StrategyParams
from mpf.core.pump_detector import detect_pump
from mpf.data.synthetic import generate_pump_series
from mpf.models import Candle

PARAMS = StrategyParams()


def _flat_series(n: int, price: float = 100.0, volume: float = 100.0) -> list[Candle]:
    return [
        Candle(ts=i * 60_000, open=price, high=price, low=price, close=price, volume=volume)
        for i in range(n)
    ]


def test_no_signal_on_flat_market() -> None:
    assert detect_pump("FLAT/USDT", _flat_series(200), PARAMS) is None


def test_no_signal_without_volume_spike() -> None:
    candles = _flat_series(200)
    # резкий рост цены, но объём не вырос
    rising = [
        Candle(ts=c.ts, open=c.open, high=c.close, low=c.open, close=c.close * (1 + 0.02 * i), volume=100.0)
        for i, c in enumerate(candles)
    ]
    assert detect_pump("NOVOL/USDT", rising, PARAMS) is None


def test_signal_on_synthetic_pump() -> None:
    candles = generate_pump_series(seed=7)
    signals = [
        detect_pump("SYN/USDT", candles[: k + 1], PARAMS) for k in range(len(candles))
    ]
    found = [s for s in signals if s is not None]
    assert found, "на синтетическом пампе сигнал должен находиться"
    first = found[0]
    assert first.pump_pct >= PARAMS.pump_threshold_pct
    assert first.rsi >= PARAMS.rsi_overbought
    assert 0.0 <= first.score <= 100.0
    assert first.atr_pct >= 0.0
    assert first.adx >= 0.0


def test_atr_filter_blocks_signal() -> None:
    candles = generate_pump_series(seed=7)
    assert detect_pump("SYN/USDT", candles, StrategyParams(min_atr_pct=10_000.0)) is None


def test_adx_filter_blocks_signal() -> None:
    candles = generate_pump_series(seed=7)
    assert detect_pump("SYN/USDT", candles, StrategyParams(min_adx=99.0)) is None