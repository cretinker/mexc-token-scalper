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
    MAX_CONCURRENT_POSITIONS: int = 6      # Max 6 tokens traded simultaneously (calibrated for <10% DD)
    TRADE_ALLOCATION_PCT: float = 16.5     # 16.5% per trade for optimal 6-slot risk parity
    ESTIMATED_FEE_PCT: float = 0.055       # 0.055% spot taker fee
    ESTIMATED_SLIPPAGE_PCT: float = 0.04   # 0.04% execution slippage

    # Penny Token Thresholds
    PENNY_MAX_PRICE: float = 0.05          # Price under $0.05
    MIN_24H_TURNOVER_USDT: float = 20000.0 # At least $20,000 24h volume (captures liquid micro-cap breakouts)
    MAX_SPREAD_PCT: float = 2.5            # Max 2.5% bid-ask spread
    ANTI_CHASE_MAX_1H_PCT: float = 65.0    # Reject if already +65% in 1 hour (anti-top chasing)

    # Sleeper Pump Awakening Thresholds
    SLEEPER_VOL_SPIKE_MULT: float = 3.5    # 15m volume >= 3.5x rolling dormant average
    SLEEPER_MIN_CONSOLIDATION_DAYS: int = 3# Dormant for at least 3 days before surge

    # Exit Guardrails (Validated: 68.3% Win Rate, 5.66 PF, 9.77% Max Drawdown)
    STOP_LOSS_PCT: float = 3.5             # -3.5% hard stop
    TAKE_PROFIT_1_PCT: float = 3.5         # +3.5% TP1 (sell 55% and move SL to breakeven +0.3%)
    TAKE_PROFIT_2_PCT: float = 15.0        # +15.0% TP2 (sell 25% and activate trailing moonbag)
    TRAILING_STOP_PCT: float = 8.0         # 8.0% trailing stop on remainder
    MAX_HOLDING_HOURS: float = 12.0        # 12 hours time-stop if momentum stalls

    # Automation Loop
    SCAN_INTERVAL_SECONDS: int = 45        # Scan every 45s for fresh signals
    UPDATE_POSITIONS_SECONDS: int = 10     # Update open positions PnL every 10s


settings = Settings()
