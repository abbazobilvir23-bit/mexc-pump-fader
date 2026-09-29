"""Тесты машины состояний позиции."""

from __future__ import annotations

import pytest

from mpf.config import StrategyParams
from mpf.core.strategy import Decision, Position, decide
from mpf.models import Candle, Side

PARAMS = StrategyParams()


def _position(entry_price: float = 100.0, qty: float = 1.0) -> Position:
    pos = Position(symbol="X/USDT", params=PARAMS, side=Side.SHORT)
    pos.add(ts=0, price=entry_price, qty=qty)
    return pos


def _candle(high: float, low: float, close: float | None = None) -> Candle:
    return Candle(ts=1, open=close or low, high=high, low=low, close=close or low, volume=1.0)


def test_aggregates() -> None:
    pos = _position(100.0, 1.0)
    pos.add(ts=1, price=110.0, qty=1.0)
    assert pos.qty == 2.0
    assert pos.avg_entry == pytest.approx(105.0)
    assert pos.first_entry_price == 100.0
    assert pos.last_entry_price == 110.0


def test_stop_and_take_prices() -> None:
    pos = _position(100.0, 1.0)
    assert pos.stop_price == pytest.approx(120.0)          # 100 * 1.20
    assert pos.take_profit_price == pytest.approx(98.5)    # 100 * (1 - 0.015)
    assert pos.next_average_price == pytest.approx(105.0)  # 100 * 1.05


def test_decision_stop_loss() -> None:
    pos = _position(100.0, 1.0)
    assert decide(pos, _candle(high=121.0, low=119.0), PARAMS) is Decision.EXIT_STOP_LOSS


def test_decision_take_profit() -> None:
    pos = _position(100.0, 1.0)
    assert decide(pos, _candle(high=100.5, low=98.0), PARAMS) is Decision.EXIT_TAKE_PROFIT


def test_decision_average() -> None:
    pos = _position(100.0, 1.0)
    # high дотянулся до уровня усреднения 105, но не до стопа 120 и не ниже TP
    assert decide(pos, _candle(high=106.0, low=99.0), PARAMS) is Decision.AVERAGE


def test_decision_hold() -> None:
    pos = _position(100.0, 1.0)
    assert decide(pos, _candle(high=104.0, low=99.0), PARAMS) is Decision.HOLD


def test_no_average_after_limit() -> None:
    pos = _position(100.0, 1.0)
    pos.add(ts=1, price=105.0, qty=1.0)
    pos.add(ts=2, price=110.0, qty=1.0)
    assert not pos.can_average
    # на уровне 115 уже не усредняемся, а просто держим
    assert decide(pos, _candle(high=115.5, low=108.0), PARAMS) is Decision.HOLD


def test_stop_wins_over_take_on_ambiguous_candle() -> None:
    pos = _position(100.0, 1.0)
    # и high выше стопа, и low ниже тейка — консервативно считаем стоп
    assert decide(pos, _candle(high=125.0, low=95.0), PARAMS) is Decision.EXIT_STOP_LOSS


def test_unrealized_pnl_short() -> None:
    pos = _position(100.0, 2.0)
    assert pos.unrealized_pnl(90.0) == pytest.approx(20.0)
    assert pos.unrealized_pnl(110.0) == pytest.approx(-20.0)
    assert pos.unrealized_pct(98.0) == pytest.approx(2.0)


def _leveraged_position() -> Position:
    params = StrategyParams(
        leverage=10.0, use_stop_loss=True, stop_loss_pct=6.0,
        avg_step_pct=3.0, max_averages=1,
    )
    pos = Position(symbol="X/USDT", params=params, side=Side.SHORT)
    pos.add(ts=0, price=100.0, qty=1.0)
    return pos


def test_liquidation_price_at_10x() -> None:
    pos = _leveraged_position()
    # 1/10 - 0.5% = 9.5%
    assert pos.liquidation_price == pytest.approx(109.5)


def test_stop_takes_priority_over_liquidation() -> None:
    pos = _leveraged_position()
    # high 110 выше обоих уровней, но ближайший — стоп (106) -> стоп
    assert decide(pos, _candle(high=110.0, low=108.0), pos.params) is Decision.EXIT_STOP_LOSS


def test_stop_triggers_before_liquidation() -> None:
    pos = _leveraged_position()
    # high 107: выше стопа 106, но ниже ликвидации 109.5 -> стоп
    assert decide(pos, _candle(high=107.0, low=100.0), pos.params) is Decision.EXIT_STOP_LOSS


def test_liquidation_without_stop() -> None:
    params = StrategyParams(
        leverage=10.0, use_stop_loss=False, stop_loss_pct=20.0,
        avg_step_pct=3.0, max_averages=1,
    )
    pos = Position(symbol="X/USDT", params=params, side=Side.SHORT)
    pos.add(ts=0, price=100.0, qty=1.0)
    assert decide(pos, _candle(high=110.0, low=100.0), params) is Decision.EXIT_LIQUIDATION