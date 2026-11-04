import logging
import statistics
from typing import Dict, Any, List, Optional
from app.mexc_client import mexc_client

logger = logging.getLogger(__name__)


class DOMEngine:
    """
    Depth of Market (DOM) & Order Book Liquidity Engine.
    Analyzes live Level-2 order book depth to:
      1. Detect Ask Walls (Resistance Ceilings) to front-run for Take Profit.
      2. Detect Bid Walls (Support Floors) to anchor Stop Losses behind institutional shields.
      3. Identify Passive Absorption and Liquidity Depletion (Bid Vacuum / Ask Vacuum).
      4. Provide dynamic trailing stops pegged to real order book support blocks.
    """

    def __init__(self):
        self.min_block_notional_usdt = 250.0  # Minimum USDT value to qualify as a micro-cap block
        self.wall_multiple = 2.5              # Must be >= 2.5x median level notional

    def analyze_orderbook(self, symbol: str, current_price: float, side: str = "LONG") -> Dict[str, Any]:
        """
        Fetches and analyzes the top 50 order book levels for a given symbol.
        Returns dynamic TP, dynamic SL, book imbalance ratio, and exhaustion flags.
        """
        if current_price <= 0:
            return {}

        depth = mexc_client.fetch_orderbook(symbol, limit=50)
        if not depth or "bids" not in depth or "asks" not in depth:
            return {}

        bids = depth["bids"]
        asks = depth["asks"]

        if not bids or not asks:
            return {}

        # Parse notional values: (price, qty, notional)
        bids_parsed = [(p, q, p * q) for p, q in bids if p > 0 and q > 0]
        asks_parsed = [(p, q, p * q) for p, q in asks if p > 0 and q > 0]

        if not bids_parsed or not asks_parsed:
            return {}

        # Calculate median notionals across visible levels
        bid_notionals = [n for _, _, n in bids_parsed]
        ask_notionals = [n for _, _, n in asks_parsed]
        median_bid = statistics.median(bid_notionals) if bid_notionals else 100.0
        median_ask = statistics.median(ask_notionals) if ask_notionals else 100.0

        # Depth sums in top 15 levels
        top15_bid_depth = sum(n for _, _, n in bids_parsed[:15])
        top15_ask_depth = sum(n for _, _, n in asks_parsed[:15])
        book_ratio = top15_bid_depth / top15_ask_depth if top15_ask_depth > 0 else 1.0

        result: Dict[str, Any] = {
            "book_ratio": round(book_ratio, 2),
            "top15_bid_depth": round(top15_bid_depth, 2),
            "top15_ask_depth": round(top15_ask_depth, 2),
            "bid_walls": [],
            "ask_walls": [],
            "dynamic_tp": None,
            "dynamic_sl": None,
            "bid_depleted": False,
            "ask_depleted": False,
            "absorption_exhaustion": False,
        }

        # ── Identify Significant Ask Walls (Resistance) ──
        ask_threshold = max(self.min_block_notional_usdt, median_ask * self.wall_multiple)
        for p, q, n in asks_parsed:
            if n >= ask_threshold and p > current_price:
                dist_pct = ((p - current_price) / current_price) * 100.0
                if 0.5 <= dist_pct <= 20.0:
                    result["ask_walls"].append({
                        "price": p,
                        "notional": round(n, 2),
                        "distance_pct": round(dist_pct, 2)
                    })

        # ── Identify Significant Bid Walls (Support) ──
        bid_threshold = max(self.min_block_notional_usdt, median_bid * self.wall_multiple)
        for p, q, n in bids_parsed:
            if n >= bid_threshold and p < current_price:
                dist_pct = ((current_price - p) / current_price) * 100.0
                if 0.5 <= dist_pct <= 20.0:
                    result["bid_walls"].append({
                        "price": p,
                        "notional": round(n, 2),
                        "distance_pct": round(dist_pct, 2)
                    })

        # ── Strategy Exits for LONG Positions ──
        if side == "LONG":
            # 1. Dynamic Take Profit: Front-run the nearest major Ask Wall between +2.5% and +15%
            valid_ask_walls = [w for w in result["ask_walls"] if 2.5 <= w["distance_pct"] <= 15.0]
            if valid_ask_walls:
                # Pick the nearest qualifying wall
                wall = min(valid_ask_walls, key=lambda w: w["distance_pct"])
                # Place TP 0.15% in front of the wall
                result["dynamic_tp"] = wall["price"] * 0.9985

            # 2. Dynamic Stop Loss: Anchor behind the nearest major Bid Wall between -1.0% and -4.5%
            valid_bid_walls = [w for w in result["bid_walls"] if 1.0 <= w["distance_pct"] <= 4.5]
            if valid_bid_walls:
                wall = min(valid_bid_walls, key=lambda w: w["distance_pct"])
                # Place SL 0.20% behind the wall
                result["dynamic_sl"] = wall["price"] * 0.9980

            # 3. Bid Depletion: Bids vanishing (< 0.22 ratio and low depth)
            if book_ratio < 0.22 and top15_bid_depth < 3000.0:
                result["bid_depleted"] = True

            # 4. Absorption Exhaustion: Price stalled right under a massive Ask Wall with skewed book
            nearby_ask_wall = any(w["distance_pct"] <= 1.2 for w in result["ask_walls"])
            if nearby_ask_wall and book_ratio < 0.35:
                result["absorption_exhaustion"] = True

        # ── Strategy Exits for SHORT Positions ──
        elif side == "SHORT":
            # 1. Dynamic Take Profit: Front-run the nearest major Bid Support Wall below
            valid_bid_walls = [w for w in result["bid_walls"] if 2.5 <= w["distance_pct"] <= 15.0]
            if valid_bid_walls:
                wall = min(valid_bid_walls, key=lambda w: w["distance_pct"])
                # Place TP for short 0.15% above the bid wall
                result["dynamic_tp"] = wall["price"] * 1.0015

            # 2. Dynamic Stop Loss: Anchor behind the nearest Ask Wall above
            valid_ask_walls = [w for w in result["ask_walls"] if 1.0 <= w["distance_pct"] <= 4.5]
            if valid_ask_walls:
                wall = min(valid_ask_walls, key=lambda w: w["distance_pct"])
                # Place SL for short 0.20% above the ask wall
                result["dynamic_sl"] = wall["price"] * 1.0020

            # 3. Ask Depletion (Short squeeze risk: asks vanished)
            if book_ratio > 4.5 and top15_ask_depth < 3000.0:
                result["ask_depleted"] = True

            # 4. Absorption Exhaustion: Short stalled right above a massive Bid Wall
            nearby_bid_wall = any(w["distance_pct"] <= 1.2 for w in result["bid_walls"])
            if nearby_bid_wall and book_ratio > 2.8:
                result["absorption_exhaustion"] = True

        return result


dom_engine = DOMEngine()
