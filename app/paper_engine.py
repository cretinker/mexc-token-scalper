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

    def can_open_trade(self, symbol: str, side: str = "LONG") -> tuple[bool, str]:
        """Validates portfolio rules before executing a trade."""
        side = side.upper()
        if side == "SHORT" and not settings.ENABLE_SHORTS:
            return False, "Short trades are disabled for spot execution"
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
        now = time.time()
        utc_day_start = now - (now % 86400)
        daily_pnl = db.get_realized_pnl_since(utc_day_start)
        daily_loss_limit = pf["starting_equity"] * (settings.DAILY_LOSS_CAP_PCT / 100.0)
        if daily_pnl <= -daily_loss_limit:
            return False, f"Daily loss cap reached (${daily_pnl:.2f} <= -${daily_loss_limit:.2f})"

        loss_streak, latest_loss = db.get_recent_loss_streak()
        if (
            loss_streak >= settings.MAX_CONSECUTIVE_LOSSES
            and latest_loss > 0
            and now - latest_loss < settings.LOSS_PAUSE_MINUTES * 60
        ):
            return False, f"Loss-streak pause active ({loss_streak} consecutive losses)"

        target_allocation = pf["total_equity"] * (self.alloc_pct / 100.0)
        if pf["cash"] < target_allocation * 0.8:
            return False, f"Insufficient cash (${pf['cash']:.2f}) for trade allocation (${target_allocation:.2f})"

        return True, "Approved"

    def validate_entry_preflight(
        self,
        symbol: str,
        side: str,
        fill_price: float,
        dom_info: Dict[str, Any],
        tp1: float,
        stop_loss: float
    ) -> tuple[bool, str]:
        """
        Hardens entry criteria against traps:
          1. Enforces minimum 1.25 Risk/Reward ratio based on structural DOM targets.
          2. Rejects entry if order book ratio is adverse (< 0.65 for Long, > 1.5 for Short).
          3. Rejects Long if a massive Ask Wall is within 1.1% of entry.
          4. Validates 15m micro-candle momentum (rejects buying falling knives or shorting vertical rockets).
        """
        # 1. Structural Risk-to-Reward Ratio
        potential_gain = abs(tp1 - fill_price)
        potential_risk = abs(fill_price - stop_loss)
        if potential_risk <= 0:
            return False, "Invalid zero risk range"

        rr = potential_gain / potential_risk
        if rr < 1.50:
            return False, f"Unfavorable DOM R:R ratio ({rr:.2f} < 1.50)"

        # 2. Order Book Imbalance / Overhead Trap Check
        book_ratio = dom_info.get("book_ratio", 1.0)
        if side == "LONG":
            if book_ratio < 0.65:
                return False, f"Adverse order book pressure (bids < 65% of asks: {book_ratio:.2f})"

            ask_walls = dom_info.get("ask_walls", [])
            for w in ask_walls:
                if w["distance_pct"] < 1.1:
                    return False, f"Overhead Ask Wall too close (+{w['distance_pct']}%, ${w['notional']:,.0f})"

        elif side == "SHORT":
            if book_ratio > 1.5:
                return False, f"Heavy buyer order book pressure (bids > 150% of asks: {book_ratio:.2f})"

            bid_walls = dom_info.get("bid_walls", [])
            for w in bid_walls:
                if w["distance_pct"] < 1.1:
                    return False, f"Underlying Bid Wall too close (-{w['distance_pct']}%, ${w['notional']:,.0f})"

        # 3. Micro-Candle Trend & Momentum Alignment
        try:
            klines = mexc_client.fetch_klines(symbol, interval="15m", limit=5)
            if len(klines) >= 3:
                last_candle = klines[-1]
                candle_return = (last_candle["close"] - last_candle["open"]) / last_candle["open"] if last_candle["open"] > 0 else 0.0

                if side == "LONG":
                    # Reject if the current 15m candle is a severe red dump candle (> -3.5%)
                    if candle_return < -0.035:
                        return False, f"Rejecting falling knife (current 15m candle is {candle_return*100:.1f}%)"
                    # Reject if price broke below recent 3-candle support
                    low_3 = min(c["low"] for c in klines[-3:])
                    if fill_price < low_3 * 0.995:
                        return False, "Price breaking below 3-candle support"

                elif side == "SHORT":
                    # Reject shorting into an active vertical pump candle (> +4.0%)
                    if candle_return > 0.040:
                        return False, f"Rejecting short into active vertical pump candle (+{candle_return*100:.1f}%)"
        except Exception as e:
            logger.warning(f"Kline validation error on {symbol}: {e}")

        return True, "Pre-flight approved"

    def execute_buy(self, signal: Dict[str, Any]) -> Optional[int]:
        """
        Executes a paper order (LONG or SHORT) based on a detected signal.
        Enforces DOM-anchored dynamic TP and dynamic SL.
        """
        symbol = signal["symbol"]
        side = signal.get("side", "LONG").upper()
        can_trade, reason = self.can_open_trade(symbol, side)
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

        # --- Hardened Entry Pre-Flight Check ---
        passed, fail_reason = self.validate_entry_preflight(symbol, side, fill_price, dom_info, tp1, stop_loss)
        if not passed:
            logger.info(f"Entry pre-flight REJECTED {side} on {symbol}: {fail_reason}")
            return None

        if "committee_passed" in signal and not signal["committee_passed"]:
            logger.info(f"Entry committee REJECTED {side} on {symbol}")
            return None

        # Size from the planned loss at the actual structural stop, not from a
        # fixed dollar allocation. This prevents a burst of correlated trades
        # from turning two small stop-outs into a large hourly drawdown.
        pf = db.get_portfolio()
        equity = max(float(pf["total_equity"]), 0.0)
        stop_pct = abs(fill_price - stop_loss) / fill_price if fill_price > 0 else 1.0
        regime_multiplier = float(signal.get("regime_risk_multiplier", 1.0))
        per_trade_risk = equity * (settings.PER_TRADE_RISK_PCT / 100.0) * max(0.0, min(1.0, regime_multiplier))
        risk_budget = per_trade_risk
        if settings.ENABLE_AGGREGATE_OPEN_RISK:
            open_risk = self._get_open_risk_usd()
            max_open_risk = equity * (settings.MAX_OPEN_RISK_PCT / 100.0)
            remaining_risk = max_open_risk - open_risk
            risk_budget = min(per_trade_risk, max(0.0, remaining_risk))
        if stop_pct <= 0 or risk_budget <= 0:
            return None

        risk_limited_notional = risk_budget / stop_pct
        desired_investment = min(
            pf["cash"] * 0.95,
            equity * (self.alloc_pct / 100.0),
            risk_limited_notional,
        )
        if desired_investment < settings.MIN_TRADE_NOTIONAL_USDT:
            logger.info(
                f"Entry risk budget too small for {symbol}: "
                f"${desired_investment:.2f} < ${settings.MIN_TRADE_NOTIONAL_USDT:.2f}"
            )
            return None
        fee = desired_investment * (self.fee_pct / 100.0)
        net_investment = desired_investment - fee
        units = net_investment / fill_price

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

    def _get_open_risk_usd(self) -> float:
        """Planned loss at each stored stop for all currently open positions."""
        total = 0.0
        for pos in db.get_open_positions():
            entry = float(pos.get("entry_price", 0.0))
            stop = float(pos.get("stop_loss", 0.0))
            invested = float(pos.get("invested_amount", 0.0))
            if entry > 0 and stop > 0:
                total += invested * abs(entry - stop) / entry
        return total

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
