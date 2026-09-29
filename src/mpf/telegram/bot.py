"""Telegram-бот статистики: меню, команды и уведомления о сделках.

Работает на stdlib. Показывает статистику paper/сигнальных прогонов, свежие
сигналы канала и присылает уведомления о закрытых сделках.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mpf.config import StrategyParams
from mpf.signals.telegram import DEFAULT_URL, fetch_signals
from mpf.stats import RunStats, build_stats, load_trades
from mpf.telegram.api import TelegramAPI

_COMMANDS: list[tuple[str, str]] = [
    ("menu", "Открыть меню"),
    ("stats", "Депозит и итоги торговли"),
    ("positions", "Открытые позиции"),
    ("trades", "Последние сделки"),
    ("signals", "Сигналы канала"),
    ("hours", "Статистика по часам (UTC)"),
    ("report", "Результаты стратегии (бэктест)"),
    ("status", "Статус сервисов"),
    ("help", "Помощь"),
]


@dataclass(slots=True)
class BotConfig:
    token: str
    data_dir: str = "data"
    chat_id: int | None = None
    channel_url: str = DEFAULT_URL
    initial_equity: float = 1000.0
    default_run: str = "follow"
    trades_limit: int = 10
    backtest_data: str | None = None
    backtest_config: str | None = None
    stale_minutes: int = 5            # если state.json старше — сервис «завис»
    health_cooldown_min: int = 30     # не спамить алертами чаще этого


def _esc(value: object) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def mexc_url(symbol: str) -> str:
    """Ссылка на график фьючерса MEXC по символу ccxt (GP/USDT:USDT)."""
    token = symbol.split("/")[0].upper()
    return f"https://www.mexc.com/ru-RU/futures/{token}_USDT"


class TelegramBot:
    def __init__(self, api: TelegramAPI, config: BotConfig) -> None:
        self.api = api
        self.config = config
        self._seen_counts: dict[str, int] = {}
        self._open_snapshot: dict[str, set[str]] = {}
        self._chat_runs: dict[int, str] = {}
        self._owner_chat: int | None = config.chat_id or self._load_chat()
        self._last_chat: int | None = self._owner_chat
        self._last_health_alert: float = 0.0
        self._last_signal_symbols: list[str] = []

    # --- чат-владелец ---

    def _chat_file(self) -> Path:
        return Path(self.config.data_dir) / "bot_chat_id.txt"

    def _load_chat(self) -> int | None:
        path = self._chat_file()
        if path.exists():
            try:
                return int(path.read_text(encoding="utf-8").strip())
            except (ValueError, OSError):
                return None
        return None

    def _remember_chat(self, chat_id: int) -> None:
        self._last_chat = chat_id
        if self._owner_chat == chat_id:
            return
        self._owner_chat = chat_id
        with contextlib.suppress(OSError):
            self._chat_file().write_text(str(chat_id), encoding="utf-8")

    # --- данные ---

    def _run_names(self) -> list[str]:
        base = Path(self.config.data_dir)
        if not base.exists():
            return []
        names: list[str] = []
        for entry in sorted(base.iterdir()):
            if entry.is_dir() and (
                (entry / "state.json").exists() or (entry / "trades.jsonl").exists()
            ):
                names.append(entry.name)
        return names

    def _resolve_run(self, chat_id: int | None, requested: str | None = None) -> str:
        names = self._run_names()
        if requested and requested in names:
            return requested
        if chat_id is not None and self._chat_runs.get(chat_id) in names:
            return self._chat_runs[chat_id]
        if self.config.default_run in names:
            return self.config.default_run
        return names[0] if names else self.config.default_run

    def _stats(self, name: str) -> RunStats:
        base = Path(self.config.data_dir) / name
        return build_stats(
            name,
            base / "state.json",
            base / "trades.jsonl",
            self.config.initial_equity,
        )

    # --- рендеринг ---

    def _menu(self, chat_id: int, extra_rows: list[list[dict[str, str]]] | None = None) -> dict[str, Any]:
        rows: list[list[dict[str, str]]] = [
            [
                {"text": "💰 Депозит и итоги", "callback_data": "stats"},
                {"text": "💼 Открытые позиции", "callback_data": "positions"},
            ],
            [
                {"text": "🧾 Последние сделки", "callback_data": "trades"},
                {"text": "📈 Сигналы канала", "callback_data": "signals"},
            ],
            [
                {"text": "🕒 По часам", "callback_data": "hours"},
                {"text": "📉 Результаты стратегии", "callback_data": "report"},
            ],
            [{"text": "❓ Помощь", "callback_data": "help"}],
        ]
        run_rows = [
            [{"text": ("✅ " if self._resolve_run(chat_id) == n else "") + n, "callback_data": f"run:{n}"}]
            for n in self._run_names()
        ]
        if run_rows:
            rows.append([b for row in run_rows for b in row])
        if extra_rows:
            rows.extend(extra_rows)
        return {"inline_keyboard": rows}

    def _chart_rows(self, symbols: list[str], limit: int = 6) -> list[list[dict[str, str]]]:
        """Кнопки-ссылки на график MEXC для списка символов."""
        seen: list[str] = []
        for symbol in symbols:
            if symbol and symbol not in seen:
                seen.append(symbol)
        return [
            [{"text": f"📈 {s.split('/')[0]} — график", "url": mexc_url(s)}]
            for s in seen[:limit]
        ]

    def _extra_rows(self, command: str, run: str) -> list[list[dict[str, str]]]:
        """Кнопки-ссылки на графики под сообщением, в зависимости от команды."""
        if command in ("stats", "positions"):
            symbols = [str(p.get("symbol")) for p in self._stats(run).open_positions]
            return self._chart_rows(symbols)
        if command == "trades":
            trades = load_trades(Path(self.config.data_dir) / run / "trades.jsonl")
            return self._chart_rows([t.symbol for t in trades[-6:]][::-1])
        if command == "signals":
            return self._chart_rows(self._last_signal_symbols)
        return []

    def _render_stats(self, name: str) -> str:
        stats = self._stats(name)
        m = stats.metrics
        initial = self.config.initial_equity
        current = stats.equity
        pnl = current - initial
        ret = (pnl / initial * 100.0) if initial else 0.0
        arrow = "📈" if pnl > 0 else ("📉" if pnl < 0 else "➖")
        lines = [
            f"💰 <b>Депозит и итоги</b> ({_esc(name)})",
            "",
            f"💵 Было: <b>${initial:.2f}</b>",
            f"💰 Стало: <b>${current:.2f}</b>",
            f"{arrow} Прибыль: <b>{pnl:+.2f}$</b> ({ret:+.2f}%)",
            "",
            f"🎯 Сделок: <b>{m.trades}</b> — успешных {m.wins}, убыточных {m.losses}",
            f"✅ Успешность: <b>{m.win_rate:.1f}%</b>",
            f"⚖️ Средняя прибыль {m.avg_win:+.2f}$ / убыток {m.avg_loss:+.2f}$",
            f"📉 Макс. просадка: {m.max_drawdown_pct:.2f}%",
            f"🔓 Открыто позиций: {len(stats.open_positions)}",
        ]
        reasons = Counter(t.exit_reason.value for t in stats.trades)
        if stats.trades:
            lines.append(
                f"🏁 Закрытия: тейк {reasons.get('take_profit', 0)}, "
                f"стоп {reasons.get('stop_loss', 0)}, "
                f"ликвид. {reasons.get('liquidation', 0)}, "
                f"конец данных {reasons.get('end_of_data', 0)}"
            )
        if stats.signals_seen is not None:
            lines.append(f"📡 Сигналов увидено: {stats.signals_seen}")
        if m.liquidations:
            lines.append(f"⚠️ Ликвидаций: {m.liquidations}")
        return "\n".join(lines)

    def _render_positions(self, name: str) -> str:
        stats = self._stats(name)
        lines = [f"💼 <b>Открытые позиции: {_esc(name)}</b>"]
        if not stats.open_positions:
            lines.append("— пусто")
        for pos in stats.open_positions:
            lines.append(
                f"• {_esc(pos.get('symbol'))} | входов {pos.get('entries')} "
                f"| avg {pos.get('avg_entry')} | qty {pos.get('qty')}"
            )
        return "\n".join(lines)

    def _render_trades(self, name: str) -> str:
        trades = load_trades(Path(self.config.data_dir) / name / "trades.jsonl")
        lines = [f"🧾 <b>Последние сделки: {_esc(name)}</b>"]
        if not trades:
            lines.append("— пока нет сделок")
        for trade in trades[-self.config.trades_limit:][::-1]:
            lines.append(
                f"• {_esc(trade.symbol)} <b>{_esc(trade.exit_reason.value)}</b> "
                f"{trade.net_pnl:+.3f} ({trade.return_pct:+.2f}%) входов={trade.entries}"
            )
        return "\n".join(lines)

    def _render_signals(self) -> str:
        try:
            signals = fetch_signals(self.config.channel_url)
        except Exception as exc:
            return f"📈 Не удалось получить сигналы: {_esc(type(exc).__name__)}"
        pumps = [s for s in signals if s.is_pump][-self.config.trades_limit:][::-1]
        self._last_signal_symbols = [s.symbol for s in pumps]
        lines = ["📈 <b>Сигналы канала (PUMP)</b>"]
        if not pumps:
            lines.append("— нет")
        for s in pumps:
            lines.append(
                f"• {_esc(s.token)} P{s.change_pct:+.1f}% RSI {s.rsi} "
                f"ATR_K {s.atr_k} ADX {s.adx} | FP {s.prob_fp} OI {s.prob_oi}"
            )
        return "\n".join(lines)

    def _render_report(self) -> str:
        data = self.config.backtest_data
        if not data or not Path(data).exists():
            return "📉 Нет файла истории для бэктеста."
        from mpf.backtest.engine import BacktestConfig, Backtester
        from mpf.data.store import load_candles

        params = StrategyParams()
        cfg_path = self.config.backtest_config
        if cfg_path and Path(cfg_path).exists():
            params = StrategyParams.from_dict(
                json.loads(Path(cfg_path).read_text(encoding="utf-8"))
            )
        result = Backtester(
            params, BacktestConfig(initial_equity=self.config.initial_equity)
        ).run(load_candles(data))
        m = result.metrics
        return "\n".join(
            [
                "📉 <b>Бэктест стратегии</b>",
                f"Конфиг: {_esc(cfg_path or 'по умолчанию')}",
                f"Сделок: {m.trades} | winrate: {m.win_rate:.1f}%",
                f"Net PnL: {m.net_pnl:+.3f} ({m.return_pct:+.2f}%)",
                f"Profit factor: {m.profit_factor:.2f}",
                f"Средний +/-: {m.avg_win:+.3f} / {m.avg_loss:+.3f}",
                f"Макс. просадка: {m.max_drawdown_pct:.2f}%",
                f"Ликвидаций: {m.liquidations}",
            ]
        )

    def _render_hours(self, name: str) -> str:
        """Статистика по часам UTC (для оценки эффекта времени суток)."""
        trades = load_trades(Path(self.config.data_dir) / name / "trades.jsonl")
        lines = [f"🕒 <b>Статистика по часам (UTC)</b> ({_esc(name)})"]
        if not trades:
            lines.append("— пока нет сделок")
            return "\n".join(lines)
        by_hour: dict[int, list[float]] = defaultdict(list)
        for trade in trades:
            hour = datetime.fromtimestamp(trade.opened_ts / 1000, tz=UTC).hour
            by_hour[hour].append(trade.net_pnl)
        for hour in sorted(by_hour):
            values = by_hour[hour]
            wins = sum(1 for v in values if v > 0)
            lines.append(
                f"{hour:02d}:00 — сделок {len(values)}, "
                f"успешных {wins / len(values) * 100:.0f}%, net {sum(values):+.2f}$"
            )
        return "\n".join(lines)

    def _render_status(self, name: str) -> str:
        stats = self._stats(name)
        state_path = Path(self.config.data_dir) / name / "state.json"
        raw = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        age = ""
        if state_path.exists():
            age_min = (time.time() - state_path.stat().st_mtime) / 60.0
            age = f"{age_min:.1f} мин назад"
        open_n = len(stats.open_positions)
        lines = [
            f"⚙️ <b>Статус</b> ({_esc(name)})",
            f"Депозит: ${self.config.initial_equity:.2f} → ${stats.equity:.2f}",
            f"Сделок: {stats.trades_total} | открыто: {open_n}",
            f"Сигналов увидено: {stats.signals_seen if stats.signals_seen is not None else '—'}",
            f"Обновление данных: {age or '—'}",
        ]
        counters = raw.get("stats") or {}
        if counters:
            lines.append(
                f"Обработано сигналов: {counters.get('signals', 0)}, "
                f"вошло: {counters.get('entered', 0)}"
            )
            lines.append(
                f"Отсеяно: фильтр {counters.get('blocked_filter', 0)}, "
                f"funding {counters.get('blocked_funding', 0)}, "
                f"OI {counters.get('blocked_oi', 0)}, слот {counters.get('blocked_slot', 0)}"
            )
            lines.append(
                f"Наблюдение: отриц. funding {counters.get('would_block_funding', 0)}, "
                f"OI>8% {counters.get('would_block_oi', 0)}"
            )
        return "\n".join(lines)

    def _render_help(self) -> str:
        cmds = "\n".join(f"/{c} — {d}" for c, d in _COMMANDS)
        return (
            "🤖 <b>Pump-Fader Bot</b>\n"
            "Статистика paper-торговли по сигналам канала.\n\n"
            f"{cmds}\n\n"
            "Бот присылает уведомления об открытии и закрытии сделок "
            "и предупредит, если сервис остановится."
        )

    # --- диспетчеризация ---

    def handle_text(self, chat_id: int, text: str) -> None:
        self._remember_chat(chat_id)
        parts = text.strip().split()
        command = parts[0].lstrip("/").split("@")[0] if parts else "menu"
        arg = parts[1] if len(parts) > 1 else None
        run = self._resolve_run(chat_id, arg)
        renderers: dict[str, Callable[[], str]] = {
            "stats": lambda: self._render_stats(run),
            "positions": lambda: self._render_positions(run),
            "trades": lambda: self._render_trades(run),
            "signals": self._render_signals,
            "hours": lambda: self._render_hours(run),
            "report": self._render_report,
            "status": lambda: self._render_status(run),
        }
        body = renderers.get(command, self._render_help)()
        self.api.send_message(chat_id, body, self._menu(chat_id, self._extra_rows(command, run)))

    def handle_callback(self, callback_id: str, chat_id: int, data: str, message_id: int) -> None:
        self._remember_chat(chat_id)
        self.api.answer_callback_query(callback_id)
        if data.startswith("run:"):
            self._chat_runs[chat_id] = data.split(":", 1)[1]
            run = self._resolve_run(chat_id)
            body = self._render_stats(run)
            extra = self._extra_rows("stats", run)
        else:
            run = self._resolve_run(chat_id)
            mapping = {
                "stats": lambda: self._render_stats(run),
                "positions": lambda: self._render_positions(run),
                "trades": lambda: self._render_trades(run),
                "signals": self._render_signals,
                "hours": lambda: self._render_hours(run),
                "report": self._render_report,
                "help": self._render_help,
            }
            body = mapping.get(data, self._render_help)()
            extra = self._extra_rows(data, run)
        self.api.edit_message_text(chat_id, message_id, body, self._menu(chat_id, extra))

    def _dispatch(self, update: dict[str, Any]) -> None:
        if "message" in update and "text" in update["message"]:
            message = update["message"]
            self.handle_text(int(message["chat"]["id"]), str(message["text"]))
        elif "callback_query" in update:
            query = update["callback_query"]
            message = query.get("message") or {}
            self.handle_callback(
                str(query["id"]),
                int(query["message"]["chat"]["id"]),
                str(query.get("data", "")),
                int(message.get("message_id", 0)),
            )

    def notify_new_trades(self) -> None:
        """Разослать уведомления о новых закрытых сделках."""
        chat_id = self._owner_chat
        if chat_id is None:
            return
        for name in self._run_names():
            path = Path(self.config.data_dir) / name / "trades.jsonl"
            trades = load_trades(path)
            count = len(trades)
            previous = self._seen_counts.get(name)
            if previous is None:
                self._seen_counts[name] = count
            else:
                for trade in trades[previous:count]:
                    icon = "🟢" if trade.net_pnl > 0 else "🔴"
                    self.api.send_message(
                        chat_id,
                        f"{icon} <b>{_esc(name)}</b>: {_esc(trade.symbol)} "
                        f"{_esc(trade.exit_reason.value)} {trade.net_pnl:+.3f} "
                        f"({trade.return_pct:+.2f}%)",
                    )
                self._seen_counts[name] = count

            # новые открытые позиции
            current = {str(p.get("symbol")) for p in self._stats(name).open_positions}
            snapshot = self._open_snapshot.get(name)
            if snapshot is None:
                self._open_snapshot[name] = current
            else:
                for symbol in current - snapshot:
                    self.api.send_message(
                        chat_id,
                        f"🟡 <b>{_esc(name)}</b>: открыта позиция {_esc(symbol)}",
                    )
                self._open_snapshot[name] = current

    def _check_health(self) -> None:
        """Алерт, если раннер не обновлял state.json дольше stale_minutes."""
        chat_id = self._owner_chat
        if chat_id is None:
            return
        now = time.time()
        cooldown = self.config.health_cooldown_min * 60
        if now - self._last_health_alert < cooldown:
            return
        problems: list[str] = []
        for name in self._run_names():
            state = Path(self.config.data_dir) / name / "state.json"
            if not state.exists():
                continue
            age_min = (now - state.stat().st_mtime) / 60.0
            if age_min > self.config.stale_minutes:
                problems.append(f"⚠️ «{name}» молчит {age_min:.0f} мин")
        if problems:
            self.api.send_message(
                chat_id, "🚨 <b>Проблема с сервисом</b>\n" + "\n".join(problems)
            )
            self._last_health_alert = now

    def run(
        self,
        *,
        poll_timeout: int = 25,
        stop: Callable[[], bool] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self.api.set_my_commands(_COMMANDS)
        chat_id = self._owner_chat
        if chat_id is not None:
            self.api.send_message(
                chat_id,
                "🤖 <b>Бот запущен.</b> Слежу за торговлей 24/7.\n\n" + self._render_help(),
                self._menu(chat_id),
            )
        offset: int | None = None
        while stop is None or not stop():
            try:
                for update in self.api.get_updates(offset, timeout=poll_timeout):
                    offset = int(update["update_id"]) + 1
                    self._dispatch(update)
                self.notify_new_trades()
                self._check_health()
            except Exception as exc:  # сеть/лимиты — не роняем бота
                if on_error is not None:
                    on_error(exc)