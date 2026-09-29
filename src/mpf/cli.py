"""CLI: scan | backtest | fetch | demo.

Примеры:
    python -m mpf.cli demo
    python -m mpf.cli scan --top 40
    python -m mpf.cli backtest --data data/history.jsonl
    python -m mpf.cli fetch --symbol BTC/USDT:USDT --limit 1000 --out data/history.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from mpf.backtest.engine import BacktestConfig, Backtester
from mpf.config import StrategyParams
from mpf.data.collector import collect_history, select_low_liquidity
from mpf.data.store import load_candles, merge_candles, save_candles
from mpf.data.synthetic import generate_pump_series
from mpf.optimize.optimizer import DEFAULT_GRID, grid_search, walk_forward
from mpf.signals.telegram import DEFAULT_URL


def load_params(path: str | None) -> StrategyParams:
    """Загрузить параметры из JSON или вернуть значения по умолчанию."""
    if path is None:
        params = StrategyParams()
        params.validate()
        return params
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return StrategyParams.from_dict(data)


def _cmd_demo(args: argparse.Namespace) -> int:
    params = load_params(args.config)
    series = {
        f"SYN{i}/USDT:USDT": generate_pump_series(seed=100 + i, pump_step_pct=15.0 + i)
        for i in range(args.symbols)
    }
    result = Backtester(params, BacktestConfig(initial_equity=args.equity)).run(series)
    _print_metrics(result.metrics)
    return 0


def _cmd_backtest(args: argparse.Namespace) -> int:
    params = load_params(args.config)
    series = load_candles(args.data)
    if not series:
        print(f"Нет данных в {args.data}", file=sys.stderr)
        return 1
    result = Backtester(params, BacktestConfig(initial_equity=args.equity)).run(series)
    _print_metrics(result.metrics)
    print(f"\nСделок: {len(result.trades)}, символов: {len(series)}")
    for trade in result.trades[: args.show]:
        print(
            f"  {trade.symbol:<20} {trade.exit_reason.value:<12} "
            f"входов={trade.entries} net={trade.net_pnl:+.4f} ({trade.return_pct:+.2f}%)"
        )
    return 0


def _cmd_scan(args: argparse.Namespace) -> int:
    params = load_params(args.config)
    from mpf.exchange.mexc import MexcExchange  # ленивый импорт: нужен ccxt
    from mpf.scanner.scanner import scan_market

    exchange = MexcExchange()
    try:
        result = scan_market(
            exchange,
            params,
            timeframe=args.timeframe,
            candles=args.candles,
            top_n=args.top,
        )
    finally:
        exchange.close()

    print(
        f"Тикеров: {result.fetched_tickers}, кандидатов по ликвидности: "
        f"{result.liquid_candidates}, проанализировано: {result.analyzed}, "
        f"сигналов: {len(result.signals)}"
    )
    for sig in result.top(args.limit):
        print(
            f"  {sig.symbol:<22} score={sig.score:>6.2f} "
            f"PUMP={sig.pump_pct:>7.2f}% RSI={sig.rsi:>5.1f} "
            f"ATR_K={sig.atr_pct:>6.2f} ADX={sig.adx:>5.1f} X={sig.volume_mult:>6.2f}"
        )
    return 0


def _cmd_fetch(args: argparse.Namespace) -> int:
    from mpf.exchange.mexc import MexcExchange

    exchange = MexcExchange()
    collected: dict[str, list] = {}
    try:
        for symbol in args.symbol:
            collected[symbol] = exchange.fetch_ohlcv(
                symbol, timeframe=args.timeframe, limit=args.limit
            )
            print(f"{symbol}: {len(collected[symbol])} свечей")
    finally:
        exchange.close()
    written = save_candles(args.out, collected)
    print(f"Сохранено {written} строк в {args.out}")
    return 0


def _cmd_collect(args: argparse.Namespace) -> int:
    params = load_params(args.config)
    from mpf.exchange.mexc import MexcExchange

    exchange = MexcExchange()
    try:
        tickers = exchange.fetch_futures_tickers()
        selected = select_low_liquidity(
            tickers, params, top_n=args.top, sort_by=args.sort
        )
        print(f"Низколиквидных кандидатов: {len(selected)} — качаю историю…")
        result = collect_history(
            exchange,
            [t.symbol for t in selected],
            timeframe=args.timeframe,
            days=args.days,
        )
    finally:
        exchange.close()

    if not result.series:
        print("Не удалось собрать данные.", file=sys.stderr)
        return 1
    series = result.series
    if args.append and Path(args.out).exists():
        series = merge_candles(load_candles(args.out), series)
    written = save_candles(args.out, series)
    total = sum(len(c) for c in series.values())
    print(
        f"Собрано: {len(result.series)} пар, {result.total_candles} новых свечей "
        f"({written} строк всего) -> {args.out}"
    )
    print(f"В архиве: {len(series)} пар, {total} свечей")
    if result.failures:
        print(f"Ошибок: {len(result.failures)} (например: "
              f"{next(iter(result.failures.items()))})")
    return 0


def _cmd_optimize(args: argparse.Namespace) -> int:
    base = load_params(args.config)
    series = load_candles(args.data)
    if not series:
        print(f"Нет данных в {args.data}", file=sys.stderr)
        return 1
    grid = json.loads(Path(args.grid).read_text(encoding="utf-8")) if args.grid else DEFAULT_GRID
    config = BacktestConfig(initial_equity=args.equity)

    header = (
        f"{'pump%':>6} {'vol×':>6} {'RSI':>5} {'ATR%':>5} {'pull%':>6} "
        f"{'tp%':>5} {'sl%':>5} {'avg':>4}"
    )

    if args.full:
        print(f"In-sample grid search: objective={args.objective}, "
              f"min_trades={args.min_trades}, комбинаций={_grid_size(grid)}")
        results = grid_search(
            base, grid, series, config=config, min_trades=args.min_trades,
            objective=args.objective, top=args.top,
        )
        if not results:
            print("Ни одна комбинация не прошла фильтр.", file=sys.stderr)
            return 1
        print()
        print(f"{header} | {'N':>5} {'WR':>6} {'net':>9} {'exp':>9}")
        print("-" * 100)
        for res in results:
            _print_row(res.params, res.metrics, None)
        return 0

    print(f"Walk-forward: train={args.train_ratio:.0%}, objective={args.objective}, "
          f"min_trades={args.min_trades}, комбинаций={_grid_size(grid)}")
    pairs = walk_forward(
        base, grid, series, config=config, train_ratio=args.train_ratio,
        min_trades=args.min_trades, objective=args.objective, top=args.top,
    )
    if not pairs:
        print("Ни одна комбинация не прошла фильтр (мало сделок?).", file=sys.stderr)
        return 1

    print()
    print(f"{header} | {'train N':>7} {'train exp':>9} | "
          f"{'test N':>6} {'test WR':>7} {'test net':>9} {'test exp':>9}")
    print("-" * 120)
    for train_res, test_metrics in pairs:
        _print_row(train_res.params, train_res.metrics, test_metrics)

    positive = sum(1 for _, tm in pairs if tm.expectancy > 0)
    print("-" * 120)
    print(f"Устойчивых на out-of-sample (expectancy>0): {positive} из {len(pairs)}")
    return 0


def _print_row(params: StrategyParams, train: object, test: object) -> None:
    p = params
    common = (
        f"{p.pump_threshold_pct:>6.1f} {p.volume_mult:>6.1f} {p.rsi_overbought:>5.1f} "
        f"{p.min_atr_pct:>5.1f} {p.entry_pullback_pct:>6.1f} {p.take_profit_pct:>5.1f} "
        f"{p.stop_loss_pct:>5.1f} {p.max_averages:>4}"
    )
    if test is None:
        print(
            f"{common} | {train.trades:>5} {train.win_rate:>5.1f}% "  # type: ignore[attr-defined]
            f"{train.net_pnl:>9.3f} {train.expectancy:>9.4f}"  # type: ignore[attr-defined]
        )
        return
    print(
        f"{common} | {train.trades:>7} {train.expectancy:>9.4f} | "  # type: ignore[attr-defined]
        f"{test.trades:>6} {test.win_rate:>6.1f}% {test.net_pnl:>9.3f} "  # type: ignore[attr-defined]
        f"{test.expectancy:>9.4f}"  # type: ignore[attr-defined]
    )


def _cmd_signals(args: argparse.Namespace) -> int:
    from mpf.signals.telegram import fetch_signals

    signals = fetch_signals(args.url)
    if args.pumps_only:
        signals = [s for s in signals if s.is_pump]
    if args.min_fp is not None:
        signals = [s for s in signals if (s.prob_fp or 0) >= args.min_fp]

    tickers: dict[str, object] = {}
    if not args.no_mexc:
        from mpf.exchange.mexc import MexcExchange

        exchange = MexcExchange()
        try:
            tickers = {t.symbol: t for t in exchange.fetch_futures_tickers()}
        finally:
            exchange.close()

    print(f"Сигналов из канала: {len(signals)} (показано {min(len(signals), args.limit)})")
    print(
        f"{'#':>6} {'token':<10} {'move':>8} {'RSI':>6} {'ATR_K':>6} {'ADX':>5} "
        f"{'FP':>4} {'OI%':>4} {'density':>10} {'MEXC OI':>10} {'vol24':>9} {'fund%':>8}"
    )
    print("-" * 110)
    for s in signals[: args.limit]:
        t = tickers.get(s.symbol)
        oi = f"{t.open_interest:>10.0f}" if t else "         -"  # type: ignore[attr-defined]
        vol = f"{t.quote_volume_24h / 1000:>8.0f}K" if t else "        -"  # type: ignore[attr-defined]
        fund = f"{t.funding_rate * 100:>7.4f}" if t else "      -"  # type: ignore[attr-defined]
        print(
            f"{s.post_id:>6} {s.token:<10} {s.direction[0]}{s.change_pct:>+6.1f}% "
            f"{_fmt(s.rsi):>6} {_fmt(s.atr_k):>6} {_fmt(s.adx):>5} "
            f"{_fmt(s.prob_fp):>4} {_fmt(s.prob_oi):>4} {_fmt(s.density):>10} "
            f"{oi} {vol} {fund}"
        )
    return 0


def _fmt(value: float | None) -> str:
    if value is None:
        return "-"
    if isinstance(value, float) and value != int(value):
        return f"{value:.2f}"
    return f"{value:.0f}"


def _cmd_paper(args: argparse.Namespace) -> int:
    params = load_params(args.config)
    from mpf.backtest.metrics import compute_metrics
    from mpf.exchange.mexc import MexcExchange
    from mpf.live.paper import PaperConfig, PaperTrader

    exchange = MexcExchange()
    try:
        tickers = exchange.fetch_futures_tickers()
        selected = select_low_liquidity(
            tickers, params, top_n=args.top, sort_by=args.sort
        )
        config = PaperConfig(
            symbols=[t.symbol for t in selected],
            params=params,
            initial_equity=args.equity,
            max_concurrent=args.max_concurrent,
            timeframe=args.timeframe,
            warmup=args.warmup,
            trades_path=f"{args.out}/trades.jsonl",
            state_path=f"{args.out}/state.json",
        )
        trader = PaperTrader(exchange, config)

        def on_tick(tick: int, new_trades: list) -> None:
            st = trader.state
            print(
                f"[tick {tick}] equity={st.equity:.2f} open={len(st.open_symbols)} "
                f"armed={len(st.armed_symbols)} trades={len(st.trades)} new={len(new_trades)}"
            )
            for t in new_trades:
                print(f"   ЗАКРЫТА {t.symbol} {t.exit_reason.value} net={t.net_pnl:+.4f}")

        state = trader.run(
            poll_seconds=args.poll, iterations=args.iterations, on_tick=on_tick
        )
    finally:
        exchange.close()

    metrics = compute_metrics(state.trades, state.equity_curve, args.equity)
    print()
    _print_metrics(metrics)
    return 0


def _cmd_follow(args: argparse.Namespace) -> int:
    params = load_params(args.config)
    overrides: dict = {}
    if args.maker:
        overrides["maker_entries"] = True
    if args.hours:
        overrides["trading_hours_utc"] = tuple(
            int(h) for h in args.hours.split(",") if h.strip()
        )
    if overrides:
        params = StrategyParams.from_dict({**params.to_dict(), **overrides})
    from mpf.backtest.metrics import compute_metrics
    from mpf.exchange.mexc import MexcExchange
    from mpf.live.signal_runner import SignalFilter, SignalPaperConfig, SignalPaperTrader

    signal_filter = SignalFilter(
        pumps_only=not args.include_dumps,
        min_prob_fp=args.min_fp,
        min_prob_oi=args.min_oi,
        require_indicators_ok=args.require_ok,
    )
    config = SignalPaperConfig(
        params=params,
        filter=signal_filter,
        initial_equity=args.equity,
        max_concurrent=args.max_concurrent,
        timeframe=args.timeframe,
        warmup=args.warmup,
        url=args.url,
        take_existing=args.take_existing,
        min_funding_rate=args.min_funding,
        max_oi_growth_pct=args.max_oi_growth,
        oi_window_min=args.oi_window,
        trades_path=f"{args.out}/trades.jsonl",
        state_path=f"{args.out}/state.json",
    )
    exchange = MexcExchange()
    try:
        trader = SignalPaperTrader(exchange, config)

        def on_tick(tick: int, new_trades: list) -> None:
            st = trader.state
            print(
                f"[tick {tick}] equity={st.equity:.2f} open={len(st.open_symbols)} "
                f"seen={len(trader.seen)} trades={len(st.trades)} new={len(new_trades)}"
            )
            for t in new_trades:
                print(f"   ЗАКРЫТА {t.symbol} {t.exit_reason.value} net={t.net_pnl:+.4f}")

        state = trader.run(
            poll_seconds=args.poll, iterations=args.iterations, on_tick=on_tick
        )
    finally:
        exchange.close()

    print()
    _print_metrics(compute_metrics(state.trades, state.equity_curve, args.equity))
    return 0


def _cmd_bot(args: argparse.Namespace) -> int:
    from mpf.telegram.api import TelegramAPI
    from mpf.telegram.bot import BotConfig, TelegramBot

    token = args.token or os.environ.get("MPF_TELEGRAM_TOKEN")
    if not token:
        print("Нужен токен: --token или MPF_TELEGRAM_TOKEN", file=sys.stderr)
        return 1
    chat_id = args.chat_id
    if chat_id is None and os.environ.get("MPF_TELEGRAM_CHAT_ID"):
        chat_id = int(os.environ["MPF_TELEGRAM_CHAT_ID"])

    config = BotConfig(
        token=token,
        data_dir=args.data_dir,
        chat_id=chat_id,
        channel_url=args.channel_url,
        initial_equity=args.equity,
        default_run=args.run,
        backtest_data=args.backtest_data,
        backtest_config=args.backtest_config,
    )
    bot = TelegramBot(TelegramAPI(token), config)
    print("Telegram-бот запущен (long polling). Ctrl+C для остановки.")
    bot.run(on_error=lambda exc: print(f"[bot] ошибка: {type(exc).__name__}: {exc}", file=sys.stderr))
    return 0


def _grid_size(grid: dict[str, list]) -> int:
    size = 1
    for values in grid.values():
        size *= max(len(values), 1)
    return size


def _print_metrics(m: object) -> None:
    print("=== Метрики бэктеста ===")
    print(f"Сделок:            {m.trades}  (побед {m.wins}, убытков {m.losses})")  # type: ignore[attr-defined]
    print(f"Winrate:           {m.win_rate}%")  # type: ignore[attr-defined]
    print(f"Net PnL:           {m.net_pnl:+.4f}")  # type: ignore[attr-defined]
    print(f"Комиссии:          {m.total_fees:.4f}")  # type: ignore[attr-defined]
    print(f"Profit factor:     {m.profit_factor}")  # type: ignore[attr-defined]
    print(f"Expectancy:        {m.expectancy:+.4f} на сделку")  # type: ignore[attr-defined]
    print(f"Средний выигрыш:   {m.avg_win:+.4f}")  # type: ignore[attr-defined]
    print(f"Средний убыток:    {m.avg_loss:+.4f}")  # type: ignore[attr-defined]
    print(f"Средне входов:     {m.avg_entries}")  # type: ignore[attr-defined]
    print(f"Худш. против:      {m.worst_adverse_pct}% (среднее {m.avg_adverse_pct}%)")  # type: ignore[attr-defined]
    print(f"Max drawdown:      {m.max_drawdown:.4f} ({m.max_drawdown_pct}%)")  # type: ignore[attr-defined]
    print(f"Итоговый equity:   {m.final_equity:.2f} ({m.return_pct:+.2f}%)")  # type: ignore[attr-defined]
    if getattr(m, "liquidations", 0):
        print(f"ЛИКВИДАЦИЙ:        {m.liquidations}")  # type: ignore[attr-defined]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mpf", description="MEXC pump-fader")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="прогон на синтетике (без сети)")
    demo.add_argument("--symbols", type=int, default=5)
    demo.add_argument("--equity", type=float, default=1000.0)
    demo.add_argument("--config")
    demo.set_defaults(func=_cmd_demo)

    bt = sub.add_parser("backtest", help="прогон на сохранённой истории")
    bt.add_argument("--data", required=True)
    bt.add_argument("--equity", type=float, default=1000.0)
    bt.add_argument("--config")
    bt.add_argument("--show", type=int, default=10, help="сколько сделок показать")
    bt.set_defaults(func=_cmd_backtest)

    scan = sub.add_parser("scan", help="сканировать MEXC на пампы")
    scan.add_argument("--timeframe", default="1m")
    scan.add_argument("--candles", type=int, default=200)
    scan.add_argument("--top", type=int, default=60, help="сколько пар проверять")
    scan.add_argument("--limit", type=int, default=20, help="сколько сигналов показать")
    scan.add_argument("--config")
    scan.set_defaults(func=_cmd_scan)

    fetch = sub.add_parser("fetch", help="скачать историю свечей")
    fetch.add_argument("--symbol", action="append", required=True)
    fetch.add_argument("--timeframe", default="1m")
    fetch.add_argument("--limit", type=int, default=1000)
    fetch.add_argument("--out", required=True)
    fetch.set_defaults(func=_cmd_fetch)

    collect = sub.add_parser("collect", help="собрать историю по низколиквидным парам")
    collect.add_argument("--top", type=int, default=80, help="сколько пар качать")
    collect.add_argument("--days", type=float, default=2.0)
    collect.add_argument("--timeframe", default="1m")
    collect.add_argument("--sort", choices=["change", "volume"], default="change")
    collect.add_argument("--out", required=True)
    collect.add_argument("--append", action="store_true", help="дополнить архив (дедуп по времени)")
    collect.add_argument("--config")
    collect.set_defaults(func=_cmd_collect)

    opt = sub.add_parser("optimize", help="walk-forward оптимизация параметров")
    opt.add_argument("--data", required=True)
    opt.add_argument("--grid", help="JSON-файл с сеткой параметров")
    opt.add_argument("--objective", default="expectancy",
                     choices=["expectancy", "net_pnl", "return_pct", "profit_factor", "win_rate"])
    opt.add_argument("--min-trades", type=int, default=20)
    opt.add_argument("--train-ratio", type=float, default=0.7)
    opt.add_argument("--top", type=int, default=10)
    opt.add_argument("--full", action="store_true", help="in-sample grid search без разбиения")
    opt.add_argument("--equity", type=float, default=1000.0)
    opt.add_argument("--config")
    opt.set_defaults(func=_cmd_optimize)

    paper = sub.add_parser("paper", help="paper-trading на живых данных MEXC (без денег)")
    paper.add_argument("--top", type=int, default=60, help="сколько пар отслеживать")
    paper.add_argument("--sort", choices=["change", "volume"], default="change")
    paper.add_argument("--timeframe", default="1m")
    paper.add_argument("--warmup", type=int, default=200)
    paper.add_argument("--equity", type=float, default=1000.0)
    paper.add_argument("--max-concurrent", type=int, default=3)
    paper.add_argument("--poll", type=int, default=30, help="секунд между опросами")
    paper.add_argument("--iterations", type=int, default=None, help="0/None = бесконечно")
    paper.add_argument("--out", default="data/paper")
    paper.add_argument("--config")
    paper.set_defaults(func=_cmd_paper)

    sig = sub.add_parser("signals", help="сигналы из Telegram-канала (публичное превью)")
    sig.add_argument("--url", default=DEFAULT_URL)
    sig.add_argument("--limit", type=int, default=20)
    sig.add_argument("--min-fp", type=float, default=None, help="мин. вероятность отката (FP)")
    sig.add_argument("--pumps-only", action="store_true", help="только PUMP, без DUMP")
    sig.add_argument("--no-mexc", action="store_true", help="не тянуть OI/объём с MEXC")
    sig.set_defaults(func=_cmd_signals)

    follow = sub.add_parser("follow", help="paper-торговля по сигналам Telegram-канала")
    follow.add_argument("--config", default="config/optimized_5m.json")
    follow.add_argument("--url", default=None, help="URL превью канала")
    follow.add_argument("--timeframe", default="5m")
    follow.add_argument("--warmup", type=int, default=200)
    follow.add_argument("--equity", type=float, default=100.0)
    follow.add_argument("--max-concurrent", type=int, default=3)
    follow.add_argument("--min-fp", type=int, default=0, help="мин. вероятность отката FP")
    follow.add_argument("--min-oi", type=int, default=0, help="мин. вероятность отката OI")
    follow.add_argument("--require-ok", action="store_true", help="требовать RSI/ATR/ADX ✅")
    follow.add_argument("--min-funding", type=float, default=None,
                        help="мин. funding; по умолчанию фильтр выключен")
    follow.add_argument("--max-oi-growth", type=float, default=0.0,
                        help="макс. рост Open Interest за окно, %% (0 = выкл.)")
    follow.add_argument("--oi-window", type=int, default=15, help="окно OI, мин")
    follow.add_argument("--maker", action="store_true",
                        help="вход и тейк лимитками (maker), ниже издержки")
    follow.add_argument("--hours", default=None,
                        help="разрешённые часы UTC через запятую, напр. 0,1,22,23")
    follow.add_argument("--include-dumps", action="store_true", help="торговать и DUMP (лонг)")
    follow.add_argument("--poll", type=int, default=30)
    follow.add_argument("--take-existing", type=int, default=0,
                        help="взять N последних сигналов при старте")
    follow.add_argument("--iterations", type=int, default=None)
    follow.add_argument("--out", default="data/follow")
    follow.set_defaults(func=_cmd_follow)

    bot = sub.add_parser("bot", help="Telegram-бот статистики")
    bot.add_argument("--token", help="токен бота (или MPF_TELEGRAM_TOKEN)")
    bot.add_argument("--chat-id", type=int, default=None, help="куда слать уведомления")
    bot.add_argument("--data-dir", default="data")
    bot.add_argument("--channel-url", default=DEFAULT_URL)
    bot.add_argument("--run", default="follow", help="какой прогон показывать по умолчанию")
    bot.add_argument("--equity", type=float, default=100.0)
    bot.add_argument("--backtest-data", default="data/history_5m.jsonl")
    bot.add_argument("--backtest-config", default="config/optimized_5m.json")
    bot.set_defaults(func=_cmd_bot)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())