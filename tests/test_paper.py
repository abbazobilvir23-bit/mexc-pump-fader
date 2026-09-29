"""Тесты paper-trading раннера (с фейковой биржей)."""

from __future__ import annotations

from mpf.config import StrategyParams
from mpf.live.paper import PaperConfig, PaperTrader
from mpf.models import Candle

STEP = 60_000


class _FakeExchange:
    """Отдаёт последние `limit` свечей из изменяемого списка."""

    def __init__(self, series: dict[str, list[Candle]], now: int) -> None:
        self.series = series
        self.now = now

    def server_time_ms(self) -> int:
        return self.now

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1m", limit: int = 200) -> list[Candle]:
        return self.series.get(symbol, [])[-limit:]


def _flat(n: int) -> list[Candle]:
    return [
        Candle(ts=i * STEP, open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0)
        for i in range(n)
    ]


def test_warmup_primes_and_records_last_ts(tmp_path) -> None:
    series = {"X/USDT": _flat(16)}
    ex = _FakeExchange(series, now=16 * STEP)
    cfg = PaperConfig(
        symbols=["X/USDT"], params=StrategyParams(), warmup=10,
        trades_path=str(tmp_path / "t.jsonl"), state_path=str(tmp_path / "s.json"),
    )
    trader = PaperTrader(ex, cfg)
    assert trader.warmup() == 1
    # последние 10 свечей: ts 6..15
    assert trader.last_ts["X/USDT"] == 15 * STEP


def test_poll_processes_only_new_closed_candles(tmp_path) -> None:
    series = {"X/USDT": _flat(16)}
    ex = _FakeExchange(series, now=17 * STEP)
    cfg = PaperConfig(
        symbols=["X/USDT"], params=StrategyParams(), warmup=10,
        trades_path=str(tmp_path / "t.jsonl"), state_path=str(tmp_path / "s.json"),
    )
    trader = PaperTrader(ex, cfg)
    trader.warmup()  # last_ts = 15*STEP

    # новая свеча ts=16*STEP, время now=17*STEP -> она закрыта
    series["X/USDT"].append(
        Candle(ts=16 * STEP, open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0)
    )
    trader.poll_once(now_ms=17 * STEP)
    assert trader.last_ts["X/USDT"] == 16 * STEP
    assert (tmp_path / "s.json").exists()


def test_poll_ignores_unclosed_candle(tmp_path) -> None:
    series = {"X/USDT": _flat(16)}
    ex = _FakeExchange(series, now=16 * STEP + STEP // 2)
    cfg = PaperConfig(
        symbols=["X/USDT"], params=StrategyParams(), warmup=10,
        trades_path=str(tmp_path / "t.jsonl"), state_path=str(tmp_path / "s.json"),
    )
    trader = PaperTrader(ex, cfg)
    trader.warmup()
    series["X/USDT"].append(
        Candle(ts=16 * STEP, open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0)
    )
    # свеча 16*STEP ещё не закрыта (now < 17*STEP)
    trader.poll_once(now_ms=16 * STEP + STEP // 2)
    assert trader.last_ts["X/USDT"] == 15 * STEP


def test_run_loop_with_iterations(tmp_path) -> None:
    series = {"X/USDT": _flat(20)}
    ex = _FakeExchange(series, now=20 * STEP)
    cfg = PaperConfig(
        symbols=["X/USDT"], params=StrategyParams(), warmup=10,
        trades_path=str(tmp_path / "t.jsonl"), state_path=str(tmp_path / "s.json"),
    )
    trader = PaperTrader(ex, cfg)
    ticks: list[int] = []
    trader.run(poll_seconds=0, iterations=3, sleep=lambda _s: None, on_tick=lambda t, _n: ticks.append(t))
    assert ticks == [1, 2, 3]