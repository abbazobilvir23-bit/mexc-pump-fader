"""Тесты сигнального paper-раннера (фейковая биржа + фейковые сигналы)."""

from __future__ import annotations

from mpf.config import StrategyParams
from mpf.live.signal_runner import SignalFilter, SignalPaperConfig, SignalPaperTrader
from mpf.models import Candle, ExitReason
from mpf.signals.telegram import TelegramSignal

STEP = 300_000  # 5m


class _FakeExchange:
    def __init__(self, series: dict[str, list[Candle]], now: int) -> None:
        self.series = series
        self.now = now
        self.tickers: list = []

    def server_time_ms(self) -> int:
        return self.now

    def fetch_ohlcv(self, symbol: str, timeframe: str = "5m", limit: int = 200) -> list[Candle]:
        return self.series.get(symbol, [])[-limit:]

    def fetch_futures_tickers(self) -> list:
        return self.tickers


def _flat(n: int, price: float = 100.0) -> list[Candle]:
    return [
        Candle(ts=i * STEP, open=price, high=price, low=price, close=price, volume=1.0)
        for i in range(n)
    ]


def _signal(post_id: int, token: str, **kw: object) -> TelegramSignal:
    base: dict[str, object] = {
        "post_id": post_id, "token": token, "direction": "PUMP", "change_pct": 13.0,
        "rsi": 80.0, "rsi_ok": True, "atr_k": 5.0, "atr_ok": True, "adx": 20.0, "adx_ok": True,
        "prob_fp": 60, "prob_oi": 90, "density": 0.01, "exchange_ok": True,
    }
    base.update(kw)
    return TelegramSignal(**base)  # type: ignore[arg-type]


def _trader(tmp_path, series, incoming, now, params=None, **cfg_kw):  # type: ignore[no-untyped-def]
    exchange = _FakeExchange(series, now)
    config = SignalPaperConfig(
        params=params or StrategyParams(),
        trades_path=str(tmp_path / "t.jsonl"),
        state_path=str(tmp_path / "s.json"),
        **cfg_kw,
    )
    return SignalPaperTrader(exchange, config, fetch_signals_fn=lambda *a, **k: list(incoming))


def test_filter_accepts() -> None:
    f = SignalFilter(min_prob_fp=50, min_prob_oi=80, require_indicators_ok=True)
    assert f.accepts(_signal(1, "Q"))
    assert not f.accepts(_signal(2, "Q", direction="DUMP"))
    assert not f.accepts(_signal(3, "Q", prob_fp=10))
    assert not f.accepts(_signal(4, "Q", rsi_ok=False))
    assert not f.accepts(_signal(5, "Q", exchange_ok=False))


def test_first_poll_only_seeds(tmp_path) -> None:
    series = {"Q/USDT:USDT": _flat(16)}
    incoming = [_signal(1, "Q")]
    trader = _trader(tmp_path, series, incoming, now=16 * STEP)
    new = trader.poll_once()
    assert new == []
    assert trader.state.open_symbols == []
    assert trader.seen == {1}


def test_new_signal_opens_position(tmp_path) -> None:
    series = {"Q/USDT:USDT": _flat(16), "GP/USDT:USDT": _flat(16)}
    incoming = [_signal(1, "Q")]
    trader = _trader(tmp_path, series, incoming, now=16 * STEP)
    trader.poll_once()  # seed
    incoming.append(_signal(2, "GP"))
    trader.poll_once()
    assert "GP/USDT:USDT" in trader.state.open_symbols


def test_resume_loads_existing_trades(tmp_path) -> None:
    import json
    from dataclasses import asdict

    from mpf.models import ClosedTrade, Side

    trades_path = tmp_path / "trades.jsonl"
    trade = ClosedTrade(
        symbol="GP/USDT:USDT", side=Side.SHORT, entries=1, opened_ts=1, closed_ts=2,
        avg_entry=100.0, exit_price=98.0, qty=0.5, gross_pnl=1.0, fees=0.0,
        net_pnl=1.0, return_pct=2.0, exit_reason=ExitReason.TAKE_PROFIT,
    )
    trades_path.write_text(json.dumps(asdict(trade)) + "\n", encoding="utf-8")

    exchange = _FakeExchange({}, now=0)
    config = SignalPaperConfig(
        params=StrategyParams(), trades_path=str(trades_path), state_path=str(tmp_path / "s.json")
    )
    trader = SignalPaperTrader(exchange, config, fetch_signals_fn=lambda *a, **k: [])
    assert len(trader.state.trades) == 1
    assert trader.state.equity == 1001.0


def test_take_existing_opens_on_start(tmp_path) -> None:
    series = {"GP/USDT:USDT": _flat(16)}
    incoming = [_signal(1, "Q"), _signal(2, "GP")]
    trader = _trader(tmp_path, series, incoming, now=16 * STEP, take_existing=2)
    trader.poll_once()
    assert "GP/USDT:USDT" in trader.state.open_symbols


def test_hours_filter_blocks(tmp_path) -> None:
    # now = 16*STEP, STEP=5м -> час UTC = 1; разрешаем только час 0
    series = {"GP/USDT:USDT": _flat(16)}
    incoming = [_signal(1, "Q")]
    trader = _trader(
        tmp_path, series, incoming, now=16 * STEP,
        params=StrategyParams(trading_hours_utc=(0,)),
    )
    trader.poll_once()  # seed
    incoming.append(_signal(2, "GP"))
    trader.poll_once()
    assert "GP/USDT:USDT" not in trader.state.open_symbols


def test_funding_allows_positive(tmp_path) -> None:
    from mpf.models import Ticker

    series = {"GP/USDT:USDT": _flat(16)}
    incoming = [_signal(1, "Q")]
    trader = _trader(tmp_path, series, incoming, now=16 * STEP, min_funding_rate=0.0)
    trader.exchange.tickers.append(
        Ticker("GP/USDT:USDT", 100.0, 1000.0, 0.0, funding_rate=0.001)
    )
    trader.poll_once()  # seed
    incoming.append(_signal(2, "GP"))
    trader.poll_once()
    assert "GP/USDT:USDT" in trader.state.open_symbols


def test_funding_blocks_negative(tmp_path) -> None:
    from mpf.models import Ticker

    series = {"GP/USDT:USDT": _flat(16)}
    incoming = [_signal(1, "Q")]
    trader = _trader(tmp_path, series, incoming, now=16 * STEP, min_funding_rate=0.0)
    trader.exchange.tickers.append(
        Ticker("GP/USDT:USDT", 100.0, 1000.0, 0.0, funding_rate=-0.001)
    )
    trader.poll_once()  # seed
    incoming.append(_signal(2, "GP"))
    trader.poll_once()
    assert "GP/USDT:USDT" not in trader.state.open_symbols


def test_take_profit_closes_position(tmp_path) -> None:
    series = {"GP/USDT:USDT": _flat(16)}
    incoming = [_signal(1, "Q")]
    trader = _trader(tmp_path, series, incoming, now=16 * STEP)
    trader.poll_once()  # seed (Q has no candles, но это не важно)
    incoming.append(_signal(2, "GP"))
    trader.poll_once()  # вход по 100.0, тейк на 98.5
    assert "GP/USDT:USDT" in trader.state.open_symbols

    # цена уходит ниже тейка (98.5) на закрытой свече
    series["GP/USDT:USDT"].append(
        Candle(ts=16 * STEP, open=100.0, high=100.0, low=98.0, close=99.0, volume=1.0)
    )
    trader.exchange.now = 17 * STEP  # type: ignore[attr-defined]
    new = trader.poll_once()
    assert new, "позиция должна закрыться по тейку"
    assert new[0].exit_reason is ExitReason.TAKE_PROFIT
    assert trader.state.open_symbols == []