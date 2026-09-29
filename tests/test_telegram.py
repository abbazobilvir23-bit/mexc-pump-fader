"""Тесты парсера сигналов Telegram-канала."""

from __future__ import annotations

from mpf.signals.telegram import parse_signals

_CARD = """
<div class="tgme_widget_message" data-post="OptimacTrading/41909">
  <div class="tgme_widget_message_text js-message_text" dir="auto">
    Token: <code>Q</code><br/>
    <b>PUMP +12.67%</b><br/>
    RSI_5m: 82.0 <i class="emoji"><b>✅</b></i>
    ATR_K: 9.24 <i class="emoji"><b>✅</b></i>
    ADX: 22.0 <i class="emoji"><b>❌</b></i><br/>
    Вероятность отката (FP): 3%<br/>
    Вероятность отката (OI): 75%<br/>
    Ближайшая плотность в стакане: 0.0365<br/>
    <a href="https://www.mexc.com/ru-RU/futures/Q_USDT">MEXC</a> ✅
  </div>
  <div class="tgme_widget_message_footer"></div>
</div>
"""

_DUMP_CARD = """
<div class="tgme_widget_message" data-post="OptimacTrading/41913">
  <div class="tgme_widget_message_text js-message_text" dir="auto">
    Token: <code>ROBIN</code><br/>
    <b>DUMP -13.08%</b><br/>
    RSI_5m: 22.84 <i><b>✅</b></i> ATR_K: 4.70 <i><b>✅</b></i>
    ADX: 21.7 <i><b>✅</b></i><br/>
    Вероятность отката (FP): 1%<br/>
    Вероятность отката (OI): 100%<br/>
    Ближайшая плотность в стакане: 0.001595<br/>
    MEXC ❌
  </div>
</div>
"""


def test_parse_pump_card() -> None:
    signals = parse_signals(_CARD)
    assert len(signals) == 1
    s = signals[0]
    assert s.post_id == 41909
    assert s.token == "Q"
    assert s.is_pump
    assert s.change_pct == 12.67
    assert s.rsi == 82.0 and s.rsi_ok is True
    assert s.atr_k == 9.24 and s.atr_ok is True
    assert s.adx == 22.0 and s.adx_ok is False
    assert s.prob_fp == 3
    assert s.prob_oi == 75
    assert s.density == 0.0365
    assert s.exchange_ok is True
    assert s.symbol == "Q/USDT:USDT"


def test_parse_dump_card() -> None:
    s = parse_signals(_DUMP_CARD)[0]
    assert s.token == "ROBIN"
    assert not s.is_pump
    assert s.change_pct == -13.08
    assert s.exchange_ok is False


def test_parse_multiple_cards() -> None:
    assert len(parse_signals(_CARD + _DUMP_CARD)) == 2


def test_ignore_non_signal_message() -> None:
    assert parse_signals('<div data-post="X/1"><div class="tgme_widget_message_text js-message_text">Привет</div></div>') == []