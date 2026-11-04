"""Deterministic bull/bear adversarial gate for live entries.

This is intentionally not presented as an AI probability model.  It is a
reproducible evidence checklist inspired by StrategyLab's committee: the bull
case must be supported by trend, volume and relative strength while the bear
case can veto obvious exhaustion, adverse regime, or falling-knife setups.
"""

from dataclasses import dataclass
from typing import List, Optional

from app.market_context import MarketContext
from app.market_regime import RegimeDecision, direction_allowed


@dataclass(frozen=True)
class CommitteeDecision:
    passed: bool
    action: str
    margin_of_safety: float
    bull_conviction: float
    bear_conviction: float
    summary: str
    bull_arguments: List[str]
    bear_arguments: List[str]

    def reason(self) -> str:
        return (
            f"Committee {self.action} margin={self.margin_of_safety:+.1f} "
            f"bull={self.bull_conviction:.0f} bear={self.bear_conviction:.0f}: {self.summary}"
        )


def evaluate_setup(
    symbol: str,
    side: str,
    context: MarketContext,
    regime: RegimeDecision,
    relative_strength: float,
    spread_pct: float,
    stop_price: Optional[float] = None,
    target_price: Optional[float] = None,
) -> CommitteeDecision:
    side = side.upper()
    bull = 50.0
    bear = 50.0
    bull_args: List[str] = []
    bear_args: List[str] = []

    is_long = side == "LONG"
    trend_aligned = context.trend == ("BULLISH" if is_long else "BEARISH")
    if trend_aligned:
        bull += 20
        bull_args.append(f"closed-candle trend is {context.trend.lower()}")
    else:
        bear += 22
        bear_args.append(f"closed-candle trend is {context.trend.lower()}, not aligned")

    if context.volume_ratio >= 1.2:
        bull += 12
        bull_args.append(f"volume expansion {context.volume_ratio:.2f}x")
    elif context.volume_ratio < 0.75:
        bear += 12
        bear_args.append(f"weak volume {context.volume_ratio:.2f}x")

    if (is_long and 45.0 <= context.rsi14 <= 72.0) or (not is_long and 28.0 <= context.rsi14 <= 55.0):
        bull += 8
        bull_args.append(f"RSI {context.rsi14:.1f} is not terminal")
    elif (is_long and context.rsi14 > 76.0) or (not is_long and context.rsi14 < 24.0):
        bear += 25
        bear_args.append(f"terminal RSI {context.rsi14:.1f}")

    if relative_strength >= 3.0 if is_long else relative_strength <= -3.0:
        bull += 15
        bull_args.append(f"relative strength {relative_strength:+.2f}%")
    else:
        bear += 15
        bear_args.append(f"relative strength conflicts ({relative_strength:+.2f}%)")

    if spread_pct <= 0.8:
        bull += 5
    else:
        bear += 8
        bear_args.append(f"spread {spread_pct:.2f}% is costly")

    if is_long and context.candle_return_pct < -2.5:
        bear += 30
        bear_args.append(f"falling knife candle {context.candle_return_pct:+.2f}%")
    if not is_long and context.candle_return_pct > 2.5:
        bear += 30
        bear_args.append(f"vertical pump candle {context.candle_return_pct:+.2f}%")
    if (is_long and context.upper_wick_ratio > 0.45) or (not is_long and context.lower_wick_ratio > 0.45):
        bear += 22
        bear_args.append("large rejection wick")

    if not direction_allowed(side, regime, relative_strength):
        bear += 35
        bear_args.append(f"BTC firewall is {regime.firewall_mode}")

    if stop_price and target_price and abs(context.price - stop_price) > 0:
        rr = abs(target_price - context.price) / abs(context.price - stop_price)
        if rr >= 1.5:
            bull += 8
            bull_args.append(f"structural R:R {rr:.2f}")
        else:
            bear += 15
            bear_args.append(f"structural R:R only {rr:.2f}")

    margin = bull - bear
    hard_veto = (
        not direction_allowed(side, regime, relative_strength)
        or (is_long and context.candle_return_pct < -3.5)
        or (not is_long and context.candle_return_pct > 3.5)
        or ((is_long and context.upper_wick_ratio > 0.65) or (not is_long and context.lower_wick_ratio > 0.65))
    )
    passed = not hard_veto and margin >= 12.0
    if hard_veto:
        action = "VETO_BLOCK"
        summary = f"{symbol} failed a hard bear-case veto"
    elif passed and margin >= 28.0:
        action = "STRONG_PASS"
        summary = f"{symbol} has aligned trend, flow and relative strength"
    elif passed:
        action = "MODERATE_PASS"
        summary = f"{symbol} passes with a limited margin of safety"
    else:
        action = "DEADLOCK"
        summary = f"{symbol} has insufficient bull/bear separation"

    return CommitteeDecision(
        passed=passed,
        action=action,
        margin_of_safety=round(margin, 1),
        bull_conviction=round(max(0.0, min(100.0, bull)), 1),
        bear_conviction=round(max(0.0, min(100.0, bear)), 1),
        summary=summary,
        bull_arguments=bull_args,
        bear_arguments=bear_args,
    )
