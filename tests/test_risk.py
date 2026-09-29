"""Тесты риск-менеджмента."""

from __future__ import annotations

import pytest

from mpf.config import StrategyParams
from mpf.core.risk import entry_sizes, total_qty_for_risk


def test_total_qty_matches_risk_budget() -> None:
    params = StrategyParams(risk_per_trade_pct=1.0, stop_loss_pct=20.0)
    qty = total_qty_for_risk(equity=1000.0, entry_price=100.0, params=params)
    # риск 10 USDT, убыток на единицу = 100 * 0.20 = 20 -> qty = 0.5
    assert qty == pytest.approx(0.5)


def test_total_qty_zero_on_bad_input() -> None:
    params = StrategyParams()
    assert total_qty_for_risk(0.0, 100.0, params) == 0.0
    assert total_qty_for_risk(1000.0, 0.0, params) == 0.0


def test_entry_sizes_equal_split() -> None:
    params = StrategyParams(max_averages=2, avg_size_mult=1.0)
    sizes = entry_sizes(0.9, params)
    assert len(sizes) == 3
    assert sum(sizes) == pytest.approx(0.9)
    assert all(s == pytest.approx(0.3) for s in sizes)


def test_entry_sizes_geometric_growth() -> None:
    params = StrategyParams(max_averages=2, avg_size_mult=2.0)
    sizes = entry_sizes(1.0, params)
    # веса 1, 2, 4 -> доли 1/7, 2/7, 4/7
    assert sizes[0] == pytest.approx(1 / 7)
    assert sizes[1] == pytest.approx(2 / 7)
    assert sizes[2] == pytest.approx(4 / 7)
    assert sum(sizes) == pytest.approx(1.0)


def test_entry_sizes_no_averaging() -> None:
    params = StrategyParams(max_averages=0)
    assert entry_sizes(0.5, params) == [pytest.approx(0.5)]