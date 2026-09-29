"""Адаптер MEXC Futures через ccxt.

ccxt импортируется лениво: ядро, бэктест и тесты не требуют сети/ccxt.
Установка: pip install -e ".[exchange]"
"""

from __future__ import annotations

from mpf.models import Candle, Ticker

# Таймфреймы MEXC, которые нас интересуют для детекта пампа.
DEFAULT_TIMEFRAME = "1m"


class MexcExchange:
    """Обёртка над ccxt.mexc для USDT-M фьючерсов."""

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        *,
        enable_rate_limit: bool = True,
    ) -> None:
        try:
            import ccxt
        except ImportError as exc:  # pragma: no cover - зависит от окружения
            raise RuntimeError(
                "ccxt не установлен. Выполни: pip install -e \".[exchange]\""
            ) from exc

        self._client = ccxt.mexc(
            {
                "apiKey": api_key or "",
                "secret": api_secret or "",
                "enableRateLimit": enable_rate_limit,
                "options": {"defaultType": "swap"},
            }
        )
        # Отложенная загрузка рынков: методы обращаются к self._ensure_markets().
        self._markets_loaded = False

    def _ensure_markets(self) -> None:
        if not self._markets_loaded:
            self._client.load_markets()
            self._markets_loaded = True

    def fetch_futures_tickers(self) -> list[Ticker]:
        """Все swap-пары с объёмом за 24ч. Использует bulk-эндпоинт ccxt."""
        self._ensure_markets()
        raw = self._client.fetch_tickers()
        out: list[Ticker] = []
        for symbol, data in raw.items():
            market = self._client.markets.get(symbol)
            if not market or not market.get("swap"):
                continue
            last = _to_float(data.get("last")) or _to_float(data.get("close"))
            if last is None or last <= 0:
                continue
            quote_volume = _to_float(data.get("quoteVolume")) or 0.0
            change = _to_float(data.get("percentage"))
            if change is None:
                change = _pct_change(_to_float(data.get("open")), last)
            out.append(
                Ticker(
                    symbol=symbol,
                    last=last,
                    quote_volume_24h=quote_volume,
                    change_pct_24h=change,
                    bid=_to_float(data.get("bid")) or 0.0,
                    ask=_to_float(data.get("ask")) or 0.0,
                    open_interest=_to_float(data.get("info", {}).get("holdVol")) or 0.0,
                    funding_rate=_to_float(data.get("info", {}).get("fundingRate")) or 0.0,
                )
            )
        return out

    def fetch_ohlcv(
        self, symbol: str, timeframe: str = DEFAULT_TIMEFRAME, limit: int = 200
    ) -> list[Candle]:
        rows = self._client.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        return [_row_to_candle(row) for row in rows]

    def fetch_ohlcv_since(
        self, symbol: str, timeframe: str, since: int, limit: int = 1000
    ) -> list[Candle]:
        rows = self._client.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit)
        return [_row_to_candle(row) for row in rows]

    def server_time_ms(self) -> int:
        return int(self._client.milliseconds())

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()


def _row_to_candle(row: list[float]) -> Candle:
    return Candle(
        ts=int(row[0]),
        open=float(row[1]),
        high=float(row[2]),
        low=float(row[3]),
        close=float(row[4]),
        volume=float(row[5]),
    )


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _pct_change(open_price: float | None, last: float) -> float:
    if open_price is None or open_price <= 0:
        return 0.0
    return (last / open_price - 1.0) * 100.0