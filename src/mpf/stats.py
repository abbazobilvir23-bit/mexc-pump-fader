"""Сбор статистики прогонов из файлов состояния и сделок.

Раннеры (`paper`, `follow`) пишут `state.json` и `trades.jsonl`. Этот модуль
читает их и собирает сводку для Telegram-бота и CLI.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mpf.backtest.metrics import Metrics, compute_metrics
from mpf.models import ClosedTrade, ExitReason, Side


@dataclass(slots=True)
class RunStats:
    name: str
    equity: float
    initial_equity: float
    trades_total: int
    open_positions: list[dict[str, Any]] = field(default_factory=list)
    signals_seen: int | None = None
    trades: list[ClosedTrade] = field(default_factory=list)

    @property
    def metrics(self) -> Metrics:
        curve = [(t.closed_ts, 0.0) for t in self.trades]
        return compute_metrics(self.trades, curve, self.initial_equity)


def load_state(path: str | Path) -> dict[str, Any]:
    file = Path(path)
    if not file.exists():
        return {}
    try:
        return json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def load_trades(path: str | Path) -> list[ClosedTrade]:
    file = Path(path)
    if not file.exists():
        return []
    trades: list[ClosedTrade] = []
    for line in file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            trades.append(_trade_from_dict(json.loads(line)))
        except (KeyError, TypeError, ValueError):
            continue
    return trades


def _trade_from_dict(row: dict[str, Any]) -> ClosedTrade:
    return ClosedTrade(
        symbol=str(row["symbol"]),
        side=Side(row.get("side", "short")),
        entries=int(row["entries"]),
        opened_ts=int(row["opened_ts"]),
        closed_ts=int(row["closed_ts"]),
        avg_entry=float(row["avg_entry"]),
        exit_price=float(row["exit_price"]),
        qty=float(row["qty"]),
        gross_pnl=float(row["gross_pnl"]),
        fees=float(row["fees"]),
        net_pnl=float(row["net_pnl"]),
        return_pct=float(row["return_pct"]),
        exit_reason=ExitReason(row.get("exit_reason", "end_of_data")),
        max_adverse_pct=float(row.get("max_adverse_pct", 0.0)),
        funding=float(row.get("funding", 0.0)),
    )


def build_stats(
    name: str,
    state_path: str | Path,
    trades_path: str | Path,
    initial_equity: float = 1000.0,
) -> RunStats:
    state = load_state(state_path)
    trades = load_trades(trades_path)
    return RunStats(
        name=name,
        equity=float(state.get("equity", initial_equity)),
        initial_equity=initial_equity,
        trades_total=int(state.get("trades_total", len(trades))),
        open_positions=list(state.get("open_positions", [])),
        signals_seen=state.get("signals_seen"),
        trades=trades,
    )