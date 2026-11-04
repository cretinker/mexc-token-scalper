from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from app.config import settings
from app.mexc_client import mexc_client


@dataclass
class FilterResult:
    passed: bool
    score: int
    play_type: str
    reasons: List[str] = field(default_factory=list)
    reject_reasons: List[str] = field(default_factory=list)
    spread_pct: float = 0.0
    volume_mult: float = 1.0


class SafetyFilter:
    """
    Quality gate & anti-rug safety filter for MEXC micro-caps and penny tokens.
    Prevents buying into illiquid traps, fake wash trades, wide spreads,
    and exhausted parabolic blow-off tops.
    """

    def __init__(self):
        self.max_spread_pct = settings.MAX_SPREAD_PCT
        self.min_volume_usdt = settings.MIN_24H_TURNOVER_USDT
        self.max_1h_pct = settings.ANTI_CHASE_MAX_1H_PCT
        self.min_score_threshold = 60

    def evaluate_ticker(
        self,
        ticker: Dict[str, Any],
        play_type: str,
        volume_mult: float = 1.0,
        recent_1h_change: Optional[float] = None,
        book: Optional[Dict[str, float]] = None
    ) -> FilterResult:
        symbol = ticker.get("symbol", "")
        reject_reasons = []
        positive_factors = []
        score = 50  # Base score

        # 1. Price check
        price = float(ticker.get("lastPrice", 0.0))
        if price <= 0:
            return FilterResult(passed=False, score=0, play_type=play_type, reject_reasons=["Invalid zero price"])

        # 2. 24h Volume check
        vol_24h = float(ticker.get("quoteVolume", 0.0))
        if vol_24h < self.min_volume_usdt:
            reject_reasons.append(f"Insufficient 24h volume: ${vol_24h:,.0f} < ${self.min_volume_usdt:,.0f}")

        # 3. Spread check via book ticker
        if book is None:
            book = mexc_client.fetch_book_ticker(symbol)
        spread_pct = 0.0
        if book:
            spread_pct = book.get("spread_pct", 0.0)
            if spread_pct > self.max_spread_pct:
                reject_reasons.append(f"Spread too wide: {spread_pct:.2f}% > {self.max_spread_pct:.2f}%")
            elif spread_pct <= 0.5:
                score += 15
                positive_factors.append(f"Ultra tight spread: {spread_pct:.2f}%")
            elif spread_pct <= 1.2:
                score += 8
                positive_factors.append(f"Clean spread: {spread_pct:.2f}%")
        else:
            score -= 10

        # 4. Anti-Top Chasing (Parabolic Exhaustion Filter)
        # MEXC's Binance-compatible ticker already reports a percentage
        # (e.g. 12.5 means +12.5%), not a decimal return.
        change_24h = float(ticker.get("priceChangePercent", 0.0))
        if change_24h > 300.0:
            reject_reasons.append(f"Extreme 24h pump (+{change_24h:.1f}%): High risk of dump exhaustion")
        elif change_24h > 150.0:
            score -= 15
        elif 10.0 <= change_24h <= 60.0:
            score += 10
            positive_factors.append(f"Healthy early momentum (+{change_24h:.1f}%)")

        if recent_1h_change is not None and abs(recent_1h_change) > self.max_1h_pct:
            reject_reasons.append(f"Extreme 1h move ({recent_1h_change:+.1f}%): Anti-chase triggered")

        # 5. Playbook Specific Scoring
        if play_type == "SLEEPER_REVIVAL":
            if volume_mult >= 5.0:
                score += 25
                positive_factors.append(f"Massive dormancy breakout ({volume_mult:.1f}x volume)")
            elif volume_mult >= 3.0:
                score += 15
                positive_factors.append(f"Confirmed sleeper volume spike ({volume_mult:.1f}x volume)")
            else:
                score -= 10

        elif play_type == "PENNY_MOMENTUM":
            if price < 0.005:
                score += 15
                positive_factors.append("Deep sub-cent penny profile (< $0.005)")
            if vol_24h > 100000:
                score += 15
                positive_factors.append(f"Strong penny liquidity (${vol_24h:,.0f})")

        elif play_type == "NEW_LISTING":
            if vol_24h > 200000:
                score += 20
                positive_factors.append("Strong launch turnover")
            if spread_pct < 1.0:
                score += 10

        # Cap score between 0 and 100
        score = max(0, min(100, score))
        passed = len(reject_reasons) == 0 and score >= self.min_score_threshold

        return FilterResult(
            passed=passed,
            score=score,
            play_type=play_type,
            reasons=positive_factors,
            reject_reasons=reject_reasons,
            spread_pct=spread_pct,
            volume_mult=volume_mult
        )


safety_filter = SafetyFilter()
