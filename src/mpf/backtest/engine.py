"""Бэктест-движок поверх общей машины состояний `LiveState`.

Бэктест — это просто «проигрывание» истории через ту же логику, что работает
в paper-trading. Гарантия единства логики покрыта тестом на эквивалентность.

Ограничения (важно понимать!):
  * Нет стакана — проскальзывание задаётся константой `slippage_pct`.
  * Нет внутрибарного порядка — при неоднозначности приоритет у ближайшего уровня.
  * Funding-ставки не учтены.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mpf.backtest.metrics import Metrics, compute_metrics
from mpf.config import StrategyParams
from mpf.live.state import LiveState
from mpf.models import Candle, ClosedTrade


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    initial_equity: float = 1000.0
    max_concurrent: int = 3


@dataclass(slots=True)
class BacktestResult:
    params: StrategyParams
    config: BacktestConfig
    trades: list[ClosedTrade] = field(default_factory=list)
    equity_curve: list[tuple[int, float]] = field(default_factory=list)

    @property
    def metrics(self) -> Metrics:
        return compute_metrics(self.trades, self.equity_curve, self.config.initial_equity)


class Backtester:
    def __init__(self, params: StrategyParams, config: BacktestConfig | None = None) -> None:
        params.validate()
        self.params = params
        self.config = config or BacktestConfig()

    def run(self, candles_by_symbol: dict[str, list[Candle]]) -> BacktestResult:
        result = BacktestResult(params=self.params, config=self.config)
        series = {s: sorted(c, key=lambda c: c.ts) for s, c in candles_by_symbol.items() if c}
        if not series:
            return result

        state = LiveState(self.params, self.config.initial_equity, self.config.max_concurrent)
        for _ts, symbol, idx in _build_events(series):
            state.on_candle(symbol, series[symbol][idx])
        state.finalize({s: candles[-1] for s, candles in series.items()})

        result.trades = state.trades
        result.equity_curve = state.equity_curve
        return result


def _build_events(
    series: dict[str, list[Candle]],
) -> list[tuple[int, str, int]]:
    """Единая временная шкала: (ts, symbol, index), отсортированная по времени."""
    events: list[tuple[int, str, int]] = []
    for symbol, candles in series.items():
        for idx, candle in enumerate(candles):
            events.append((candle.ts, symbol, idx))
    events.sort(key=lambda e: (e[0], e[1]))
    return events