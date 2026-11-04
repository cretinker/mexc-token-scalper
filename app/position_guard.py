import logging
import time
from typing import Dict, Any, List
from app.config import settings
from app.database import db
from app.mexc_client import mexc_client
from app.dom_engine import dom_engine

logger = logging.getLogger(__name__)


class PositionGuard:
    """
    Intelligent Order-Book Trade Manager and Exit Guard.
    Executes Pure DOM Liquidity Exits:
      * Dynamic DOM TP: Front-runs the primary Level-2 Ask Wall.
      * Dynamic DOM SL: Anchors behind the primary Level-2 Bid Wall.
      * Adaptive Retargeting: Snaps TP down if a new wall appears overhead.
      * Progressive DOM Ratchet: Steps up behind newly formed Bid Support blocks.
      * Milestone Profit Locking: Never surrenders double-digit gains back to breakeven.
      * L2 Absorption & Bid/Ask Depletion Defense.
      * Automated 30-Minute Post-Stop Cooldown.
    """

    def __init__(self):
        self.fee_pct = settings.ESTIMATED_FEE_PCT
        self.slippage_pct = settings.ESTIMATED_SLIPPAGE_PCT

    def update_open_positions(self) -> List[Dict[str, Any]]:
        """Fetch live prices for all open positions and execute required exits."""
        positions = db.get_open_positions()
        if not positions:
            return []

        updates_summary = []
        for pos in positions:
            symbol = pos["symbol"]
            live_price = mexc_client.fetch_ticker_price(symbol)
            if not live_price or live_price <= 0:
                continue

            res = self._evaluate_position_step(pos, live_price)
            updates_summary.append(res)

        return updates_summary

    def _evaluate_position_step(self, pos: Dict[str, Any], live_price: float) -> Dict[str, Any]:
        pos_id = pos["id"]
        symbol = pos["symbol"]
        side = pos.get("side", "LONG").upper()
        entry_price = pos["entry_price"]
        remaining_units = pos["remaining_units"]
        stop_loss = pos["stop_loss"]
        tp1 = pos["tp1"]
        tp2 = pos["tp2"]
        tp1_hit = bool(pos["tp1_hit"])
        tp2_hit = bool(pos["tp2_hit"])
        opened_at = pos["opened_at"]

        # Track high-water peak (Long) or low-water trough (Short)
        if side == "SHORT":
            peak_price = min(pos["peak_price"], live_price) if pos["peak_price"] > 0 else live_price
            pnl_pct = ((entry_price - live_price) / entry_price) * 100.0
        else:
            peak_price = max(pos["peak_price"], live_price)
            pnl_pct = ((live_price - entry_price) / entry_price) * 100.0

        pos_updates: Dict[str, Any] = {
            "current_price": live_price,
            "peak_price": peak_price,
        }

        # Query live Order Book DOM
        dom_info = dom_engine.analyze_orderbook(symbol, live_price, side=side)

        # ── 1. Defensive Liquidity Exits (Bid/Ask Depletion & Absorption) ──
        if side == "LONG":
            if dom_info.get("bid_depleted") and pnl_pct <= 0.5:
                exit_price = live_price * (1.0 - self.slippage_pct / 100.0)
                self._close_remaining(pos, exit_price, reason="DOM_BID_DEPLETED")
                logger.warning(f"[{symbol}] Emergency exit: Level-2 bid liquidity depleted.")
                return {"symbol": symbol, "event": "CLOSED_BID_DEPLETED", "price": exit_price, "pnl_pct": pnl_pct}

            if dom_info.get("absorption_exhaustion") and pnl_pct >= 2.0:
                exit_price = live_price * (1.0 - self.slippage_pct / 100.0)
                self._close_remaining(pos, exit_price, reason="DOM_ABSORPTION_EXHAUSTION")
                logger.info(f"[{symbol}] Profit harvested: Passive sell absorption detected at wall (+{pnl_pct:.2f}%).")
                return {"symbol": symbol, "event": "CLOSED_ABSORPTION", "price": exit_price, "pnl_pct": pnl_pct}

        elif side == "SHORT":
            if dom_info.get("ask_depleted") and pnl_pct <= 0.5:
                exit_price = live_price * (1.0 + self.slippage_pct / 100.0)
                self._close_remaining(pos, exit_price, reason="DOM_ASK_DEPLETED")
                logger.warning(f"[{symbol}] Emergency short cover: Level-2 ask liquidity depleted.")
                return {"symbol": symbol, "event": "CLOSED_ASK_DEPLETED", "price": exit_price, "pnl_pct": pnl_pct}

            if dom_info.get("absorption_exhaustion") and pnl_pct >= 2.0:
                exit_price = live_price * (1.0 + self.slippage_pct / 100.0)
                self._close_remaining(pos, exit_price, reason="DOM_ABSORPTION_EXHAUSTION")
                logger.info(f"[{symbol}] Short profit harvested: Passive buyer absorption detected at wall (+{pnl_pct:.2f}%).")
                return {"symbol": symbol, "event": "CLOSED_ABSORPTION", "price": exit_price, "pnl_pct": pnl_pct}

        # ── 2. Adaptive DOM Take Profit Retargeting ──
        # If an unexpected massive wall has formed closer to market price, snap TP1 down/up to front-run it
        if not tp1_hit and remaining_units > 0:
            active_tp = dom_info.get("dynamic_tp")
            if side == "LONG" and active_tp:
                if active_tp > entry_price * 1.015 and active_tp < tp1:
                    pos_updates["tp1"] = active_tp
                    tp1 = active_tp
            elif side == "SHORT" and active_tp:
                if active_tp < entry_price * 0.985 and active_tp > tp1:
                    pos_updates["tp1"] = active_tp
                    tp1 = active_tp

        # ── 3. Check Stop Loss / Trailing Stop ──
        stop_triggered = (live_price <= stop_loss) if side == "LONG" else (live_price >= stop_loss)
        if stop_triggered:
            slip_mult = (1.0 - self.slippage_pct / 100.0) if side == "LONG" else (1.0 + self.slippage_pct / 100.0)
            exit_price = live_price * slip_mult
            reason = "STOP_LOSS" if not tp1_hit else "TRAIL_STOP"
            self._close_remaining(pos, exit_price, reason=reason)
            return {"symbol": symbol, "event": f"CLOSED_{reason}", "price": exit_price, "pnl_pct": pnl_pct}

        # ── 4. Check TP1 (DOM Wall Front-Run) ──
        tp1_triggered = (live_price >= tp1) if side == "LONG" else (live_price <= tp1)
        if tp1_triggered and not tp1_hit and remaining_units > 0:
            units_to_sell = pos["initial_units"] * 0.55
            actual_sell = min(remaining_units, units_to_sell)
            slip_mult = (1.0 - self.slippage_pct / 100.0) if side == "LONG" else (1.0 + self.slippage_pct / 100.0)
            exit_price = live_price * slip_mult

            self._execute_partial_sell(pos, actual_sell, exit_price, reason="TP1_HIT", step="TP1")

            # Move Stop Loss behind nearest active Level-2 Support Shield (or Breakeven + fee buffer)
            if side == "LONG":
                be_stop = entry_price * 1.003
                dom_sl = dom_info.get("dynamic_sl")
                new_sl = max(be_stop, dom_sl) if (dom_sl and dom_sl < live_price) else be_stop
                pos_updates["stop_loss"] = new_sl
            else:
                be_stop = entry_price * 0.997
                dom_sl = dom_info.get("dynamic_sl")
                new_sl = min(be_stop, dom_sl) if (dom_sl and dom_sl > live_price) else be_stop
                pos_updates["stop_loss"] = new_sl

            pos_updates["tp1_hit"] = 1
            pos_updates["remaining_units"] = remaining_units - actual_sell
            db.update_position(pos_id, pos_updates)
            logger.info(f"[{symbol}] TP1 Hit at ${exit_price:.6f} (+{pnl_pct:.2f}%). Sold 55%. SL anchored to ${new_sl:.6f}.")
            return {"symbol": symbol, "event": "TP1_HIT", "price": exit_price, "pnl_pct": pnl_pct}

        # ── 5. Check TP2 (Expansion Wall) ──
        tp2_triggered = (live_price >= tp2) if side == "LONG" else (live_price <= tp2)
        if tp2_triggered and not tp2_hit and remaining_units > 0:
            units_to_sell = pos["initial_units"] * 0.25
            actual_sell = min(remaining_units, units_to_sell)
            slip_mult = (1.0 - self.slippage_pct / 100.0) if side == "LONG" else (1.0 + self.slippage_pct / 100.0)
            exit_price = live_price * slip_mult

            self._execute_partial_sell(pos, actual_sell, exit_price, reason="TP2_HIT", step="TP2")
            pos_updates["tp2_hit"] = 1
            pos_updates["remaining_units"] = remaining_units - actual_sell
            db.update_position(pos_id, pos_updates)
            logger.info(f"[{symbol}] TP2 Hit at ${exit_price:.6f} (+{pnl_pct:.2f}%). Sold 25%. Moonbag active.")
            return {"symbol": symbol, "event": "TP2_HIT", "price": exit_price, "pnl_pct": pnl_pct}

        # ── 6. Progressive DOM Support Ratchet & Dynamic High-Water Trailing ──
        if remaining_units > 0 and (tp1_hit or pnl_pct >= 2.5):
            current_sl = pos_updates.get("stop_loss", stop_loss)

            if side == "LONG":
                # A. Level-2 Bid Wall Support Ratchet
                dom_sl = dom_info.get("dynamic_sl")
                if dom_sl and dom_sl > current_sl and dom_sl < live_price:
                    pos_updates["stop_loss"] = dom_sl
                    current_sl = dom_sl

                # B. High-water mark trailing (4.0% below peak)
                trail_sl = peak_price * (1.0 - 0.040)
                if trail_sl > current_sl and trail_sl < live_price:
                    pos_updates["stop_loss"] = trail_sl
                    current_sl = trail_sl

                # C. Tiered Milestone Profit Locks
                if pnl_pct >= 5.0 and current_sl < entry_price * 1.020:
                    pos_updates["stop_loss"] = entry_price * 1.020
                if pnl_pct >= 8.0 and current_sl < entry_price * 1.045:
                    pos_updates["stop_loss"] = entry_price * 1.045
                if pnl_pct >= 12.0 and current_sl < entry_price * 1.080:
                    pos_updates["stop_loss"] = entry_price * 1.080

            elif side == "SHORT":
                # A. Level-2 Ask Wall Resistance Ratchet
                dom_sl = dom_info.get("dynamic_sl")
                if dom_sl and dom_sl < current_sl and dom_sl > live_price:
                    pos_updates["stop_loss"] = dom_sl
                    current_sl = dom_sl

                # B. Low-water mark trailing (4.0% above peak low)
                trail_sl = peak_price * (1.0 + 0.040)
                if trail_sl < current_sl and trail_sl > live_price:
                    pos_updates["stop_loss"] = trail_sl
                    current_sl = trail_sl

                # C. Tiered Milestone Profit Locks
                if pnl_pct >= 5.0 and current_sl > entry_price * 0.980:
                    pos_updates["stop_loss"] = entry_price * 0.980
                if pnl_pct >= 8.0 and current_sl > entry_price * 0.955:
                    pos_updates["stop_loss"] = entry_price * 0.955
                if pnl_pct >= 12.0 and current_sl > entry_price * 0.920:
                    pos_updates["stop_loss"] = entry_price * 0.920

        # ── 7. Stagnant Momentum Time-Stop ──
        age_hours = (time.time() - opened_at) / 3600.0
        if age_hours >= settings.MAX_HOLDING_HOURS and not tp1_hit:
            if -2.5 <= pnl_pct <= 2.5:
                slip_mult = (1.0 - self.slippage_pct / 100.0) if side == "LONG" else (1.0 + self.slippage_pct / 100.0)
                exit_price = live_price * slip_mult
                self._close_remaining(pos, exit_price, reason="TIME_STAGNANT_EXIT")
                logger.info(f"[{symbol}] Time-stop triggered after {age_hours:.1f}h. Stagnant position exited.")
                return {"symbol": symbol, "event": "CLOSED_TIME", "price": exit_price, "pnl_pct": pnl_pct}

        # Normal price & peak update in DB
        db.update_position(pos_id, pos_updates)
        return {"symbol": symbol, "event": "HOLD", "price": live_price, "pnl_pct": pnl_pct}

    def _execute_partial_sell(self, pos: Dict[str, Any], units: float, price: float, reason: str, step: str):
        side = pos.get("side", "LONG").upper()
        notional = units * price
        fee = notional * (self.fee_pct / 100.0)
        cost_basis = units * pos["entry_price"]

        if side == "SHORT":
            pnl = (cost_basis - notional) - fee
            cash_credit = cost_basis + pnl
        else:
            pnl = (notional - fee) - cost_basis
            cash_credit = notional - fee

        pnl_pct = (pnl / cost_basis) * 100.0 if cost_basis > 0 else 0.0

        # Update portfolio cash
        pf = db.get_portfolio()
        new_cash = pf["cash"] + cash_credit
        new_realized = pf["realized_pnl"] + pnl
        win_count = pf["win_count"] + (1 if pnl > 0 else 0)
        loss_count = pf["loss_count"] + (1 if pnl < 0 else 0)

        db.update_portfolio(
            cash=new_cash,
            total_equity=new_cash + self._calc_open_equity_excluding(pos["id"], units),
            realized_pnl=new_realized,
            win_count=win_count,
            loss_count=loss_count
        )

        trade_side = f"COVER_{step}" if side == "SHORT" else f"SELL_{step}"
        db.record_trade({
            "position_id": pos["id"],
            "symbol": pos["symbol"],
            "side": trade_side,
            "price": price,
            "units": units,
            "notional": notional,
            "fee": fee,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "reason": reason
        })

    def _close_remaining(self, pos: Dict[str, Any], price: float, reason: str):
        units = pos["remaining_units"]
        if units <= 0:
            return

        symbol = pos["symbol"]
        side = pos.get("side", "LONG").upper()
        notional = units * price
        fee = notional * (self.fee_pct / 100.0)
        cost_basis = units * pos["entry_price"]

        if side == "SHORT":
            pnl = (cost_basis - notional) - fee
            cash_credit = cost_basis + pnl
        else:
            pnl = (notional - fee) - cost_basis
            cash_credit = notional - fee

        pnl_pct = (pnl / cost_basis) * 100.0 if cost_basis > 0 else 0.0

        pf = db.get_portfolio()
        new_cash = pf["cash"] + cash_credit
        new_realized = pf["realized_pnl"] + pnl
        win_count = pf["win_count"] + (1 if pnl > 0 else 0)
        loss_count = pf["loss_count"] + (1 if pnl < 0 else 0)

        db.update_position(pos["id"], {
            "status": "CLOSED",
            "current_price": price,
            "remaining_units": 0.0,
            "closed_at": time.time(),
            "realized_pnl": pos["realized_pnl"] + pnl
        })

        db.update_portfolio(
            cash=new_cash,
            total_equity=new_cash + self._calc_open_equity_excluding(pos["id"], 0),
            realized_pnl=new_realized,
            win_count=win_count,
            loss_count=loss_count
        )

        trade_side = f"COVER_{reason}" if side == "SHORT" else f"SELL_{reason}"
        db.record_trade({
            "position_id": pos["id"],
            "symbol": symbol,
            "side": trade_side,
            "price": price,
            "units": units,
            "notional": notional,
            "fee": fee,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "reason": reason
        })

        # Apply 30-minute cooldown if stopped out or liquidity collapsed
        if reason in ("STOP_LOSS", "DOM_BID_DEPLETED", "DOM_ASK_DEPLETED"):
            db.set_cooldown(symbol, duration_seconds=1800.0, reason=reason)
            logger.info(f"[{symbol}] Cooldown initiated (30 minutes) following {reason}.")

    def _calc_open_equity_excluding(self, pos_id: int, sold_units: float) -> float:
        total = 0.0
        for p in db.get_open_positions():
            rem = p["remaining_units"]
            if p["id"] == pos_id:
                rem = max(0.0, rem - sold_units)

            side = p.get("side", "LONG").upper()
            if side == "SHORT":
                pos_cost = rem * p["entry_price"]
                pos_val = rem * p["current_price"]
                pnl = pos_cost - pos_val
                total += (pos_cost + pnl)
            else:
                total += rem * p["current_price"]
        return total


position_guard = PositionGuard()
