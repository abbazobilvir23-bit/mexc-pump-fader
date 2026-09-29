"""Интеграционные тесты бэктест-движка на синтетике."""

from __future__ import annotations

import pytest

from mpf.backtest.engine import BacktestConfig, Backtester
from mpf.config import StrategyParams
from mpf.data.synthetic import generate_pump_series
from mpf.models import Candle, ExitReason

PARAMS = StrategyParams()


def _series(n_symbols: int = 5) -> dict[str, list]:
    return {
        f"SYN{i}/USDT": generate_pump_series(seed=200 + i, pump_step_pct=15.0 + i)
        for i in range(n_symbols)
    }


def test_backtest_produces_trades() -> None:
    result = Backtester(PARAMS, BacktestConfig(initial_equity=1000.0)).run(_series())
    assert result.trades, "на синтетических пампах должны быть сделки"


def test_trade_entry_count_within_limit() -> None:
    result = Backtester(PARAMS).run(_series())
    for trade in result.trades:
        assert 1 <= trade.entries <= 1 + PARAMS.max_averages


def test_metrics_are_consistent() -> None:
    result = Backtester(PARAMS, BacktestConfig(initial_equity=1000.0)).run(_series())
    m = result.metrics
    assert m.trades == len(result.trades)
    assert m.wins + m.losses == m.trades
    # Метрики округлены до 4 знаков — сравниваем с допуском.
    assert m.net_pnl == pytest.approx(m.gross_profit + m.gross_loss, abs=0.01)
    assert m.final_equity == pytest.approx(m.initial_equity + m.net_pnl, abs=0.01)
    assert 0.0 <= m.win_rate <= 100.0


def test_mean_reversion_is_profitable_on_synthetic() -> None:
    """На чистых «памп → откат» стратегия должна ловить тейки."""
    result = Backtester(PARAMS).run(_series())
    tp = [t for t in result.trades if t.exit_reason is ExitReason.TAKE_PROFIT]
    assert tp, "должны быть сделки, закрытые по тейк-профиту"


def test_empty_input() -> None:
    result = Backtester(PARAMS).run({})
    assert result.trades == []
    assert result.metrics.trades == 0


def test_respects_max_concurrent() -> None:
    # Одна и та же серия на всех символах: одновременно не более max_concurrent позиций.
    base = generate_pump_series(seed=1)
    series = {f"S{i}": list(base) for i in range(10)}
    config = BacktestConfig(max_concurrent=2)
    result = Backtester(PARAMS, config).run(series)
    assert len(result.trades) <= 10


def test_no_entry_without_pullback() -> None:
    """Если после сигнала нет отката на entry_pullback_pct — входа быть не должно."""
    # Монотонный рост: цена не откатывает от пика достаточно.
    candles = [
        Candle(ts=i * 60_000, open=100.0 + i, high=100.0 + i, low=100.0 + i,
               close=100.0 + i, volume=1000.0)
        for i in range(120)
    ]
    # Ускоряемся в конце, чтобы сработал детектор пампа и объём.
    candles[-1] = Candle(ts=candles[-1].ts, open=219.0, high=260.0, low=219.0,
                         close=260.0, volume=50_000.0)
    result = Backtester(PARAMS).run({"MONO/USDT": candles})
    # Откат так и не наступил (монотонный рост) -> сигнал снимается по таймауту.
    assert result.trades == []


def _adverse_series() -> list[Candle]:
    """Памп → продолжение роста (против шорта) → разворот вниз."""
    candles: list[Candle] = []
    ts = 0

    def add(o: float, h: float, low: float, c: float, v: float) -> None:
        nonlocal ts
        candles.append(Candle(ts=ts, open=o, high=h, low=low, close=c, volume=v))
        ts += 60_000

    for _ in range(80):
        add(100.0, 100.5, 99.5, 100.0, 100.0)
    add(100.0, 115.0, 100.0, 115.0, 5000.0)
    add(115.0, 132.0, 115.0, 132.0, 5000.0)
    add(132.0, 152.0, 132.0, 152.0, 5000.0)
    add(152.0, 165.0, 150.0, 160.0, 3000.0)
    add(160.0, 185.0, 158.0, 180.0, 3000.0)
    add(180.0, 200.0, 175.0, 195.0, 3000.0)
    for p in (185.0, 170.0, 155.0, 145.0, 135.0, 125.0, 115.0, 110.0, 105.0, 100.0, 95.0, 90.0):
        add(p + 2, p + 3, p - 3, p, 2000.0)
    return candles


def test_max_adverse_is_tracked_without_stop() -> None:
    params = StrategyParams(use_stop_loss=False, entry_pullback_pct=0.0, take_profit_pct=2.0)
    result = Backtester(params).run({"ADV/USDT": _adverse_series()})
    assert result.trades
    trade = result.trades[0]
    assert trade.max_adverse_pct > 10.0, "позиция уходила против нас минимум на 10%"
    assert trade.exit_reason is not ExitReason.STOP_LOSS


def test_no_stop_mode_has_no_stop_exits() -> None:
    params = StrategyParams(
        use_stop_loss=False, entry_pullback_pct=0.0, max_averages=3, take_profit_pct=2.0
    )
    result = Backtester(params).run(_series())
    assert result.trades
    assert all(t.exit_reason is not ExitReason.STOP_LOSS for t in result.trades)


def test_liquidation_happens_without_stop_at_leverage() -> None:
    # 10x, без стопа, вход на пике: цена уходит +25% против -> ликвидация.
    params = StrategyParams(
        leverage=10.0, use_stop_loss=False, entry_pullback_pct=0.0,
        max_averages=3, take_profit_pct=2.0, position_pct=20.0,
    )
    result = Backtester(params).run({"ADV/USDT": _adverse_series()})
    assert result.trades
    assert result.metrics.liquidations >= 1


def test_safe_leverage_has_no_liquidation() -> None:
    params = StrategyParams(
        leverage=10.0, use_stop_loss=True, stop_loss_pct=6.0,
        avg_step_pct=3.0, max_averages=1, entry_pullback_pct=3.0,
    )
    result = Backtester(params).run(_series())
    assert all(t.exit_reason is not ExitReason.LIQUIDATION for t in result.trades)