"""Детектор пампов: по серии свечей решает, есть ли сигнал на шорт.

Набор фич повторяет формат сигналов канала Optimac:
рост (PUMP), RSI_5m, всплеск объёма (X30), ATR_K (ATR в % от цены), ADX.
"""

from __future__ import annotations

from collections.abc import Sequence

from mpf.config import StrategyParams
from mpf.core.indicators import adx, atr_pct, pct_change, rsi, volume_ratio
from mpf.models import Candle, PumpSignal


def detect_pump(
    symbol: str,
    candles: Sequence[Candle],
    params: StrategyParams,
    end: int | None = None,
) -> PumpSignal | None:
    """Вернуть сигнал пампа или None.

    `end` — индекс «текущей» свечи (не включительно), по умолчанию конец ряда.
    Индикаторы считаются по ограниченному хвосту окна, а не по всей истории:
    это делает вызов O(1) по длине ряда (критично для grid search).

    Условия (все одновременно):
      1. Рост цены за `pump_lookback` >= `pump_threshold_pct`.
      2. Всплеск объёма (X) >= `volume_mult`.
      3. RSI >= `rsi_overbought` — перегрев.
      4. ATR_K >= `min_atr_pct` (если задан) — достаточная волатильность.
      5. ADX >= `min_adx` (если задан) — достаточная сила тренда.

    Фильтр ликвидности здесь НЕ применяется: он требует данных тикера
    и живёт в сканере (`mpf.scanner`).
    """
    end = len(candles) if end is None else end
    needed = max(params.pump_lookback, params.rsi_period, params.volume_lookback) + 1
    if end < needed:
        return None

    # Ограниченное окно: RSI/ADX Уайлдера сходятся быстро, длинной истории не нужно.
    window_len = (
        max(
            params.volume_lookback,
            params.rsi_period * 10,
            params.pump_lookback,
            params.atr_period * 3,
            params.adx_period * 3,
        )
        + 1
    )
    start = max(0, end - window_len)
    window = candles[start:end]

    closes = [c.close for c in window]
    volumes = [c.volume for c in window]

    pump_pct = pct_change(closes, params.pump_lookback)
    if pump_pct < params.pump_threshold_pct:
        return None

    vol_ratio = volume_ratio(volumes, params.volume_lookback)
    if vol_ratio < params.volume_mult:
        return None

    current_rsi = rsi(closes, params.rsi_period)
    if current_rsi < params.rsi_overbought:
        return None

    atr_value = atr_pct(window, params.atr_period)
    if atr_value < params.min_atr_pct:
        return None

    adx_value = adx(window, params.adx_period)
    if adx_value < params.min_adx:
        return None

    score = _score(pump_pct, vol_ratio, current_rsi, atr_value, adx_value, params)
    return PumpSignal(
        symbol=symbol,
        ts=window[-1].ts,
        pump_pct=pump_pct,
        volume_mult=vol_ratio,
        rsi=current_rsi,
        score=score,
        atr_pct=atr_value,
        adx=adx_value,
    )


def _score(
    pump_pct: float,
    vol_ratio: float,
    current_rsi: float,
    atr_value: float,
    adx_value: float,
    params: StrategyParams,
) -> float:
    """Нормированный скор [0..100] для ранжирования сигналов.

    Больше рост, объём, перегрев, волатильность и сила тренда — выше скор.
    Каждый фактор зажат в [0, 1].
    """
    growth = min(pump_pct / (params.pump_threshold_pct * 3.0), 1.0)
    volume = min(vol_ratio / (params.volume_mult * 3.0), 1.0)
    heat = min(max((current_rsi - params.rsi_overbought) / (100.0 - params.rsi_overbought), 0.0), 1.0)
    atr_ref = params.min_atr_pct * 3.0 if params.min_atr_pct > 0 else 10.0
    volatility = min(atr_value / atr_ref, 1.0)
    trend = min(adx_value / 40.0, 1.0)
    # Перегрев и объём весят больше: именно они дают откат, на котором мы зарабатываем.
    return round(
        (
            growth * 0.25
            + volume * 0.25
            + heat * 0.25
            + volatility * 0.15
            + trend * 0.10
        )
        * 100.0,
        2,
    )