import logging
import time
from typing import Dict, Any, Optional
from app.config import settings
from app.database import db
from app.mexc_client import mexc_client
from app.dom_engine import dom_engine

logger = logging.getLogger(__name__)


class PaperEngine:
    """
    Paper Trading Simulator Engine.
    Executes realistic orders against live MEXC market data with
    accurate fee, slippage, and portfolio accounting.
    """

    def __init__(self):
        self.max_positions = settings.MAX_CONCURRENT_POSITIONS
        self.alloc_pct = settings.TRADE_ALLOCATION_PCT
        self.fee_pct = settings.ESTIMATED_FEE_PCT
        self.slippage_pct = settings.ESTIMATED_SLIPPAGE_PCT

    def get_portfolio_summary(self) -> Dict[str, Any]:
        """Calculates real-time equity, cash, unrealized PnL, win rate."""
        pf = db.get_portfolio()
        open_positions = db.get_open_positions()

        open_equity = 0.0
        unrealized_pnl = 0.0

        for p in open_positions:
            rem_units = p["remaining_units"]
            cur_price = p["current_price"]
            entry_price = p["entry_price"]
            side = p.get("side", "LONG")

            if side == "SHORT":
                # For short: profit when current price is lower than entry
                pos_cost = rem_units * entry_price
                pos_val = rem_units * cur_price
                pnl = pos_cost - pos_val
                open_equity += (pos_cost + pnl)
                unrealized_pnl += pnl
            else:
                pos_value = rem_units * cur_price
                pos_cost = rem_units * entry_price
                open_equity += pos_value
                unrealized_pnl += (pos_value - pos_cost)

        cash = pf["cash"]
        total_equity = cash + open_equity
        start_equity = pf["starting_equity"]
        total_pnl = total_equity - start_equity
        roi_pct = (total_pnl / start_equity) * 100.0 if start_equity > 0 else 0.0

        total_closed = pf["win_count"] + pf["loss_count"]
        win_rate = (pf["win_count"] / total_closed * 100.0) if total_closed > 0 else 0.0

        return {
            "starting_equity": round(start_equity, 2),
            "cash": round(cash, 2),
            "open_equity": round(open_equity, 2),
            "total_equity": round(total_equity, 2),
            "peak_equity": round(pf["peak_equity"], 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "realized_pnl": round(pf["realized_pnl"], 2),
            "total_pnl": round(total_pnl, 2),
            "roi_pct": round(roi_pct, 2),
            "win_count": pf["win_count"],
            "loss_count": pf["loss_count"],
            "win_rate": round(win_rate, 1),
            "open_positions_count": len(open_positions),
            "max_positions": self.max_positions,
        }

    def can_open_trade(self, symbol: str) -> tuple[bool, str]:
        """Validates portfolio rules before executing a trade."""
        # 1. Check if symbol is in post-exit cooldown
        in_cooldown, rem_sec = db.is_in_cooldown(symbol)
        if in_cooldown:
            return False, f"Symbol {symbol} in cooldown ({int(rem_sec)}s remaining)"

        open_positions = db.get_open_positions()
        if len(open_positions) >= self.max_positions:
            return False, f"Max concurrent positions reached ({self.max_positions})"

        # Check if symbol already open
        if any(p["symbol"] == symbol for p in open_positions):
            return False, f"Position for {symbol} is already active"

        pf = db.get_portfolio()
        target_allocation = pf["total_equity"] * (self.alloc_pct / 100.0)
        if pf["cash"] < target_allocation * 0.8:
            return False, f"Insufficient cash (${pf['cash']:.2f}) for trade allocation (${target_allocation:.2f})"

        return True, "Approved"

    def execute_buy(self, signal: Dict[str, Any]) -> Optional[int]:
        """
        Executes a paper order (LONG or SHORT) based on a detected signal.
        Enforces DOM-anchored dynamic TP and dynamic SL.
        """
        symbol = signal["symbol"]
        side = signal.get("side", "LONG").upper()
        can_trade, reason = self.can_open_trade(symbol)
        if not can_trade:
            logger.info(f"Skipping {side} on {symbol}: {reason}")
            return None

        # Fetch latest book price to determine fill price
        book = mexc_client.fetch_book_ticker(symbol)
        if side == "SHORT":
            raw_px = book["bid"] if book and book["bid"] > 0 else signal["price"]
            fill_price = raw_px * (1.0 - self.slippage_pct / 100.0)
        else:
            raw_px = book["ask"] if book and book["ask"] > 0 else signal["price"]
            fill_price = raw_px * (1.0 + self.slippage_pct / 100.0)

        if fill_price <= 0:
            return None

        # Calculate position size
        pf = db.get_portfolio()
        desired_investment = min(pf["cash"] * 0.95, pf["total_equity"] * (self.alloc_pct / 100.0))
        fee = desired_investment * (self.fee_pct / 100.0)
        net_investment = desired_investment - fee
        units = net_investment / fill_price

        # Consult DOM Engine for intelligent structural levels (no hardcoded static clamps)
        dom_info = dom_engine.analyze_orderbook(symbol, fill_price, side=side)

        tp1 = dom_info.get("dynamic_tp")
        tp2 = dom_info.get("dynamic_tp2")
        stop_loss = dom_info.get("dynamic_sl")

        if side == "SHORT":
            # For SHORT: Take profit below, stop loss above
            if not tp1 or tp1 >= fill_price:
                tp1 = fill_price * 0.955
            if not tp2 or tp2 >= tp1:
                tp2 = tp1 * 0.92
            if not stop_loss or stop_loss <= fill_price:
                stop_loss = fill_price * 1.028

            tp1_dist_pct = ((fill_price - tp1) / fill_price) * 100.0
            sl_dist_pct = ((stop_loss - fill_price) / fill_price) * 100.0
            trade_action = "SELL_SHORT"
        else:
            # For LONG: Take profit above, stop loss below
            if not tp1 or tp1 <= fill_price:
                tp1 = fill_price * 1.045
            if not tp2 or tp2 <= tp1:
                tp2 = tp1 * 1.08
            if not stop_loss or stop_loss >= fill_price:
                stop_loss = fill_price * 0.972

            tp1_dist_pct = ((tp1 - fill_price) / fill_price) * 100.0
            sl_dist_pct = ((fill_price - stop_loss) / fill_price) * 100.0
            trade_action = "BUY"

        # Deduct cash from portfolio
        new_cash = pf["cash"] - desired_investment
        db.update_portfolio(
            cash=new_cash,
            total_equity=pf["total_equity"],
            realized_pnl=pf["realized_pnl"],
            win_count=pf["win_count"],
            loss_count=pf["loss_count"]
        )

        # Open position record
        pos_id = db.open_position({
            "symbol": symbol,
            "side": side,
            "play_type": signal.get("play_type", "MOMENTUM"),
            "entry_price": fill_price,
            "units": units,
            "invested_amount": desired_investment,
            "stop_loss": stop_loss,
            "tp1": tp1,
            "tp2": tp2,
        })

        # Record trade log
        db.record_trade({
            "position_id": pos_id,
            "symbol": symbol,
            "side": trade_action,
            "price": fill_price,
            "units": units,
            "notional": desired_investment,
            "fee": fee,
            "reason": f"Signal {signal.get('play_type')} (Score {signal.get('score')})"
        })

        logger.info(f"ENTERED {side}: {symbol} at ${fill_price:.6f} (${desired_investment:.2f} allocated) | Structural DOM TP1: ${tp1:.6f} (+{tp1_dist_pct:.2f}%) | Structural DOM SL: ${stop_loss:.6f} (-{sl_dist_pct:.2f}%)")
        return pos_id

    def close_manually(self, pos_id: int) -> bool:
        """Manual close trigger for user control from UI."""
        pos = db.get_position_by_id(pos_id)
        if not pos or pos["status"] != "OPEN":
            return False

        symbol = pos["symbol"]
        live_price = mexc_client.fetch_ticker_price(symbol) or pos["current_price"]
        from app.position_guard import position_guard
        position_guard._close_remaining(pos, live_price, reason="MANUAL_CLOSE")
        return True


paper_engine = PaperEngine()
