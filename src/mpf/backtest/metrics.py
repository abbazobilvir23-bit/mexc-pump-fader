"""Метрики бэктеста. Считаются из списка закрытых сделок и кривой equity."""

from __future__ import annotations

from dataclasses import dataclass

from mpf.models import ClosedTrade, ExitReason


@dataclass(frozen=True, slots=True)
class Metrics:
    trades: int
    wins: int
    losses: int
    win_rate: float            # %
    gross_profit: float
    gross_loss: float
    net_pnl: float
    total_fees: float
    profit_factor: float       # gross_profit / |gross_loss|
    expectancy: float          # средний net PnL на сделку
    avg_win: float
    avg_loss: float
    avg_entries: float         # среднее число входов на сделку
    worst_adverse_pct: float   # худшее движение против позиции среди сделок, %
    avg_adverse_pct: float     # среднее максимальное движение против, %
    max_drawdown: float
    max_drawdown_pct: float
    initial_equity: float
    final_equity: float
    return_pct: float
    liquidations: int = 0


def compute_metrics(
    trades: list[ClosedTrade],
    equity_curve: list[tuple[int, float]],
    initial_equity: float,
) -> Metrics:
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]

    gross_profit = sum(t.net_pnl for t in wins)
    gross_loss = sum(t.net_pnl for t in losses)

    total_fees = sum(t.fees for t in trades)
    net_pnl = gross_profit + gross_loss

    profit_factor = (
        gross_profit / abs(gross_loss) if gross_loss < 0 else float("inf")
    )
    win_rate = (len(wins) / len(trades) * 100.0) if trades else 0.0
    expectancy = net_pnl / len(trades) if trades else 0.0
    avg_win = gross_profit / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    avg_entries = sum(t.entries for t in trades) / len(trades) if trades else 0.0
    adversities = [t.max_adverse_pct for t in trades]
    worst_adverse = max(adversities) if adversities else 0.0
    avg_adverse = sum(adversities) / len(adversities) if adversities else 0.0

    max_dd, max_dd_pct = _max_drawdown(equity_curve, initial_equity)
    final_equity = equity_curve[-1][1] if equity_curve else initial_equity
    return_pct = (
        (final_equity / initial_equity - 1.0) * 100.0 if initial_equity > 0 else 0.0
    )

    return Metrics(
        trades=len(trades),
        wins=len(wins),
        losses=len(losses),
        win_rate=round(win_rate, 2),
        gross_profit=round(gross_profit, 4),
        gross_loss=round(gross_loss, 4),
        net_pnl=round(net_pnl, 4),
        total_fees=round(total_fees, 4),
        profit_factor=round(profit_factor, 3),
        expectancy=round(expectancy, 4),
        avg_win=round(avg_win, 4),
        avg_loss=round(avg_loss, 4),
        avg_entries=round(avg_entries, 2),
        worst_adverse_pct=round(worst_adverse, 2),
        avg_adverse_pct=round(avg_adverse, 2),
        max_drawdown=round(max_dd, 4),
        max_drawdown_pct=round(max_dd_pct, 2),
        initial_equity=round(initial_equity, 2),
        final_equity=round(final_equity, 2),
        return_pct=round(return_pct, 2),
        liquidations=sum(1 for t in trades if t.exit_reason is ExitReason.LIQUIDATION),
    )


def _max_drawdown(
    equity_curve: list[tuple[int, float]],
    initial_equity: float,
) -> tuple[float, float]:
    """Максимальная просадка (абсолют и %) от пика кривой equity."""
    peak = initial_equity
    max_dd = 0.0
    max_dd_pct = 0.0
    for _, equity in equity_curve:
        peak = max(peak, equity)
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = (dd / peak * 100.0) if peak > 0 else 0.0
    return max_dd, max_dd_pct