"""Deterministic BTC regime firewall and relative-strength helpers."""

from dataclasses import dataclass
from typing import Any, Dict, List

from app.market_context import build_context, closed_candles


@dataclass(frozen=True)
class RegimeDecision:
    bias: str
    firewall_mode: str
    confidence: float
    risk_budget_pct: float
    reasons: List[str]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "bias": self.bias,
            "firewall_mode": self.firewall_mode,
            "confidence": self.confidence,
            "risk_budget_pct": self.risk_budget_pct,
            "reasons": self.reasons,
        }


def compute_btc_regime(candles: List[Dict[str, Any]]) -> RegimeDecision | None:
    """Vote across trend and multiple closed-candle return horizons."""
    context = build_context(candles, minimum_bars=60)
    if context is None:
        return None

    values = [float(c["close"]) for c in closed_candles(candles) if float(c.get("close", 0.0)) > 0]
    ret24 = ((values[-1] / values[-25]) - 1.0) * 100.0 if len(values) >= 25 else 0.0
    ret72 = ((values[-1] / values[-73]) - 1.0) * 100.0 if len(values) >= 73 else 0.0
    votes = [
        1 if context.trend == "BULLISH" else -1 if context.trend == "BEARISH" else 0,
        1 if ret24 > 0.25 else -1 if ret24 < -0.25 else 0,
        1 if ret72 > 0.50 else -1 if ret72 < -0.50 else 0,
    ]
    bulls = sum(v > 0 for v in votes)
    bears = sum(v < 0 for v in votes)
    if bulls >= 2 and bulls > bears:
        bias, mode, risk = "BULLISH", "LONG_ONLY", 1.0
    elif bears >= 2 and bears > bulls:
        bias, mode, risk = "BEARISH", "SHORT_ONLY", 0.5
    else:
        bias, mode, risk = "CHOP", "DEFENSIVE_CHOP", 0.35

    confidence = abs(bulls - bears) / len(votes)
    return RegimeDecision(
        bias=bias,
        firewall_mode=mode,
        confidence=round(confidence, 2),
        risk_budget_pct=risk,
        reasons=[
            f"BTC trend={context.trend}",
            f"BTC return24={ret24:+.2f}%",
            f"BTC return72={ret72:+.2f}%",
            f"BTC ATR={context.atr_pct:.2f}%",
        ],
    )


def direction_allowed(
    side: str,
    regime: RegimeDecision,
    relative_strength: float,
    min_rs_in_chop_pct: float = 5.0,
) -> bool:
    """Apply the firewall without forcing trades during ambiguous chop."""
    side = side.upper()
    if side == "LONG" and regime.bias == "BEARISH":
        return False
    if side == "SHORT" and regime.bias == "BULLISH":
        return False
    if regime.bias == "CHOP" and relative_strength < min_rs_in_chop_pct:
        return False
    return True
