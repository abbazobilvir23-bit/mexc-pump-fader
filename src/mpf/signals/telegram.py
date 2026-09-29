"""Парсер сигналов публичного Telegram-канала (превью t.me/s/<channel>).

Работает на stdlib: качаем HTML превью, вытаскиваем карточки сигналов.
Формат карточки (Optimac Trading Signals):

    Token: GP
    PUMP +14.07%
    RSI_5m: 71.82 ✅   ATR_K: 3.53 ❌   ADX: 17.8 ✅
    Вероятность отката (FP): 75%
    Вероятность отката (OI): 95%
    Ближайшая плотность в стакане: 0.00552
    MEXC ✅
"""

from __future__ import annotations

import html as html_module
import re
import urllib.request
from dataclasses import dataclass

DEFAULT_CHANNEL = "OptimacTrading"
DEFAULT_URL = f"https://t.me/s/{DEFAULT_CHANNEL}"

_EMOJI = {"✅": True, "❌": False}


@dataclass(frozen=True, slots=True)
class TelegramSignal:
    """Разобранный сигнал из канала."""

    post_id: int
    token: str
    direction: str            # PUMP | DUMP
    change_pct: float
    rsi: float | None = None
    rsi_ok: bool | None = None
    atr_k: float | None = None
    atr_ok: bool | None = None
    adx: float | None = None
    adx_ok: bool | None = None
    prob_fp: int | None = None
    prob_oi: int | None = None
    density: float | None = None
    exchange_ok: bool = True

    @property
    def symbol(self) -> str:
        """Унифицированный символ MEXC USDT-M (для ccxt)."""
        return f"{self.token}/USDT:USDT"

    @property
    def is_pump(self) -> bool:
        return self.direction == "PUMP"


def fetch_preview(url: str = DEFAULT_URL, timeout: float = 20.0) -> str:
    """Скачать HTML публичного превью канала."""
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def parse_signals(html: str) -> list[TelegramSignal]:
    """Извлечь все сигналы со страницы превью (в порядке появления)."""
    out: list[TelegramSignal] = []
    post_re = re.compile(r'data-post="[^"/]+/(\d+)"(.*?)(?=data-post="[^"/]+/\d+"|$)', re.S)
    text_re = re.compile(
        r'tgme_widget_message_text js-message_text[^>]*>(.*?)</div>', re.S
    )
    for post in post_re.finditer(html):
        post_id = int(post.group(1))
        text_match = text_re.search(post.group(2))
        if not text_match:
            continue
        signal = _parse_text(post_id, _to_text(text_match.group(1)))
        if signal is not None:
            out.append(signal)
    return out


def fetch_signals(url: str = DEFAULT_URL) -> list[TelegramSignal]:
    return parse_signals(fetch_preview(url))


def _to_text(fragment: str) -> str:
    """HTML карточки -> плоский текст (сохраняя ✅/❌)."""
    text = re.sub(r"<br\s*/?>", "\n", fragment)
    text = re.sub(r"<[^>]+>", "", text)
    return html_module.unescape(text)


def _parse_text(post_id: int, text: str) -> TelegramSignal | None:
    token = re.search(r"Token:\s*([A-Za-z0-9]+)", text)
    move = re.search(r"(PUMP|DUMP)\s*([+-]?\d+(?:\.\d+)?)%", text)
    if not token or not move:
        return None

    rsi, rsi_ok = _num_ok(text, "RSI_5m")
    atr_k, atr_ok = _num_ok(text, "ATR_K")
    adx, adx_ok = _num_ok(text, "ADX")
    fp = re.search(r"отката \(FP\):\s*(\d+)%", text)
    oi = re.search(r"отката \(OI\):\s*(\d+)%", text)
    density = re.search(r"плотность в стакане:\s*(\d+(?:\.\d+)?)", text)
    exch = re.search(r"MEXC\s*([✅❌])", text)

    return TelegramSignal(
        post_id=post_id,
        token=token.group(1),
        direction=move.group(1),
        change_pct=float(move.group(2)),
        rsi=rsi,
        rsi_ok=rsi_ok,
        atr_k=atr_k,
        atr_ok=atr_ok,
        adx=adx,
        adx_ok=adx_ok,
        prob_fp=int(fp.group(1)) if fp else None,
        prob_oi=int(oi.group(1)) if oi else None,
        density=float(density.group(1)) if density else None,
        exchange_ok=(_EMOJI.get(exch.group(1), True) if exch else False),
    )


def _num_ok(text: str, label: str) -> tuple[float | None, bool | None]:
    match = re.search(rf"{label}:\s*(\d+(?:\.\d+)?)\s*([✅❌])?", text)
    if not match:
        return None, None
    value = float(match.group(1))
    flag = _EMOJI.get(match.group(2)) if match.group(2) else None
    return value, flag