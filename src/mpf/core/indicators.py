"""Чистые функции-индикаторы. Без внешних зависимостей, только stdlib."""

from __future__ import annotations

from collections.abc import Sequence

from mpf.models import Candle


def pct_change(values: Sequence[float], window: int) -> float:
    """Изменение от values[-1-window] к values[-1] в процентах.

    Возвращает 0.0, если данных не хватает или база <= 0.
    """
    if window < 1 or len(values) < window + 1:
        return 0.0
    base = values[-1 - window]
    if base <= 0:
        return 0.0
    return (values[-1] / base - 1.0) * 100.0


def sma(values: Sequence[float], period: int) -> float:
    if period < 1 or len(values) < period:
        return 0.0
    window = values[-period:]
    return sum(window) / period


def ema(values: Sequence[float], period: int) -> float:
    """Экспоненциальная скользящая средняя. Малые серии — возвращает SMA."""
    if period < 1 or not values:
        return 0.0
    if len(values) < period:
        return sum(values) / len(values)
    k = 2.0 / (period + 1.0)
    value = sum(values[:period]) / period
    for price in values[period:]:
        value = price * k + value * (1.0 - k)
    return value


def median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def rsi(values: Sequence[float], period: int = 14) -> float:
    """RSI по Уайлдеру. При недостатке данных возвращает нейтральные 50.0."""
    if period < 1 or len(values) < period + 1:
        return 50.0
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        delta = values[i] - values[i - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    avg_gain = gains / period
    avg_loss = losses / period
    for i in range(period + 1, len(values)):
        delta = values[i] - values[i - 1]
        gain = max(delta, 0.0)
        loss = max(-delta, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def true_range(candle: Candle, prev_close: float | None) -> float:
    if prev_close is None:
        return candle.high - candle.low
    return max(
        candle.high - candle.low,
        abs(candle.high - prev_close),
        abs(candle.low - prev_close),
    )


def atr(candles: Sequence[Candle], period: int = 14) -> float:
    """Average True Range по Уайлдеру."""
    if len(candles) < 2:
        return 0.0
    trs: list[float] = []
    for i, candle in enumerate(candles):
        prev_close = candles[i - 1].close if i > 0 else None
        trs.append(true_range(candle, prev_close))
    if len(trs) < period:
        return sum(trs) / len(trs)
    value = sum(trs[:period]) / period
    for tr in trs[period:]:
        value = (value * (period - 1) + tr) / period
    return value


def volume_ratio(volumes: Sequence[float], lookback: int) -> float:
    """Объём последнего бара к медиане предыдущих `lookback` баров.

    Медиана устойчивее среднего: одиночные всплески её не сдвигают.
    """
    if len(volumes) < 2:
        return 0.0
    window = volumes[-1 - lookback : -1] if len(volumes) > lookback else volumes[:-1]
    base = median(window)
    if base <= 0:
        return 0.0
    return volumes[-1] / base


def atr_pct(candles: Sequence[Candle], period: int = 14) -> float:
    """ATR в процентах от текущей цены (аналог ATR_K из сигналов)."""
    if not candles or candles[-1].close <= 0:
        return 0.0
    value = atr(candles, period)
    return value / candles[-1].close * 100.0


def adx(candles: Sequence[Candle], period: int = 14) -> float:
    """Average Directional Index (Уайлдер) — сила тренда.

    0..100: <20 — флет, 20..25 — неопределённость, >25 — сильный тренд.
    Требует минимум 2*period+1 свечей, иначе возвращает 0.0.
    """
    n = len(candles)
    if period < 1 or n < 2 * period + 1:
        return 0.0

    trs: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for i in range(1, n):
        high = candles[i].high
        low = candles[i].low
        prev_high = candles[i - 1].high
        prev_low = candles[i - 1].low
        prev_close = candles[i - 1].close
        up = high - prev_high
        down = prev_low - low
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

    smoothed_tr = _wilder(trs, period)
    smoothed_plus = _wilder(plus_dm, period)
    smoothed_minus = _wilder(minus_dm, period)
    if not smoothed_tr:
        return 0.0

    dxs: list[float] = []
    for tr, plus, minus in zip(smoothed_tr, smoothed_plus, smoothed_minus, strict=True):
        if tr <= 0:
            dxs.append(0.0)
            continue
        plus_di = 100.0 * plus / tr
        minus_di = 100.0 * minus / tr
        denom = plus_di + minus_di
        dxs.append(0.0 if denom == 0 else 100.0 * abs(plus_di - minus_di) / denom)
    if len(dxs) < period:
        return 0.0

    value = sum(dxs[:period]) / period
    for dx in dxs[period:]:
        value = (value * (period - 1) + dx) / period
    return value


def _wilder(values: Sequence[float], period: int) -> list[float]:
    """Сглаживание Уайлдера: первый элемент — сумма окна, далее рекурсия."""
    if len(values) < period:
        return []
    out = [sum(values[:period])]
    for value in values[period:]:
        out.append(out[-1] - out[-1] / period + value)
    return out