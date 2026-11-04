"""Point-in-time candle features used by the live scanner and committee.

The functions in this module are deliberately pure: they only inspect candles
that have already closed.  This keeps the live confirmation path easy to test
and prevents the backtest/live implementations from quietly disagreeing about
indicator inputs.
"""

from dataclasses import dataclass
import time
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class MarketContext:
    price: float
    ema20: float
    ema50: float
    rsi14: float
    atr14: float
    atr_pct: float
    volume_ratio: float
    return_1h_pct: float
    return_4h_pct: float
    candle_return_pct: float
    upper_wick_ratio: float
    lower_wick_ratio: float
    trend: str


def _ema(values: List[float], period: int) -> float:
    if not values:
        return 0.0
    alpha = 2.0 / (period + 1.0)
    value = values[0]
    for item in values[1:]:
        value = alpha * item + (1.0 - alpha) * value
    return value


def _rsi(values: List[float], period: int = 14) -> float:
    if len(values) < 2:
        return 50.0
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    window = changes[-period:]
    gains = sum(max(change, 0.0) for change in window) / max(len(window), 1)
    losses = sum(max(-change, 0.0) for change in window) / max(len(window), 1)
    if losses <= 1e-12:
        return 100.0 if gains > 0 else 50.0
    return 100.0 - (100.0 / (1.0 + gains / losses))


def _atr(candles: List[Dict[str, Any]], period: int = 14) -> float:
    if len(candles) < 2:
        return 0.0
    true_ranges = []
    for idx in range(1, len(candles)):
        bar = candles[idx]
        previous_close = float(candles[idx - 1]["close"])
        true_ranges.append(max(
            float(bar["high"]) - float(bar["low"]),
            abs(float(bar["high"]) - previous_close),
            abs(float(bar["low"]) - previous_close),
        ))
    window = true_ranges[-period:]
    return sum(window) / max(len(window), 1)


def _return_pct(values: List[float], bars: int) -> float:
    if len(values) <= bars or values[-bars - 1] <= 0:
        return 0.0
    return (values[-1] / values[-bars - 1] - 1.0) * 100.0


def closed_candles(candles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return candles whose close time has passed, when timestamps are known."""
    if not candles:
        return []
    last_close_time = candles[-1].get("close_time")
    if last_close_time is not None:
        try:
            # MEXC timestamps are milliseconds since epoch.
            if float(last_close_time) > time.time() * 1000.0:
                return candles[:-1]
        except (TypeError, ValueError):
            pass
    return candles


def build_context(candles: List[Dict[str, Any]], minimum_bars: int = 30) -> Optional[MarketContext]:
    """Build features from closed candles only.

    MEXC returns the currently forming candle as the last row.  It is removed
    when possible; using a moving candle for confirmation was a major source of
    noisy entries in the original scanner.
    """
    closed = closed_candles(candles)
    if len(closed) < minimum_bars:
        return None

    closes = [float(c["close"]) for c in closed]
    price = closes[-1]
    if price <= 0:
        return None

    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    atr14 = _atr(closed)
    last = closed[-1]
    open_price = float(last["open"])
    high = float(last["high"])
    low = float(last["low"])
    bar_range = max(high - low, price * 1e-9)
    body_top = max(open_price, price)
    body_bottom = min(open_price, price)
    previous_volumes = [float(c.get("volume", 0.0)) for c in closed[-21:-1]]
    mean_volume = sum(previous_volumes) / max(len(previous_volumes), 1)
    volume_ratio = float(last.get("volume", 0.0)) / max(mean_volume, 1e-9)

    if price > ema20 and ema20 >= ema50:
        trend = "BULLISH"
    elif price < ema20 and ema20 <= ema50:
        trend = "BEARISH"
    else:
        trend = "CHOP"

    return MarketContext(
        price=price,
        ema20=ema20,
        ema50=ema50,
        rsi14=_rsi(closes),
        atr14=atr14,
        atr_pct=(atr14 / price) * 100.0 if price > 0 else 0.0,
        volume_ratio=volume_ratio,
        return_1h_pct=_return_pct(closes, 4),
        return_4h_pct=_return_pct(closes, 16),
        candle_return_pct=((price - open_price) / open_price) * 100.0 if open_price > 0 else 0.0,
        upper_wick_ratio=(high - body_top) / bar_range,
        lower_wick_ratio=(body_bottom - low) / bar_range,
        trend=trend,
    )
