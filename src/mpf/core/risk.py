"""Риск-менеджмент: размер позиции от риска, раскладка по усреднениям."""

from __future__ import annotations

from mpf.config import StrategyParams


def total_qty_for_risk(
    equity: float,
    entry_price: float,
    params: StrategyParams,
) -> float:
    """Суммарный объём позиции, при котором стоп съедает ровно `risk_per_trade_pct`.

    Для шорта убыток при стопе: qty * entry_price * stop_loss_pct / 100.
    Приравниваем к risk_amount и решаем относительно qty.
    """
    if equity <= 0 or entry_price <= 0:
        return 0.0
    risk_amount = equity * params.risk_per_trade_pct / 100.0
    loss_per_unit = entry_price * params.stop_loss_pct / 100.0
    if loss_per_unit <= 0:
        return 0.0
    return risk_amount / loss_per_unit


def total_qty_for_notional(
    equity: float,
    entry_price: float,
    params: StrategyParams,
) -> float:
    """Объём из доли equity (режим без стопа): notional = equity * position_pct%.

    Используется, когда стоп не задан и риск-сайзинг неприменим.
    """
    if equity <= 0 or entry_price <= 0:
        return 0.0
    notional = equity * params.position_pct / 100.0
    return notional / entry_price


def entry_sizes(total_qty: float, params: StrategyParams) -> list[float]:
    """Разбить суммарный объём на 1 базовый + `max_averages` усредняющих входов.

    Веса геометрические: 1, m, m^2, ... где m = `avg_size_mult`.
    При m = 1 все входы равны.
    """
    count = 1 + params.max_averages
    if count <= 0 or total_qty <= 0:
        return []
    weights = [params.avg_size_mult**i for i in range(count)]
    weight_sum = sum(weights)
    return [total_qty * w / weight_sum for w in weights]