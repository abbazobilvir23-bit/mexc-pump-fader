"""Оптимизация параметров стратегии: grid search и walk-forward.

Ядро оптимизации — чистое: принимает уже загруженные свечи и возвращает
ранжированные наборы параметров. Сеть и БД не нужны.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Any

from mpf.backtest.engine import BacktestConfig, Backtester
from mpf.backtest.metrics import Metrics
from mpf.config import StrategyParams
from mpf.models import Candle

# Разумная стартовая сетка. Расширяй через свой JSON.
DEFAULT_GRID: dict[str, list[Any]] = {
    "pump_threshold_pct": [10.0, 15.0, 20.0],
    "entry_pullback_pct": [2.0, 3.0, 5.0],
    "take_profit_pct": [1.0, 1.5, 2.5, 4.0],
    "stop_loss_pct": [12.0, 20.0, 30.0],
}

_OBJECTIVES = ("expectancy", "net_pnl", "return_pct", "profit_factor", "win_rate")


@dataclass(slots=True)
class OptimizeResult:
    params: StrategyParams
    metrics: Metrics


def time_split(
    series: dict[str, list[Candle]],
    train_ratio: float = 0.7,
) -> tuple[dict[str, list[Candle]], dict[str, list[Candle]], int]:
    """Разбить данные по времени на train/test (walk-forward, без заглядывания).

    Возвращает (train, test, cutoff_ts). Свечи с ts < cutoff идут в train.
    """
    if not 0.0 < train_ratio < 1.0:
        raise ValueError("train_ratio должен быть в (0, 1)")
    all_ts = sorted({c.ts for candles in series.values() for c in candles})
    if not all_ts:
        return {}, {}, 0
    cutoff = all_ts[min(int(len(all_ts) * train_ratio), len(all_ts) - 1)]
    train = {s: [c for c in cs if c.ts < cutoff] for s, cs in series.items()}
    test = {s: [c for c in cs if c.ts >= cutoff] for s, cs in series.items()}
    return (
        {s: cs for s, cs in train.items() if cs},
        {s: cs for s, cs in test.items() if cs},
        cutoff,
    )


def score_metrics(metrics: Metrics, objective: str) -> float:
    if objective not in _OBJECTIVES:
        raise ValueError(f"objective должен быть из {_OBJECTIVES}")
    value = float(getattr(metrics, objective))
    if value == float("inf"):
        return 1e12
    if value == float("-inf"):
        return -1e12
    return value


def grid_search(
    base: StrategyParams,
    grid: dict[str, list[Any]],
    series: dict[str, list[Candle]],
    *,
    config: BacktestConfig | None = None,
    min_trades: int = 10,
    objective: str = "expectancy",
    top: int = 20,
) -> list[OptimizeResult]:
    """Перебрать сетку параметров и вернуть top-N по `objective`.

    Комбинации, не прошедшие валидацию или давшие < `min_trades` сделок,
    отбрасываются — иначе оптимизатор любит «красивые» 2-сделочные выбросы.
    """
    if not series:
        return []
    keys = list(grid)
    results: list[OptimizeResult] = []
    base_dict = base.to_dict()

    for values in product(*(grid[k] for k in keys)):
        overrides = dict(zip(keys, values, strict=True))
        try:
            params = StrategyParams(**{**base_dict, **overrides})
            params.validate()
        except ValueError:
            continue
        run = Backtester(params, config).run(series)
        metrics = run.metrics
        if metrics.trades < min_trades:
            continue
        results.append(OptimizeResult(params=params, metrics=metrics))

    results.sort(key=lambda r: score_metrics(r.metrics, objective), reverse=True)
    return results[:top]


def walk_forward(
    base: StrategyParams,
    grid: dict[str, list[Any]],
    series: dict[str, list[Candle]],
    *,
    config: BacktestConfig | None = None,
    train_ratio: float = 0.7,
    min_trades: int = 10,
    objective: str = "expectancy",
    top: int = 10,
) -> list[tuple[OptimizeResult, Metrics]]:
    """Оптимизировать на train, честно оценить на test.

    Возвращает список (train_result, test_metrics) для top-N train-решений.
    """
    train, test, _ = time_split(series, train_ratio)
    if not train or not test:
        return []
    train_results = grid_search(
        base, grid, train, config=config, min_trades=min_trades, objective=objective, top=top
    )
    out: list[tuple[OptimizeResult, Metrics]] = []
    for res in train_results:
        test_metrics = Backtester(res.params, config).run(test).metrics
        out.append((res, test_metrics))
    return out