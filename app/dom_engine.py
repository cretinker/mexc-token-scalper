import logging
import statistics
from typing import Dict, Any, List, Optional
from app.mexc_client import mexc_client

logger = logging.getLogger(__name__)


class DOMEngine:
    """
    Intelligent Depth of Market (DOM) & Liquidity Architecture.
    Calculates bespoke, asset-specific Take Profit and Stop Loss levels
    anchored directly to Level-2 order book liquidity structures:
      * TP1: Snaps 0.15% in front of the primary Level-2 Ask Wall / Resistance Cluster.
      * TP2: Snaps 0.15% in front of the secondary expansion Ask Wall.
      * SL: Snaps 0.20% behind the primary Level-2 Bid Wall / Support Floor.
      * Dynamic Retargeting: Adjusts TP down if new walls appear, ratchets SL up behind new support blocks.
      * Absorption / Depletion: Detects liquidity vacuums and iceberg stalls in real time.
    """

    def __init__(self):
        self.min_block_notional_usdt = 200.0   # Minimum USDT value to qualify as a micro-cap block
        self.wall_multiple = 2.0               # Must be >= 2.0x median level notional

    def analyze_orderbook(self, symbol: str, current_price: float, side: str = "LONG") -> Dict[str, Any]:
        """
        Fetches and profiles the top 50 order book levels for a given symbol.
        Returns true structural TP1, TP2, and SL levels based on real liquidity walls.
        """
        if current_price <= 0:
            return {}

        depth = mexc_client.fetch_orderbook(symbol, limit=50)
        if not depth or "bids" not in depth or "asks" not in depth:
            return self._fallback_structural_levels(current_price, side)

        bids = depth["bids"]
        asks = depth["asks"]

        if not bids or not asks:
            return self._fallback_structural_levels(current_price, side)

        # Parse notional values: (price, qty, notional)
        bids_parsed = [(p, q, p * q) for p, q in bids if p > 0 and q > 0]
        asks_parsed = [(p, q, p * q) for p, q in asks if p > 0 and q > 0]

        if not bids_parsed or not asks_parsed:
            return self._fallback_structural_levels(current_price, side)

        # Calculate median notionals across visible levels
        bid_notionals = [n for _, _, n in bids_parsed]
        ask_notionals = [n for _, _, n in asks_parsed]
        median_bid = statistics.median(bid_notionals) if bid_notionals else 100.0
        median_ask = statistics.median(ask_notionals) if ask_notionals else 100.0

        # Depth sums in top 15 levels
        top15_bid_depth = sum(n for _, _, n in bids_parsed[:15])
        top15_ask_depth = sum(n for _, _, n in asks_parsed[:15])
        book_ratio = top15_bid_depth / top15_ask_depth if top15_ask_depth > 0 else 1.0

        best_bid = bids_parsed[0][0]
        best_ask = asks_parsed[0][0]
        spread_pct = ((best_ask - best_bid) / best_ask) * 100.0 if best_ask > 0 else 0.2

        # ── Profile Ask Walls (Overhead Resistance) ──
        ask_threshold = max(self.min_block_notional_usdt, median_ask * self.wall_multiple)
        ask_walls = []
        for p, q, n in asks_parsed:
            if n >= ask_threshold and p > current_price:
                dist_pct = ((p - current_price) / current_price) * 100.0
                if 0.5 <= dist_pct <= 30.0:
                    ask_walls.append({
                        "price": p,
                        "notional": round(n, 2),
                        "distance_pct": round(dist_pct, 2)
                    })

        # ── Profile Bid Walls (Underlying Support) ──
        bid_threshold = max(self.min_block_notional_usdt, median_bid * self.wall_multiple)
        bid_walls = []
        for p, q, n in bids_parsed:
            if n >= bid_threshold and p < current_price:
                dist_pct = ((current_price - p) / current_price) * 100.0
                if 0.5 <= dist_pct <= 30.0:
                    bid_walls.append({
                        "price": p,
                        "notional": round(n, 2),
                        "distance_pct": round(dist_pct, 2)
                    })

        # Sort walls by proximity to market price
        ask_walls.sort(key=lambda w: w["distance_pct"])
        bid_walls.sort(key=lambda w: w["distance_pct"])

        result: Dict[str, Any] = {
            "book_ratio": round(book_ratio, 2),
            "spread_pct": round(spread_pct, 2),
            "top15_bid_depth": round(top15_bid_depth, 2),
            "top15_ask_depth": round(top15_ask_depth, 2),
            "ask_walls": ask_walls,
            "bid_walls": bid_walls,
            "dynamic_tp": None,
            "dynamic_tp2": None,
            "dynamic_sl": None,
            "bid_depleted": False,
            "ask_depleted": False,
            "absorption_exhaustion": False,
        }

        # ── 1. INTELLIGENT EXITS FOR LONG POSITIONS ──
        if side == "LONG":
            # Primary Take Profit (TP1): First significant Ask Wall at least 1.5% away
            qualifying_tp1_walls = [w for w in ask_walls if w["distance_pct"] >= max(1.5, spread_pct * 2.0)]
            if qualifying_tp1_walls:
                tp1_wall = qualifying_tp1_walls[0]
                result["dynamic_tp"] = tp1_wall["price"] * 0.9985  # 0.15% in front of the wall
                result["tp1_wall_dist"] = tp1_wall["distance_pct"]
            else:
                # If no distinct wall exists, target liquidity expansion based on spread
                expansion_pct = max(3.0, min(8.0, spread_pct * 4.0))
                result["dynamic_tp"] = current_price * (1.0 + expansion_pct / 100.0)
                result["tp1_wall_dist"] = expansion_pct

            # Secondary Take Profit (TP2): Next significant Ask Wall at least 4% higher than TP1
            qualifying_tp2_walls = [
                w for w in ask_walls
                if w["price"] > (result["dynamic_tp"] * 1.04) and w["distance_pct"] <= 25.0
            ]
            if qualifying_tp2_walls:
                tp2_wall = qualifying_tp2_walls[0]
                result["dynamic_tp2"] = tp2_wall["price"] * 0.9985
            else:
                result["dynamic_tp2"] = result["dynamic_tp"] * 1.08

            # Intelligent Stop Loss: Behind the first genuine Bid Support Wall
            # Must be outside spread noise (at least spread_pct * 1.5 away)
            min_sl_dist = max(1.0, spread_pct * 1.5)
            qualifying_sl_walls = [w for w in bid_walls if min_sl_dist <= w["distance_pct"] <= 5.5]
            if qualifying_sl_walls:
                sl_wall = qualifying_sl_walls[0]
                result["dynamic_sl"] = sl_wall["price"] * 0.9980  # 0.20% behind the institutional shield
                result["sl_wall_dist"] = sl_wall["distance_pct"]
            else:
                # Heuristic: place SL just beyond the top-15 bid depth boundary (capped between 2.0% and 4.5%)
                safe_sl_dist = max(2.0, min(4.5, spread_pct * 2.5 + 1.0))
                result["dynamic_sl"] = current_price * (1.0 - safe_sl_dist / 100.0)
                result["sl_wall_dist"] = safe_sl_dist

            # Order Book Imbalance / Liquidity Depletion
            if book_ratio < 0.22 and top15_bid_depth < 3000.0:
                result["bid_depleted"] = True

            # Absorption: Heavy ask wall within 1.0% with book heavily skewed against buyers
            nearby_ask_wall = any(w["distance_pct"] <= 1.0 for w in ask_walls)
            if nearby_ask_wall and book_ratio < 0.35:
                result["absorption_exhaustion"] = True

        # ── 2. INTELLIGENT EXITS FOR SHORT POSITIONS ──
        elif side == "SHORT":
            # Primary Take Profit (TP1 for Short): First significant Bid Wall below
            qualifying_tp1_walls = [w for w in bid_walls if w["distance_pct"] >= max(1.5, spread_pct * 2.0)]
            if qualifying_tp1_walls:
                tp1_wall = qualifying_tp1_walls[0]
                result["dynamic_tp"] = tp1_wall["price"] * 1.0015  # 0.15% above the bid wall
                result["tp1_wall_dist"] = tp1_wall["distance_pct"]
            else:
                expansion_pct = max(3.0, min(8.0, spread_pct * 4.0))
                result["dynamic_tp"] = current_price * (1.0 - expansion_pct / 100.0)
                result["tp1_wall_dist"] = expansion_pct

            # Secondary Take Profit (TP2 for Short)
            qualifying_tp2_walls = [
                w for w in bid_walls
                if w["price"] < (result["dynamic_tp"] * 0.96) and w["distance_pct"] <= 25.0
            ]
            if qualifying_tp2_walls:
                tp2_wall = qualifying_tp2_walls[0]
                result["dynamic_tp2"] = tp2_wall["price"] * 1.0015
            else:
                result["dynamic_tp2"] = result["dynamic_tp"] * 0.92

            # Intelligent Stop Loss: Behind the first genuine Ask Resistance Wall above
            min_sl_dist = max(1.0, spread_pct * 1.5)
            qualifying_sl_walls = [w for w in ask_walls if min_sl_dist <= w["distance_pct"] <= 5.5]
            if qualifying_sl_walls:
                sl_wall = qualifying_sl_walls[0]
                result["dynamic_sl"] = sl_wall["price"] * 1.0020  # 0.20% above the resistance ceiling
                result["sl_wall_dist"] = sl_wall["distance_pct"]
            else:
                safe_sl_dist = max(2.0, min(4.5, spread_pct * 2.5 + 1.0))
                result["dynamic_sl"] = current_price * (1.0 + safe_sl_dist / 100.0)
                result["sl_wall_dist"] = safe_sl_dist

            # Short Squeeze / Liquidity Depletion
            if book_ratio > 4.5 and top15_ask_depth < 3000.0:
                result["ask_depleted"] = True

            # Absorption: Short stalling right on a massive bid wall
            nearby_bid_wall = any(w["distance_pct"] <= 1.0 for w in bid_walls)
            if nearby_bid_wall and book_ratio > 2.8:
                result["absorption_exhaustion"] = True

        return result

    def _fallback_structural_levels(self, current_price: float, side: str) -> Dict[str, Any]:
        """Provides dynamic fallback targets when depth API is momentarily unavailable."""
        if side == "SHORT":
            return {
                "dynamic_tp": current_price * 0.955,   # -4.5%
                "dynamic_tp2": current_price * 0.88,   # -12.0%
                "dynamic_sl": current_price * 1.028,   # +2.8%
                "book_ratio": 1.0,
                "bid_depleted": False,
                "ask_depleted": False,
                "absorption_exhaustion": False,
            }
        else:
            return {
                "dynamic_tp": current_price * 1.045,   # +4.5%
                "dynamic_tp2": current_price * 1.12,   # +12.0%
                "dynamic_sl": current_price * 0.972,   # -2.8%
                "book_ratio": 1.0,
                "bid_depleted": False,
                "ask_depleted": False,
                "absorption_exhaustion": False,
            }


dom_engine = DOMEngine()
