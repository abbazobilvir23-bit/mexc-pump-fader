"""Источники сигналов (внешние каналы)."""

from mpf.signals.telegram import (
    TelegramSignal,
    fetch_preview,
    fetch_signals,
    parse_signals,
)

__all__ = ["TelegramSignal", "fetch_preview", "fetch_signals", "parse_signals"]