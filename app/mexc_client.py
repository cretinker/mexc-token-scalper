import logging
import time
from typing import Any, Dict, List, Optional
import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class MexcClient:
    """
    Resilient MEXC Spot Market Data Client.
    Automatically rotates through primary and mirror domains (api.mexc.co, api.mexc.io)
    to guarantee reliable real-time access.
    """

    def __init__(self):
        self.endpoints = settings.MEXC_FALLBACK_URLS
        self.current_endpoint_idx = 0
        self.timeout = 15.0
        self._kline_cache: Dict[tuple[str, str, int], tuple[float, List[Dict[str, Any]]]] = {}

    @property
    def base_url(self) -> str:
        return self.endpoints[self.current_endpoint_idx]

    def _rotate_endpoint(self):
        self.current_endpoint_idx = (self.current_endpoint_idx + 1) % len(self.endpoints)
        logger.warning(f"Rotating to alternative MEXC endpoint: {self.base_url}")

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Any]:
        """HTTP GET with automatic retry across mirror endpoints."""
        for attempt in range(len(self.endpoints)):
            url = f"{self.base_url}{path}"
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.get(url, params=params)
                    if resp.status_code == 200:
                        return resp.json()
                    else:
                        logger.warning(f"MEXC error {resp.status_code} at {url}: {resp.text[:100]}")
            except Exception as e:
                logger.warning(f"MEXC request failed on {url}: {type(e).__name__} - {e}")
                self._rotate_endpoint()
                time.sleep(0.5)

        logger.error(f"All MEXC endpoints failed for path: {path}")
        return None

    def fetch_all_tickers(self) -> List[Dict[str, Any]]:
        """
        Fetch 24h ticker price change statistics for all spot pairs.
        Endpoint: /api/v3/ticker/24hr
        """
        data = self.get("/api/v3/ticker/24hr")
        if isinstance(data, list):
            return data
        return []

    def fetch_ticker_price(self, symbol: str) -> Optional[float]:
        """Fetch latest price for a single symbol."""
        data = self.get("/api/v3/ticker/price", params={"symbol": symbol})
        if data and "price" in data:
            try:
                return float(data["price"])
            except (ValueError, TypeError):
                return None
        return None

    def fetch_all_book_tickers(self) -> Dict[str, Dict[str, float]]:
        """
        Fetch all book tickers in a single batch request.
        Returns mapping: {symbol: {'bid': float, 'ask': float, 'spread_pct': float}}
        """
        data = self.get("/api/v3/ticker/bookTicker")
        if not data or not isinstance(data, list):
            return {}

        result = {}
        for item in data:
            sym = item.get("symbol")
            if not sym:
                continue
            try:
                bid = float(item.get("bidPrice", 0.0))
                ask = float(item.get("askPrice", 0.0))
                spread = ((ask - bid) / ask) * 100.0 if ask > 0 else 0.0
                result[sym] = {
                    "bid": bid,
                    "ask": ask,
                    "bidQty": float(item.get("bidQty", 0.0)),
                    "askQty": float(item.get("askQty", 0.0)),
                    "spread_pct": spread,
                }
            except (ValueError, TypeError):
                continue
        return result

    def fetch_book_ticker(self, symbol: str) -> Optional[Dict[str, float]]:
        """
        Fetch best bid and ask price/quantity for spread verification.
        Endpoint: /api/v3/ticker/bookTicker
        """
        data = self.get("/api/v3/ticker/bookTicker", params={"symbol": symbol})
        if data and "bidPrice" in data and "askPrice" in data:
            try:
                bid = float(data["bidPrice"])
                ask = float(data["askPrice"])
                spread = ((ask - bid) / ask) * 100.0 if ask > 0 else 0.0
                return {
                    "bid": bid,
                    "ask": ask,
                    "bidQty": float(data.get("bidQty", 0.0)),
                    "askQty": float(data.get("askQty", 0.0)),
                    "spread_pct": spread,
                }
            except (ValueError, TypeError):
                return None
        return None

    def fetch_orderbook(self, symbol: str, limit: int = 20) -> Optional[Dict[str, Any]]:
        """
        Fetch order book depth.
        Endpoint: /api/v3/depth
        """
        data = self.get("/api/v3/depth", params={"symbol": symbol, "limit": limit})
        if data and "bids" in data and "asks" in data:
            return {
                "bids": [[float(p), float(q)] for p, q in data["bids"]],
                "asks": [[float(p), float(q)] for p, q in data["asks"]],
            }
        return None

    def fetch_klines(
        self,
        symbol: str,
        interval: str = "15m",
        limit: int = 100,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch candlestick / klines data.
        Intervals: 1m, 5m, 15m, 30m, 60m, 4h, 1d
        Endpoint: /api/v3/klines
        Returns parsed list of dicts: [time, open, high, low, close, volume, quote_volume]
        """
        cache_key = (symbol, interval, limit)
        cached = self._kline_cache.get(cache_key)
        # Keep scanner confirmations bounded to one normal scan loop while
        # avoiding a fresh HTTP client for every candidate on every pass.
        if cached and time.time() - cached[0] < 30.0:
            return cached[1]

        params: Dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time

        data = self.get("/api/v3/klines", params=params)
        if not data or not isinstance(data, list):
            return []

        candles = []
        for row in data:
            try:
                # [openTime, open, high, low, close, vol, closeTime, quoteVol]
                candles.append(
                    {
                        "time": int(row[0]),
                        "open": float(row[1]),
                        "high": float(row[2]),
                        "low": float(row[3]),
                        "close": float(row[4]),
                        "volume": float(row[5]),
                        "close_time": int(row[6]),
                        "quote_volume": float(row[7]),
                    }
                )
            except (IndexError, ValueError, TypeError):
                continue
        self._kline_cache[cache_key] = (time.time(), candles)
        return candles


mexc_client = MexcClient()
