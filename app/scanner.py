import logging
import time
from typing import Dict, Any, List, Optional
from app.config import settings
from app.mexc_client import mexc_client
from app.safety_filter import safety_filter, FilterResult
from app.database import db

logger = logging.getLogger(__name__)


class MexcScanner:
    """
    Scans live MEXC Spot market for high-probability setups:
    1. SLEEPER_REVIVAL: Dormant tokens with sudden abnormal volume breakout.
    2. PENNY_MOMENTUM: Sub-cent micro-caps showing strong organic accumulation.
    3. NEW_LISTING: Recently listed tokens establishing support and breaking out.
    """

    def __init__(self):
        self.min_volume = settings.MIN_24H_TURNOVER_USDT
        self.penny_max_price = settings.PENNY_MAX_PRICE
        self._historical_volume_cache: Dict[str, float] = {}

    def scan_market(self) -> List[Dict[str, Any]]:
        """Run a full scan cycle across all MEXC spot tickers."""
        tickers = mexc_client.fetch_all_tickers()
        if not tickers:
            logger.warning("No tickers returned from MEXC.")
            return []

        # Batch fetch all book tickers once for ultra-fast spread checks
        book_map = mexc_client.fetch_all_book_tickers()

        # Compute benchmark BTC 24h change for Relative Strength (RS) scoring
        btc_ticker = next(
            (t for t in tickers if t.get("symbol") in ("BTCUSDT", "BTC_USDT")),
            None
        )
        btc_change_24h = float(btc_ticker.get("priceChangePercent", 0.0)) * 100.0 if btc_ticker else 0.0

        qualified_signals: List[Dict[str, Any]] = []

        # Filter active USDT pairs
        usdt_pairs = [
            t for t in tickers
            if t.get("symbol", "").endswith("USDT")
            and float(t.get("quoteVolume", 0.0)) >= self.min_volume
        ]

        logger.info(f"Scanning {len(usdt_pairs)} active USDT pairs on MEXC (BTC 24h: {btc_change_24h:+.2f}%)...")

        for t in usdt_pairs:
            symbol = t["symbol"]
            price = float(t.get("lastPrice", 0.0))
            vol_24h = float(t.get("quoteVolume", 0.0))
            change_24h = float(t.get("priceChangePercent", 0.0)) * 100.0

            # Exclude major anchors/stables and single-letter junk tokens
            base = symbol.replace("USDT", "")
            if base in {"BTC", "ETH", "USDC", "DAI", "EUR", "FDUSD", "TUSD"} or base.isdigit() or len(base) <= 1:
                continue

            # Check Playbook 1: Sleeper Revival (Volume anomaly relative to past 24h)
            vol_mult = self._estimate_volume_spike_multiple(symbol, vol_24h)
            is_sleeper = vol_mult >= settings.SLEEPER_VOL_SPIKE_MULT and (3.0 <= change_24h <= 45.0)

            # Check Playbook 2: Penny Momentum (< $0.05 with positive trend)
            is_penny = price <= self.penny_max_price and (8.0 <= change_24h <= 40.0)

            # Determine play type
            play_type = None
            if is_sleeper:
                play_type = "SLEEPER_REVIVAL"
            elif is_penny:
                play_type = "PENNY_MOMENTUM"
            elif change_24h > 15.0 and vol_24h > 150000:
                play_type = "NEW_LISTING"

            if not play_type:
                continue

            # Relative Strength (RS) vs BTC
            rs_score = round(change_24h - btc_change_24h, 2)

            # Evaluate with Safety & Anti-Rug Filter using pre-fetched book ticker
            filter_res: FilterResult = safety_filter.evaluate_ticker(
                ticker=t,
                play_type=play_type,
                volume_mult=vol_mult,
                book=book_map.get(symbol)
            )

            if filter_res.passed:
                signal_data = {
                    "symbol": symbol,
                    "play_type": play_type,
                    "score": filter_res.score,
                    "rs_score": rs_score,
                    "price": price,
                    "change_24h": round(change_24h, 2),
                    "volume_24h": round(vol_24h, 2),
                    "spike_multiple": round(vol_mult, 2),
                    "spread_pct": round(filter_res.spread_pct, 2),
                    "reason": "; ".join(filter_res.reasons),
                    "timestamp": time.time(),
                    "traded": False
                }
                qualified_signals.append(signal_data)
                db.record_signal(signal_data)

        # Sort signals prioritizing positive Relative Strength over BTC and high safety score
        qualified_signals.sort(
            key=lambda x: (x["rs_score"] > 0, x["score"] + min(x["rs_score"], 50.0), x["spike_multiple"]),
            reverse=True
        )
        return qualified_signals

    def _estimate_volume_spike_multiple(self, symbol: str, current_vol_24h: float) -> float:
        """
        Calculates sudden volume surge multiple compared to recent baseline.
        If cache is empty, uses estimated baseline from 24h distribution.
        """
        past_vol = self._historical_volume_cache.get(symbol)
        self._historical_volume_cache[symbol] = current_vol_24h

        if past_vol and past_vol > 0:
            diff = current_vol_24h - past_vol
            # If volume jumped significantly in this scan interval
            if diff > 0:
                return round(1.0 + (diff / (past_vol / 24.0)), 2)

        # Baseline heuristic: if high turnover relative to standard micro-cap
        if current_vol_24h > 100000.0:
            return 2.5
        return 1.0


scanner = MexcScanner()
