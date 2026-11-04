import os
from pydantic import BaseModel


class Settings(BaseModel):
    PROJECT_NAME: str = "MEXC Penny Token & Revival Scalper"
    VERSION: str = "1.0.0"

    # API Endpoints (Fallback order for resilient connectivity)
    MEXC_PRIMARY_URL: str = "https://api.mexc.co"
    MEXC_FALLBACK_URLS: list[str] = [
        "https://api.mexc.co",
        "https://api.mexc.io",
        "https://api.mexc.com"
    ]

    # Database
    DB_PATH: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mexc_scalper.db")

    # Paper Trading Defaults
    STARTING_EQUITY: float = 1000.0        # $1,000 USDT virtual wallet
    MAX_CONCURRENT_POSITIONS: int = 3      # Limit correlated micro-cap exposure
    TRADE_ALLOCATION_PCT: float = 10.0     # Hard notional ceiling per trade
    ESTIMATED_FEE_PCT: float = 0.055       # 0.055% spot taker fee
    ESTIMATED_SLIPPAGE_PCT: float = 0.04   # 0.04% execution slippage
    PER_TRADE_RISK_PCT: float = 0.75       # Maximum planned loss at the stop
    MAX_OPEN_RISK_PCT: float = 2.0         # Aggregate planned loss across open trades
    DAILY_LOSS_CAP_PCT: float = 3.0        # Stop opening new trades after this loss
    MAX_CONSECUTIVE_LOSSES: int = 2       # Pause after a short losing streak
    LOSS_PAUSE_MINUTES: int = 45
    MIN_TRADE_NOTIONAL_USDT: float = 25.0
    MAX_ENTRIES_PER_SCAN: int = 2
    MAX_DEEP_CANDIDATES: int = 20
    MIN_SIGNAL_SCORE: int = 75
    ENABLE_NEW_LISTING: bool = False      # Recent losses were dominated by this playbook
    ENABLE_SHORTS: bool = False           # This project currently executes spot, not margin
    REQUIRE_BTC_REGIME: bool = True
    MIN_RS_IN_CHOP_PCT: float = 5.0

    # Penny Token Thresholds
    PENNY_MAX_PRICE: float = 0.05          # Price under $0.05
    MIN_24H_TURNOVER_USDT: float = 25000.0 # Avoid the thinnest micro-cap books
    MAX_SPREAD_PCT: float = 1.2            # Costs matter at small TP distances
    ANTI_CHASE_MAX_1H_PCT: float = 25.0    # Reject a late vertical move

    # Sleeper Pump Awakening Thresholds
    SLEEPER_VOL_SPIKE_MULT: float = 3.5    # 15m volume >= 3.5x rolling dormant average
    SLEEPER_MIN_CONSOLIDATION_DAYS: int = 3# Dormant for at least 3 days before surge

    # Exit Guardrails. These are safety defaults, not validated performance claims.
    STOP_LOSS_PCT: float = 2.8             # Default structural fallback stop
    TAKE_PROFIT_1_PCT: float = 4.5         # TP1 must clear costs and offer positive R:R
    TAKE_PROFIT_2_PCT: float = 15.0        # +15.0% TP2 (sell 25% and activate trailing moonbag)
    TRAILING_STOP_PCT: float = 8.0         # 8.0% trailing stop on remainder
    MAX_HOLDING_HOURS: float = 8.0         # Release capital sooner when momentum stalls

    # Automation Loop
    SCAN_INTERVAL_SECONDS: int = 45        # Scan every 45s for fresh signals
    UPDATE_POSITIONS_SECONDS: int = 10     # Update open positions PnL every 10s


settings = Settings()
