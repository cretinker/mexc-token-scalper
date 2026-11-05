import logging
import time
from typing import Dict, Any, List, Optional
from app.config import settings
from app.mexc_client import mexc_client
from app.safety_filter import safety_filter, FilterResult
from app.database import db
from app.market_context import build_context
from app.market_regime import compute_btc_regime, direction_allowed
from app.committee import evaluate_setup

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

        # Compute benchmark BTC 24h change for Relative Strength (RS) scoring.
        # MEXC already returns percentage points here; do not multiply by 100.
        btc_ticker = next(
            (t for t in tickers if t.get("symbol") in ("BTCUSDT", "BTC_USDT")),
            None
        )
        btc_change_24h = float(btc_ticker.get("priceChangePercent", 0.0)) if btc_ticker else 0.0

        btc_candles = mexc_client.fetch_klines("BTCUSDT", interval="60m", limit=100)
        btc_regime = compute_btc_regime(btc_candles)
        if settings.REQUIRE_BTC_REGIME and btc_regime is None:
            logger.warning("BTC regime unavailable; standing down rather than trading blind.")
            return []

        preliminary: List[Dict[str, Any]] = []

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
            change_24h = float(t.get("priceChangePercent", 0.0))

            # Exclude major anchors/stables and single-letter junk tokens
            base = symbol.replace("USDT", "")
            if base in {"BTC", "ETH", "USDC", "DAI", "EUR", "FDUSD", "TUSD"} or base.isdigit() or len(base) <= 1:
                continue

            # Check if symbol is in post-exit cooldown
            in_cooldown, rem_sec = db.is_in_cooldown(symbol)
            if in_cooldown:
                continue

            # High price and low price from 24h ticker
            high_24h = float(t.get("highPrice", 0.0))

            # Check Playbook 1: Sleeper Revival (Volume anomaly relative to past 24h)
            vol_mult = self._estimate_volume_spike_multiple(symbol, vol_24h)
            is_sleeper = vol_mult >= settings.SLEEPER_VOL_SPIKE_MULT and (3.0 <= change_24h <= 45.0)

            # Check Playbook 2: Penny Momentum (< $0.05 with positive trend)
            is_penny = price <= self.penny_max_price and (8.0 <= change_24h <= 40.0)

            # Check Playbook 3: New Listing momentum
            is_new_listing = settings.ENABLE_NEW_LISTING and change_24h > 15.0 and vol_24h > 150000

            # Check Playbook 4: Exhaustion Short (Overextended parabolic pump dropping from 24h peak)
            is_exhaustion_short = settings.ENABLE_SHORTS and (
                change_24h >= 25.0
                and vol_24h >= 40000.0
                and high_24h > 0
                and price <= high_24h * 0.94
                and price >= high_24h * 0.82
            )

            # Determine play type and side
            play_type = None
            side = "LONG"

            if is_exhaustion_short:
                play_type = "EXHAUSTION_SHORT"
                side = "SHORT"
            elif is_sleeper:
                play_type = "SLEEPER_REVIVAL"
                side = "LONG"
            elif is_penny:
                play_type = "PENNY_MOMENTUM"
                side = "LONG"
            elif is_new_listing:
                play_type = "NEW_LISTING"
                side = "LONG"

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

            if filter_res.passed and filter_res.score >= settings.MIN_SIGNAL_SCORE:
                preliminary.append({
                    "symbol": symbol,
                    "side": side,
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
                    "traded": False,
                    "filter_result": filter_res,
                })
        # Sort cheap candidates first, then spend network calls only on the best
        # few. This prevents a full-market scan from creating a burst of stale
        # entries and keeps the 45-second loop bounded.
        preliminary.sort(
            key=lambda x: (x["rs_score"] > 0, x["score"] + min(x["rs_score"], 50.0), x["spike_multiple"]),
            reverse=True
        )

        qualified_signals: List[Dict[str, Any]] = []
        for candidate in preliminary[: settings.MAX_DEEP_CANDIDATES]:
            symbol = candidate["symbol"]
            side = candidate["side"]
            if btc_regime is None:
                continue
            if not direction_allowed(
                side,
                btc_regime,
                candidate["rs_score"],
                settings.MIN_RS_IN_CHOP_PCT,
            ):
                continue

            candles = mexc_client.fetch_klines(symbol, interval="15m", limit=80)
            context = build_context(candles)
            if context is None:
                continue

            final_filter = safety_filter.evaluate_ticker(
                ticker=next((item for item in usdt_pairs if item.get("symbol") == symbol), {}),
                play_type=candidate["play_type"],
                volume_mult=candidate["spike_multiple"],
                recent_1h_change=context.return_1h_pct,
                book=book_map.get(symbol),
            )
            if not final_filter.passed or final_filter.score < settings.MIN_SIGNAL_SCORE:
                continue

            stop = context.price * (1.0 - settings.STOP_LOSS_PCT / 100.0) if side == "LONG" else context.price * (1.0 + settings.STOP_LOSS_PCT / 100.0)
            target = context.price * (1.0 + settings.TAKE_PROFIT_1_PCT / 100.0) if side == "LONG" else context.price * (1.0 - settings.TAKE_PROFIT_1_PCT / 100.0)
            committee = evaluate_setup(
                symbol=symbol,
                side=side,
                context=context,
                regime=btc_regime,
                relative_strength=candidate["rs_score"],
                spread_pct=candidate["spread_pct"],
                stop_price=stop,
                target_price=target,
            )
            if not committee.passed:
                continue

            signal_data = {k: v for k, v in candidate.items() if k != "filter_result"}
            signal_data.update({
                "score": final_filter.score,
                "change_24h": round(candidate["change_24h"], 2),
                "recent_1h_change": round(context.return_1h_pct, 2),
                "trend": context.trend,
                "rsi14": round(context.rsi14, 2),
                "volume_ratio": round(context.volume_ratio, 2),
                "btc_regime": btc_regime.bias if btc_regime else "UNKNOWN",
                "regime_risk_multiplier": btc_regime.risk_budget_pct,
                "committee_action": committee.action,
                "committee_margin": committee.margin_of_safety,
                "committee_passed": True,
                "reason": "; ".join([
                    *final_filter.reasons,
                    *(btc_regime.reasons if btc_regime else []),
                    committee.reason(),
                ]),
            })
            qualified_signals.append(signal_data)
            db.record_signal(signal_data)

        qualified_signals.sort(
            key=lambda x: (x["committee_margin"], x["score"], x["rs_score"]),
            reverse=True,
        )
        return qualified_signals[: settings.MAX_ENTRIES_PER_SCAN * 2]

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
