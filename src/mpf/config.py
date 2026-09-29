"""Параметры стратегии. Один immutable-объект, который прокидывается во все расчёты."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any


@dataclass(frozen=True, slots=True)
class StrategyParams:
    """Все настройки стратегии «fade the pump + усреднение».

    Значения по умолчанию — стартовая гипотеза, НЕ оптимум.
    Их обязательно нужно подтвердить бэктестом (см. `mpf backtest`).
    """

    # --- Детектор пампа ---
    pump_lookback: int = 15          # окно свечей для оценки роста
    pump_threshold_pct: float = 15.0  # минимальный рост за окно, %
    volume_lookback: int = 60         # окно для медианного объёма
    volume_mult: float = 3.0          # объём последней свечи / медиана, раз
    rsi_period: int = 14
    rsi_overbought: float = 75.0      # RSI выше этого = перегрев
    atr_period: int = 14
    min_atr_pct: float = 0.0          # минимальный ATR в % от цены (ATR_K)
    adx_period: int = 14
    min_adx: float = 0.0              # минимальная сила тренда (0 = без фильтра)

    # --- Подтверждение входа ---
    # Не входим во время пампа, а ждём откат от пика на entry_pullback_pct.
    # Это защита от продолжения роста сразу после сигнала (главный источник стопов).
    entry_pullback_pct: float = 3.0
    entry_timeout_candles: int = 10    # сколько ждать откат, иначе снимаем сигнал

    # --- Фильтр ликвидности ---
    min_quote_volume_24h: float = 200_000.0    # ниже — слишком мёртвая пара
    max_quote_volume_24h: float = 5_000_000.0  # выше — уже не «низколиквидный памп»

    # --- Управление позицией ---
    max_averages: int = 2             # максимум ДОбавочных входов (итого 1 + N)
    avg_step_pct: float = 5.0         # шаг усреднения от последнего входа, %
    avg_size_mult: float = 1.0        # множитель размера каждого следующего входа
    take_profit_pct: float = 1.5      # тейк от средней цены, %
    stop_loss_pct: float = 20.0       # стоп от ПЕРВОГО входа, %
    use_stop_loss: bool = True         # False = режим «без стопа» (как руками)
    position_pct: float = 10.0         # % equity на позицию; используется только без стопа
    leverage: float = 1.0              # плечо; >1 включает порог ликвидации
    maintenance_margin_pct: float = 0.5  # поддерживающая маржа MEXC, %

    # --- Риск и издержки ---
    risk_per_trade_pct: float = 1.0   # риск на сделку, % от equity (до стопа)
    fee_pct: float = 0.06             # комиссия тейкера за сторону, %
    maker_fee_pct: float = 0.02       # комиссия мейкера за сторону, %
    maker_entries: bool = False       # True: вход и тейк лимитками (maker), стоп — маркет
    slippage_pct: float = 0.15        # проскальзывание тейкер-исполнения за сторону, %
    trading_hours_utc: tuple[int, ...] = ()  # разрешённые часы UTC (пусто = все)

    def validate(self) -> None:
        """Проверить внутреннюю согласованность. Бросает ValueError."""
        if self.pump_lookback < 2:
            raise ValueError("pump_lookback должен быть >= 2")
        if self.pump_threshold_pct <= 0:
            raise ValueError("pump_threshold_pct должен быть > 0")
        if self.volume_lookback < 2:
            raise ValueError("volume_lookback должен быть >= 2")
        if self.volume_mult <= 0:
            raise ValueError("volume_mult должен быть > 0")
        if not 0 < self.rsi_overbought <= 100:
            raise ValueError("rsi_overbought должен быть в (0, 100]")
        if self.atr_period < 1:
            raise ValueError("atr_period должен быть >= 1")
        if self.min_atr_pct < 0:
            raise ValueError("min_atr_pct не может быть отрицательным")
        if self.adx_period < 1:
            raise ValueError("adx_period должен быть >= 1")
        if self.min_adx < 0:
            raise ValueError("min_adx не может быть отрицательным")
        if self.entry_pullback_pct < 0:
            raise ValueError("entry_pullback_pct не может быть отрицательным")
        if self.entry_timeout_candles < 1:
            raise ValueError("entry_timeout_candles должен быть >= 1")
        if self.max_averages < 0:
            raise ValueError("max_averages не может быть отрицательным")
        if self.avg_step_pct <= 0:
            raise ValueError("avg_step_pct должен быть > 0")
        if self.avg_size_mult <= 0:
            raise ValueError("avg_size_mult должен быть > 0")
        if self.take_profit_pct <= 0:
            raise ValueError("take_profit_pct должен быть > 0")
        if self.stop_loss_pct <= 0:
            raise ValueError("stop_loss_pct должен быть > 0")
        if not 0 < self.position_pct <= 100:
            raise ValueError("position_pct должен быть в (0, 100]")
        # Ключевая защита: все усреднения должны успеть сработать до стопа,
        # иначе последний вход уходит «за стоп» и риск-сайзинг врёт.
        # В режиме без стопа проверка неприменима.
        if self.use_stop_loss:
            last_avg_level = self.avg_step_pct * self.max_averages
            if self.stop_loss_pct <= last_avg_level:
                raise ValueError(
                    f"stop_loss_pct ({self.stop_loss_pct}) должен быть больше "
                    f"avg_step_pct * max_averages ({last_avg_level}), "
                    "иначе усреднения не успевают сработать до стопа"
                )
        if self.leverage < 1:
            raise ValueError("leverage должен быть >= 1")
        liq_distance = (1.0 / self.leverage - self.maintenance_margin_pct / 100.0) * 100.0
        if liq_distance <= 0:
            raise ValueError("maintenance_margin_pct слишком велик для такого плеча")
        if self.leverage > 1 and self.use_stop_loss and self.stop_loss_pct >= liq_distance:
            raise ValueError(
                f"stop_loss_pct ({self.stop_loss_pct}) должен быть меньше порога "
                f"ликвидации ({liq_distance:.2f}%) при плече {self.leverage}x"
            )
        if self.min_quote_volume_24h < 0 or self.max_quote_volume_24h <= self.min_quote_volume_24h:
            raise ValueError("некорректные границы ликвидности")
        if not 0 < self.risk_per_trade_pct <= 100:
            raise ValueError("risk_per_trade_pct должен быть в (0, 100]")
        if self.fee_pct < 0 or self.maker_fee_pct < 0:
            raise ValueError("комиссии не могут быть отрицательными")
        if self.slippage_pct < 0:
            raise ValueError("slippage_pct не может быть отрицательным")
        if any(h < 0 or h > 23 for h in self.trading_hours_utc):
            raise ValueError("trading_hours_utc: часы должны быть 0..23")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StrategyParams:
        """Собрать из словаря, игнорируя неизвестные ключи. Валидирует результат."""
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"неизвестные параметры: {sorted(unknown)}")
        params = cls(**{k: v for k, v in data.items() if k in known})
        params.validate()
        return params

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)