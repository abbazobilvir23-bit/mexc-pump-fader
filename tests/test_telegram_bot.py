"""Тесты статистики и Telegram-бота (без сети)."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from mpf.models import ClosedTrade, ExitReason, Side
from mpf.stats import build_stats, load_trades
from mpf.telegram.api import TelegramAPI
from mpf.telegram.bot import BotConfig, TelegramBot


class _FakeAPI:
    def __init__(self) -> None:
        self.sent: list[tuple] = []
        self.edits: list[tuple] = []
        self.answers: list[str] = []
        self.commands: list | None = None

    def send_message(self, chat_id, text, reply_markup=None):  # type: ignore[no-untyped-def]
        self.sent.append((chat_id, text, reply_markup))
        return {}

    def edit_message_text(self, chat_id, message_id, text, reply_markup=None):  # type: ignore[no-untyped-def]
        self.edits.append((chat_id, message_id, text, reply_markup))
        return {}

    def answer_callback_query(self, callback_id, text=None):  # type: ignore[no-untyped-def]
        self.answers.append(callback_id)
        return {}

    def set_my_commands(self, commands):  # type: ignore[no-untyped-def]
        self.commands = commands
        return {}

    def get_updates(self, offset=None, timeout=25):  # type: ignore[no-untyped-def]
        return []


def _trade(net: float, reason: ExitReason = ExitReason.TAKE_PROFIT) -> ClosedTrade:
    return ClosedTrade(
        symbol="GP/USDT:USDT", side=Side.SHORT, entries=1, opened_ts=1, closed_ts=2,
        avg_entry=100.0, exit_price=98.0, qty=0.5, gross_pnl=net, fees=0.01,
        net_pnl=net, return_pct=2.0, exit_reason=reason,
    )


def _setup(tmp_path: Path, trades: list[ClosedTrade], state_extra: dict | None = None) -> Path:
    data = tmp_path / "data"
    run = data / "follow"
    run.mkdir(parents=True)
    (run / "state.json").write_text(
        json.dumps(
            {
                "equity": 1005.0,
                "trades_total": len(trades),
                "signals_seen": 42,
                "open_positions": [{"symbol": "Q/USDT:USDT", "entries": 1, "avg_entry": 1.0, "qty": 2.0}],
            }
        ),
        encoding="utf-8",
    )
    with (run / "trades.jsonl").open("w", encoding="utf-8") as fh:
        for t in trades:
            fh.write(json.dumps(asdict(t), ensure_ascii=False) + "\n")
    return data


def test_build_stats_and_load_trades(tmp_path) -> None:
    data = _setup(tmp_path, [_trade(1.0), _trade(-2.0, ExitReason.STOP_LOSS)])
    stats = build_stats("follow", data / "follow/state.json", data / "follow/trades.jsonl")
    assert stats.equity == 1005.0
    assert stats.trades_total == 2
    assert stats.signals_seen == 42
    assert stats.metrics.win_rate == 50.0
    assert load_trades(data / "follow/trades.jsonl")[1].exit_reason is ExitReason.STOP_LOSS


def test_stats_command_sends_message(tmp_path) -> None:
    data = _setup(tmp_path, [_trade(1.0)])
    api = _FakeAPI()
    bot = TelegramBot(api, BotConfig(token="x", data_dir=str(data), chat_id=1))
    bot.handle_text(1, "/stats")
    assert api.sent
    text = api.sent[-1][1]
    assert "Депозит" in text
    assert "Было" in text and "Стало" in text
    assert "follow" in text


def test_chat_id_persisted(tmp_path) -> None:
    data = _setup(tmp_path, [])
    api = _FakeAPI()
    bot = TelegramBot(api, BotConfig(token="x", data_dir=str(data)))
    bot.handle_text(555, "/stats")
    assert (data / "bot_chat_id.txt").read_text(encoding="utf-8") == "555"
    bot2 = TelegramBot(_FakeAPI(), BotConfig(token="x", data_dir=str(data)))
    assert bot2._owner_chat == 555


def test_health_alerts_on_stale(tmp_path) -> None:
    import os
    import time

    data = _setup(tmp_path, [])
    old = time.time() - 3600
    os.utime(data / "follow/state.json", (old, old))
    api = _FakeAPI()
    bot = TelegramBot(api, BotConfig(token="x", data_dir=str(data), chat_id=1, stale_minutes=0))
    bot._check_health()
    assert any("Проблема" in text for _c, text, _k in api.sent)


def test_callback_edits_message(tmp_path) -> None:
    data = _setup(tmp_path, [_trade(1.0)])
    api = _FakeAPI()
    bot = TelegramBot(api, BotConfig(token="x", data_dir=str(data), chat_id=1))
    bot.handle_callback("cb1", 1, "positions", 10)
    assert api.answers == ["cb1"]
    assert api.edits and "позиции" in api.edits[-1][2].lower()


def test_unknown_command_shows_help(tmp_path) -> None:
    data = _setup(tmp_path, [])
    api = _FakeAPI()
    bot = TelegramBot(api, BotConfig(token="x", data_dir=str(data), chat_id=1))
    bot.handle_text(1, "/nonsense")
    assert "Помощь" in api.sent[-1][1] or "коротк" in api.sent[-1][1] or "tp" in api.sent[-1][1].lower()


def test_notify_new_trades_baseline_then_alert(tmp_path) -> None:
    data = _setup(tmp_path, [_trade(1.0)])
    api = _FakeAPI()
    bot = TelegramBot(api, BotConfig(token="x", data_dir=str(data), chat_id=1))
    bot.notify_new_trades()  # базовая линия — без уведомлений
    assert api.sent == []
    with (data / "follow/trades.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(_trade(3.0)), ensure_ascii=False) + "\n")
    bot.notify_new_trades()
    assert len(api.sent) == 1
    assert "GP/USDT:USDT" in api.sent[0][1]


def test_notify_open_position(tmp_path) -> None:
    data = _setup(tmp_path, [])  # state уже содержит открытую Q/USDT:USDT
    api = _FakeAPI()
    bot = TelegramBot(api, BotConfig(token="x", data_dir=str(data), chat_id=1))
    bot.notify_new_trades()  # базовая линия открытых позиций
    assert api.sent == []

    # добавляем новую открытую позицию в state
    state_path = data / "follow/state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["open_positions"].append(
        {"symbol": "GP/USDT:USDT", "entries": 1, "avg_entry": 1.0, "qty": 1.0}
    )
    state_path.write_text(json.dumps(state), encoding="utf-8")
    bot.notify_new_trades()
    assert any("открыта позиция" in text for _cid, text, _kb in api.sent)


def test_api_requires_token() -> None:
    assert TelegramAPI("123:abc").base.endswith("/bot123:abc")


def test_mexc_url() -> None:
    from mpf.telegram.bot import mexc_url

    assert mexc_url("GP/USDT:USDT") == "https://www.mexc.com/ru-RU/futures/GP_USDT"


def test_hours_render(tmp_path) -> None:
    from mpf.models import ClosedTrade, ExitReason, Side

    def make(ts: int, net: float) -> ClosedTrade:
        return ClosedTrade(
            symbol="A/USDT:USDT", side=Side.SHORT, entries=1, opened_ts=ts, closed_ts=ts + 1,
            avg_entry=1.0, exit_price=0.99, qty=1.0, gross_pnl=net, fees=0.0,
            net_pnl=net, return_pct=1.0, exit_reason=ExitReason.TAKE_PROFIT,
        )

    hour = 3_600_000
    data = _setup(tmp_path, [make(0, 0.5), make(5 * hour, -0.2)])
    api = _FakeAPI()
    bot = TelegramBot(api, BotConfig(token="x", data_dir=str(data), chat_id=1))
    bot.handle_text(1, "/hours")
    text = api.sent[-1][1]
    assert "00:00" in text
    assert "05:00" in text


def test_positions_have_chart_button(tmp_path) -> None:
    data = _setup(tmp_path, [])  # state содержит открытую Q/USDT:USDT
    api = _FakeAPI()
    bot = TelegramBot(api, BotConfig(token="x", data_dir=str(data), chat_id=1))
    bot.handle_text(1, "/positions")
    keyboard = api.sent[-1][2]
    urls = [b.get("url") for row in keyboard["inline_keyboard"] for b in row if b.get("url")]
    assert any("Q_USDT" in u for u in urls)


def test_report_without_data(tmp_path) -> None:
    api = _FakeAPI()
    bot = TelegramBot(api, BotConfig(token="x", data_dir=str(tmp_path), chat_id=1))
    bot.handle_text(1, "/report")
    assert "Нет файла истории" in api.sent[-1][1]


def test_report_with_data(tmp_path) -> None:
    from mpf.data.store import save_candles
    from mpf.data.synthetic import generate_pump_series

    history = tmp_path / "h.jsonl"
    save_candles(history, {"S1": generate_pump_series(seed=1)})
    api = _FakeAPI()
    bot = TelegramBot(
        api,
        BotConfig(
            token="x", data_dir=str(tmp_path), chat_id=1, backtest_data=str(history)
        ),
    )
    bot.handle_text(1, "/report")
    assert "Бэктест стратегии" in api.sent[-1][1]