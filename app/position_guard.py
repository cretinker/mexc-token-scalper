import logging
import time
from typing import Dict, Any, List
from app.config import settings
from app.database import db
from app.mexc_client import mexc_client

logger = logging.getLogger(__name__)


class PositionGuard:
    """
    Dynamic Trade Manager and Exit Guard for Open Positions.
    Executes Stepped Ladder Exits:
      * TP1 (+3.5%): Sells 55% of position and moves Stop Loss to Breakeven (+0.3% fee cushion).
      * TP2 (+15.0%): Sells 25% of position and activates trailing moonbag.
      * Trailing Stop (8% from peak): Protects remaining 20% moonbag.
      * Hard Stop (-3.5%): Protects capital on failed breakouts.
      * Time-Stop (12h stagnant): Frees trapped capital if momentum dries up.
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
        entry_price = pos["entry_price"]
        peak_price = max(pos["peak_price"], live_price)
        remaining_units = pos["remaining_units"]
        stop_loss = pos["stop_loss"]
        tp1 = pos["tp1"]
        tp2 = pos["tp2"]
        tp1_hit = bool(pos["tp1_hit"])
        tp2_hit = bool(pos["tp2_hit"])
        opened_at = pos["opened_at"]

        # Current unrealized PnL %
        pnl_pct = ((live_price - entry_price) / entry_price) * 100.0

        # Updates dictionary for database
        pos_updates = {
            "current_price": live_price,
            "peak_price": peak_price,
        }

        # ── 1. Check Hard Stop / Trailing Stop ──
        if live_price <= stop_loss:
            exit_price = live_price * (1.0 - self.slippage_pct / 100.0)
            self._close_remaining(pos, exit_price, reason="STOP_LOSS" if not tp1_hit else "TRAIL_STOP")
            return {"symbol": symbol, "event": "CLOSED_STOP", "price": exit_price, "pnl_pct": pnl_pct}

        # ── 2. Check TP1 (+3.5%) ──
        if live_price >= tp1 and not tp1_hit and remaining_units > 0:
            units_to_sell = pos["initial_units"] * 0.55
            actual_sell = min(remaining_units, units_to_sell)
            exit_price = live_price * (1.0 - self.slippage_pct / 100.0)

            self._execute_partial_sell(pos, actual_sell, exit_price, reason="TP1_HIT", step="TP1")

            # Move Stop Loss to Break-Even (Entry price + fee buffer)
            be_stop = entry_price * 1.003
            pos_updates["stop_loss"] = be_stop
            pos_updates["tp1_hit"] = 1
            pos_updates["remaining_units"] = remaining_units - actual_sell

            db.update_position(pos_id, pos_updates)
            logger.info(f"[{symbol}] TP1 Hit at ${exit_price:.6f} (+{pnl_pct:.2f}%). Sold 55%. SL moved to Breakeven.")
            return {"symbol": symbol, "event": "TP1_HIT", "price": exit_price, "pnl_pct": pnl_pct}

        # ── 3. Check TP2 (+45%) ──
        if live_price >= tp2 and not tp2_hit and remaining_units > 0:
            units_to_sell = pos["initial_units"] * 0.25
            actual_sell = min(remaining_units, units_to_sell)
            exit_price = live_price * (1.0 - self.slippage_pct / 100.0)

            self._execute_partial_sell(pos, actual_sell, exit_price, reason="TP2_HIT", step="TP2")

            # Activate trailing stop on remaining 25% moonbag
            new_trailing_stop = peak_price * (1.0 - settings.TRAILING_STOP_PCT / 100.0)
            pos_updates["stop_loss"] = max(pos["stop_loss"], new_trailing_stop)
            pos_updates["tp2_hit"] = 1
            pos_updates["remaining_units"] = remaining_units - actual_sell

            db.update_position(pos_id, pos_updates)
            logger.info(f"[{symbol}] TP2 Hit at ${exit_price:.6f} (+{pnl_pct:.2f}%). Sold 25%. Moonbag trailing active.")
            return {"symbol": symbol, "event": "TP2_HIT", "price": exit_price, "pnl_pct": pnl_pct}

        # ── 4. Dynamic Trailing Stop Adjustment on Moonbag ──
        if tp2_hit and remaining_units > 0:
            trailing_stop = peak_price * (1.0 - settings.TRAILING_STOP_PCT / 100.0)
            if trailing_stop > stop_loss:
                pos_updates["stop_loss"] = trailing_stop

        # ── 5. Time-Stop (Stagnant Momentum Exit) ──
        age_hours = (time.time() - opened_at) / 3600.0
        if age_hours >= settings.MAX_HOLDING_HOURS and not tp1_hit:
            if -3.0 <= pnl_pct <= 3.0:
                exit_price = live_price * (1.0 - self.slippage_pct / 100.0)
                self._close_remaining(pos, exit_price, reason="TIME_STAGNANT_EXIT")
                logger.info(f"[{symbol}] Time-stop triggered after {age_hours:.1f}h. Flat momentum exited.")
                return {"symbol": symbol, "event": "CLOSED_TIME", "price": exit_price, "pnl_pct": pnl_pct}

        # Normal price & peak update
        db.update_position(pos_id, pos_updates)
        return {"symbol": symbol, "event": "HOLD", "price": live_price, "pnl_pct": pnl_pct}

    def _execute_partial_sell(self, pos: Dict[str, Any], units: float, price: float, reason: str, step: str):
        notional = units * price
        fee = notional * (self.fee_pct / 100.0)
        cost_basis = units * pos["entry_price"]
        pnl = (notional - fee) - cost_basis
        pnl_pct = (pnl / cost_basis) * 100.0 if cost_basis > 0 else 0.0

        # Update portfolio cash
        pf = db.get_portfolio()
        new_cash = pf["cash"] + notional - fee
        new_realized = pf["realized_pnl"] + pnl
        win_count = pf["win_count"] + (1 if pnl > 0 else 0)
        loss_count = pf["loss_count"] + (1 if pnl < 0 else 0)

        # Recalculate total equity
        db.update_portfolio(
            cash=new_cash,
            total_equity=new_cash + self._calc_open_equity_excluding(pos["id"], units),
            realized_pnl=new_realized,
            win_count=win_count,
            loss_count=loss_count
        )

        # Record trade log
        db.record_trade({
            "position_id": pos["id"],
            "symbol": pos["symbol"],
            "side": f"SELL_{step}",
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

        notional = units * price
        fee = notional * (self.fee_pct / 100.0)
        cost_basis = units * pos["entry_price"]
        pnl = (notional - fee) - cost_basis
        pnl_pct = (pnl / cost_basis) * 100.0 if cost_basis > 0 else 0.0

        # Update portfolio
        pf = db.get_portfolio()
        new_cash = pf["cash"] + notional - fee
        new_realized = pf["realized_pnl"] + pnl
        win_count = pf["win_count"] + (1 if pnl > 0 else 0)
        loss_count = pf["loss_count"] + (1 if pnl < 0 else 0)

        # Close position in DB
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

        db.record_trade({
            "position_id": pos["id"],
            "symbol": pos["symbol"],
            "side": f"SELL_{reason}",
            "price": price,
            "units": units,
            "notional": notional,
            "fee": fee,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "reason": reason
        })

    def _calc_open_equity_excluding(self, pos_id: int, sold_units: float) -> float:
        total = 0.0
        for p in db.get_open_positions():
            rem = p["remaining_units"]
            if p["id"] == pos_id:
                rem = max(0.0, rem - sold_units)
            total += rem * p["current_price"]
        return total


position_guard = PositionGuard()
