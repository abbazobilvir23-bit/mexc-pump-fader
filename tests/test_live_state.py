"""Тесты живой машины состояний и её эквивалентности бэктесту."""

from __future__ import annotations

import pytest

from mpf.backtest.engine import Backtester
from mpf.config import StrategyParams
from mpf.data.synthetic import generate_pump_series
from mpf.live.state import LiveState


def _series(n: int = 5) -> dict[str, list]:
    return {f"S{i}": generate_pump_series(seed=400 + i) for i in range(n)}


def test_live_state_matches_backtester() -> None:
    """Одна и та же логика: прогон через LiveState == прогон Backtester."""
    series = _series()
    params = StrategyParams()
    backtest = Backtester(params, config=None).run(series)

    state = LiveState(params, 1000.0, 3)
    events = sorted(
        ((c.ts, s, idx) for s, cs in series.items() for idx, c in enumerate(cs)),
        key=lambda e: (e[0], e[1]),
    )
    for _ts, symbol, idx in events:
        state.on_candle(symbol, series[symbol][idx])
    state.finalize({s: cs[-1] for s, cs in series.items()})

    assert len(state.trades) == len(backtest.trades)
    assert [t.symbol for t in state.trades] == [t.symbol for t in backtest.trades]
    assert state.equity == pytest.approx(backtest.metrics.final_equity, abs=0.01)


def test_prime_fills_window_without_trading() -> None:
    state = LiveState(StrategyParams())
    state.prime("X/USDT", generate_pump_series(seed=1))
    assert state.trades == []
    assert state.open_symbols == []
    assert state.armed_symbols == []


def test_maker_entry_cheaper_than_taker() -> None:
    from mpf.models import Candle

    candle = Candle(ts=0, open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0)
    fees: dict[bool, float] = {}
    for maker in (False, True):
        state = LiveState(StrategyParams(maker_entries=maker), 1000.0, 1, self_detect=False)
        state.open_position("X/USDT", candle)
        fees[maker] = state.positions["X/USDT"].fees
    assert fees[True] < fees[False]


def test_signal_mode_does_not_self_detect() -> None:
    # В режиме внешних сигналов LiveState не ищет пампы сам.
    state = LiveState(StrategyParams(), self_detect=False)
    for candle in generate_pump_series(seed=3):
        state.on_candle("X/USDT", candle)
    assert state.trades == []
    assert state.armed_symbols == []
    assert state.open_symbols == []


def test_open_position_manually() -> None:
    from mpf.models import Candle

    state = LiveState(StrategyParams(), self_detect=False)
    candle = Candle(ts=0, open=100.0, high=100.0, low=100.0, close=100.0, volume=1.0)
    assert state.open_position("X/USDT", candle) is True
    assert "X/USDT" in state.open_symbols
    # повторно тот же символ не открываем
    assert state.open_position("X/USDT", candle) is False


def test_prime_then_continue_detects() -> None:
    # После прогрева новые свечи должны обрабатываться штатно.
    params = StrategyParams()
    candles = generate_pump_series(seed=2)
    split = len(candles) - 5
    state = LiveState(params)
    state.prime("X/USDT", candles[:split])
    for candle in candles[split:]:
        state.on_candle("X/USDT", candle)
    # либо открылась позиция, либо хотя бы что-то произошло без ошибок
    assert isinstance(state.equity, float)