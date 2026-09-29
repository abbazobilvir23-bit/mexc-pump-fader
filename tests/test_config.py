"""Тесты параметров стратегии."""

from __future__ import annotations

import pytest

from mpf.config import StrategyParams


def test_defaults_are_valid() -> None:
    StrategyParams().validate()


def test_stop_must_exceed_last_average_level() -> None:
    with pytest.raises(ValueError, match="stop_loss_pct"):
        StrategyParams(stop_loss_pct=10.0, avg_step_pct=5.0, max_averages=2).validate()


def test_from_dict_rejects_unknown_keys() -> None:
    with pytest.raises(ValueError, match="неизвестные"):
        StrategyParams.from_dict({"pump_lookback": 10, "bogus": 1})


def test_from_dict_roundtrip() -> None:
    original = StrategyParams()
    restored = StrategyParams.from_dict(original.to_dict())
    assert restored == original


def test_negative_max_averages_rejected() -> None:
    with pytest.raises(ValueError, match="max_averages"):
        StrategyParams(max_averages=-1).validate()


def test_no_stop_mode_allows_tight_stop_value() -> None:
    # В режиме без стопа проверка «стоп > уровень усреднений» не применяется.
    StrategyParams(
        use_stop_loss=False, stop_loss_pct=5.0, avg_step_pct=5.0, max_averages=3
    ).validate()


def test_leverage_requires_stop_below_liquidation() -> None:
    # при 10x ликвидация ~9.5%, стоп 20% недопустим
    with pytest.raises(ValueError, match="ликвидац"):
        StrategyParams(leverage=10.0, use_stop_loss=True, stop_loss_pct=20.0).validate()


def test_leverage_below_one_rejected() -> None:
    with pytest.raises(ValueError, match="leverage"):
        StrategyParams(leverage=0.5).validate()


def test_safe_leverage_config_valid() -> None:
    StrategyParams(
        leverage=10.0, use_stop_loss=True, stop_loss_pct=6.0,
        avg_step_pct=3.0, max_averages=1,
    ).validate()