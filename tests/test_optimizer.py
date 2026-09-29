"""Тесты оптимизатора."""

from __future__ import annotations

import pytest

from mpf.backtest.metrics import Metrics
from mpf.config import StrategyParams
from mpf.data.synthetic import generate_pump_series
from mpf.models import Candle
from mpf.optimize.optimizer import (
    grid_search,
    score_metrics,
    time_split,
    walk_forward,
)


def _series(n: int = 6) -> dict[str, list[Candle]]:
    return {f"S{i}": generate_pump_series(seed=300 + i) for i in range(n)}


def test_time_split_by_time() -> None:
    series = {"A": [Candle(ts=t, open=1, high=1, low=1, close=1, volume=1) for t in range(100)]}
    train, test, cutoff = time_split(series, 0.7)
    assert cutoff == 70
    assert [c.ts for c in train["A"]] == list(range(70))
    assert [c.ts for c in test["A"]] == list(range(70, 100))


def test_time_split_rejects_bad_ratio() -> None:
    with pytest.raises(ValueError):
        time_split({}, 1.5)


def test_score_metrics_handles_infinity() -> None:
    m = Metrics(
        trades=1, wins=1, losses=0, win_rate=100.0, gross_profit=1.0, gross_loss=0.0,
        net_pnl=1.0, total_fees=0.0, profit_factor=float("inf"), expectancy=1.0,
        avg_win=1.0, avg_loss=0.0, avg_entries=1.0, worst_adverse_pct=0.0, avg_adverse_pct=0.0,
        max_drawdown=0.0, max_drawdown_pct=0.0,
        initial_equity=1000.0, final_equity=1001.0, return_pct=0.1,
    )
    assert score_metrics(m, "profit_factor") == 1e12


def test_grid_search_returns_ranked_results() -> None:
    base = StrategyParams()
    grid = {"take_profit_pct": [1.0, 1.5], "entry_pullback_pct": [2.0, 3.0]}
    results = grid_search(base, grid, _series(), min_trades=1, objective="expectancy", top=10)
    assert results
    scores = [r.metrics.expectancy for r in results]
    assert scores == sorted(scores, reverse=True)


def test_grid_search_min_trades_filter() -> None:
    base = StrategyParams()
    grid = {"take_profit_pct": [1.5]}
    results = grid_search(base, grid, _series(), min_trades=10_000)
    assert results == []


def test_grid_search_skips_invalid_combos() -> None:
    # stop_loss_pct=8 при avg_step=5 и max_averages=2 невалиден (нужно >10)
    base = StrategyParams()
    grid = {"stop_loss_pct": [8.0, 20.0]}
    results = grid_search(base, grid, _series(), min_trades=1)
    assert all(r.params.stop_loss_pct == 20.0 for r in results)


def test_walk_forward_returns_train_and_test() -> None:
    base = StrategyParams()
    grid = {"take_profit_pct": [1.0, 1.5, 2.5]}
    pairs = walk_forward(base, grid, _series(), train_ratio=0.7, min_trades=1, top=3)
    assert pairs
    for train_res, test_m in pairs:
        assert train_res.metrics.trades >= 1
        assert test_m.trades >= 0


def test_grid_search_empty_series() -> None:
    assert grid_search(StrategyParams(), {"take_profit_pct": [1.5]}, {}) == []