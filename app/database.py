import sqlite3
import time
from typing import Any, Dict, List, Optional
from app.config import settings


class Database:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or settings.DB_PATH
        self._init_tables()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self):
        with self._get_conn() as conn:
            c = conn.cursor()

            # Portfolio state table
            c.execute("""
            CREATE TABLE IF NOT EXISTS portfolio (
                id INTEGER PRIMARY KEY,
                starting_equity REAL NOT NULL,
                cash REAL NOT NULL,
                total_equity REAL NOT NULL,
                peak_equity REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                win_count INTEGER NOT NULL DEFAULT 0,
                loss_count INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL
            )
            """)

            # Initialize portfolio if empty
            c.execute("SELECT COUNT(*) FROM portfolio")
            if c.fetchone()[0] == 0:
                now = time.time()
                c.execute("""
                INSERT INTO portfolio (id, starting_equity, cash, total_equity, peak_equity, realized_pnl, win_count, loss_count, updated_at)
                VALUES (1, ?, ?, ?, ?, 0.0, 0, 0, ?)
                """, (settings.STARTING_EQUITY, settings.STARTING_EQUITY, settings.STARTING_EQUITY, settings.STARTING_EQUITY, now))

            # Positions table
            c.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                play_type TEXT NOT NULL,
                entry_price REAL NOT NULL,
                current_price REAL NOT NULL,
                peak_price REAL NOT NULL,
                initial_units REAL NOT NULL,
                remaining_units REAL NOT NULL,
                invested_amount REAL NOT NULL,
                realized_pnl REAL NOT NULL DEFAULT 0.0,
                stop_loss REAL NOT NULL,
                tp1 REAL NOT NULL,
                tp2 REAL NOT NULL,
                tp1_hit INTEGER NOT NULL DEFAULT 0,
                tp2_hit INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'OPEN', -- OPEN or CLOSED
                opened_at REAL NOT NULL,
                closed_at REAL,
                updated_at REAL NOT NULL
            )
            """)

            # Trades execution log
            c.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL, -- BUY, SELL_TP1, SELL_TP2, SELL_STOP, SELL_TIME, SELL_MANUAL
                price REAL NOT NULL,
                units REAL NOT NULL,
                notional REAL NOT NULL,
                fee REAL NOT NULL,
                pnl REAL NOT NULL DEFAULT 0.0,
                pnl_pct REAL NOT NULL DEFAULT 0.0,
                reason TEXT NOT NULL,
                timestamp REAL NOT NULL,
                FOREIGN KEY(position_id) REFERENCES positions(id)
            )
            """)

            # Scanner signals history
            c.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL DEFAULT 'LONG',
                play_type TEXT NOT NULL,
                score INTEGER NOT NULL,
                price REAL NOT NULL,
                change_24h REAL NOT NULL,
                volume_24h REAL NOT NULL,
                spike_multiple REAL NOT NULL,
                spread_pct REAL NOT NULL,
                reason TEXT NOT NULL,
                traded INTEGER NOT NULL DEFAULT 0,
                timestamp REAL NOT NULL
            )
            """)

            # Cooldowns table to prevent instant re-entry after stops
            c.execute("""
            CREATE TABLE IF NOT EXISTS cooldowns (
                symbol TEXT PRIMARY KEY,
                cooldown_until REAL NOT NULL,
                reason TEXT NOT NULL
            )
            """)

            # Non-destructive migrations for existing SQLite databases
            c.execute("PRAGMA table_info(positions)")
            pos_cols = [col[1] for col in c.fetchall()]
            if "side" not in pos_cols:
                c.execute("ALTER TABLE positions ADD COLUMN side TEXT NOT NULL DEFAULT 'LONG'")

            c.execute("PRAGMA table_info(signals)")
            sig_cols = [col[1] for col in c.fetchall()]
            if "side" not in sig_cols:
                c.execute("ALTER TABLE signals ADD COLUMN side TEXT NOT NULL DEFAULT 'LONG'")

            conn.commit()

    # --- Portfolio Methods ---
    def get_portfolio(self) -> Dict[str, Any]:
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM portfolio WHERE id = 1")
            row = c.fetchone()
            return dict(row) if row else {}

    def update_portfolio(self, cash: float, total_equity: float, realized_pnl: float, win_count: int, loss_count: int):
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT peak_equity FROM portfolio WHERE id = 1")
            row = c.fetchone()
            current_peak = row["peak_equity"] if row else total_equity
            new_peak = max(current_peak, total_equity)

            c.execute("""
            UPDATE portfolio
            SET cash = ?, total_equity = ?, peak_equity = ?, realized_pnl = ?, win_count = ?, loss_count = ?, updated_at = ?
            WHERE id = 1
            """, (cash, total_equity, new_peak, realized_pnl, win_count, loss_count, time.time()))
            conn.commit()

    def reset_portfolio(self, starting_equity: float):
        with self._get_conn() as conn:
            c = conn.cursor()
            now = time.time()
            c.execute("""
            UPDATE portfolio
            SET starting_equity = ?, cash = ?, total_equity = ?, peak_equity = ?, realized_pnl = 0.0, win_count = 0, loss_count = 0, updated_at = ?
            WHERE id = 1
            """, (starting_equity, starting_equity, starting_equity, starting_equity, now))
            c.execute("DELETE FROM positions")
            c.execute("DELETE FROM trades")
            c.execute("DELETE FROM signals")
            conn.commit()

    # --- Position Methods ---
    def open_position(self, pos_data: Dict[str, Any]) -> int:
        side = pos_data.get("side", "LONG")
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("""
            INSERT INTO positions (
                symbol, side, play_type, entry_price, current_price, peak_price,
                initial_units, remaining_units, invested_amount, realized_pnl,
                stop_loss, tp1, tp2, tp1_hit, tp2_hit, status, opened_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0, ?, ?, ?, 0, 0, 'OPEN', ?, ?)
            """, (
                pos_data["symbol"], side, pos_data["play_type"], pos_data["entry_price"],
                pos_data["entry_price"], pos_data["entry_price"], pos_data["units"],
                pos_data["units"], pos_data["invested_amount"], pos_data["stop_loss"],
                pos_data["tp1"], pos_data["tp2"], time.time(), time.time()
            ))
            conn.commit()
            return c.lastrowid

    def get_open_positions(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM positions WHERE status = 'OPEN' ORDER BY opened_at DESC")
            return [dict(r) for r in c.fetchall()]

    def get_position_by_id(self, pos_id: int) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM positions WHERE id = ?", (pos_id,))
            row = c.fetchone()
            return dict(row) if row else None

    def update_position(self, pos_id: int, updates: Dict[str, Any]):
        fields = []
        values = []
        for k, v in updates.items():
            fields.append(f"{k} = ?")
            values.append(v)
        fields.append("updated_at = ?")
        values.append(time.time())
        values.append(pos_id)

        query = f"UPDATE positions SET {', '.join(fields)} WHERE id = ?"
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute(query, tuple(values))
            conn.commit()

    # --- Trade Methods ---
    def record_trade(self, trade_data: Dict[str, Any]):
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("""
            INSERT INTO trades (
                position_id, symbol, side, price, units, notional, fee, pnl, pnl_pct, reason, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade_data["position_id"], trade_data["symbol"], trade_data["side"],
                trade_data["price"], trade_data["units"], trade_data["notional"],
                trade_data["fee"], trade_data.get("pnl", 0.0), trade_data.get("pnl_pct", 0.0),
                trade_data["reason"], time.time()
            ))
            conn.commit()

    def get_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?", (limit,))
            return [dict(r) for r in c.fetchall()]

    # --- Signal Methods ---
    def record_signal(self, sig: Dict[str, Any]):
        side = sig.get("side", "LONG")
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("""
            INSERT INTO signals (
                symbol, side, play_type, score, price, change_24h, volume_24h,
                spike_multiple, spread_pct, reason, traded, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                sig["symbol"], side, sig["play_type"], sig["score"], sig["price"],
                sig["change_24h"], sig["volume_24h"], sig["spike_multiple"],
                sig["spread_pct"], sig["reason"], 1 if sig.get("traded") else 0, time.time()
            ))
            conn.commit()

    def get_recent_signals(self, limit: int = 30) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM signals ORDER BY timestamp DESC LIMIT ?", (limit,))
            return [dict(r) for r in c.fetchall()]

    # --- Risk telemetry ---
    def get_realized_pnl_since(self, since_timestamp: float) -> float:
        """Return realized trade PnL since a UTC epoch timestamp."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(pnl), 0.0) AS pnl FROM trades WHERE timestamp >= ?",
                (since_timestamp,),
            ).fetchone()
            return float(row["pnl"] if row else 0.0)

    def get_recent_loss_streak(self, limit: int = 20) -> tuple[int, float]:
        """Return consecutive negative closing/partial trades and latest loss time."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT pnl, timestamp FROM trades WHERE pnl != 0 ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        streak = 0
        latest_loss = 0.0
        for row in rows:
            if float(row["pnl"]) >= 0:
                break
            if streak == 0:
                latest_loss = float(row["timestamp"])
            streak += 1
        return streak, latest_loss

    # --- Cooldown Methods ---
    def set_cooldown(self, symbol: str, duration_seconds: float = 1800.0, reason: str = "STOP_LOSS"):
        """Prevents buying the same symbol immediately after an exit or stop loss."""
        with self._get_conn() as conn:
            c = conn.cursor()
            until = time.time() + duration_seconds
            c.execute("""
            INSERT INTO cooldowns (symbol, cooldown_until, reason)
            VALUES (?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET cooldown_until = excluded.cooldown_until, reason = excluded.reason
            """, (symbol, until, reason))
            conn.commit()

    def is_in_cooldown(self, symbol: str) -> tuple[bool, float]:
        """Returns (True, remaining_seconds) if symbol is active in cooldown."""
        with self._get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT cooldown_until FROM cooldowns WHERE symbol = ?", (symbol,))
            row = c.fetchone()
            if row:
                until = row["cooldown_until"]
                now = time.time()
                if until > now:
                    return True, until - now
            return False, 0.0


db = Database()
