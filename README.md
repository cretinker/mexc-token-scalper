# MEXC Penny Token Scalper & Revival Radar

Autonomous scanner, real-time paper trading engine, and dynamic risk manager for MEXC spot penny tokens and dormant revival breakouts.

## Core Features

- **Live Market Feed with Multi-Domain Failover**: Connects to MEXC Spot REST APIs (`api.mexc.co` and `api.mexc.io`) to bypass regional ISP connection blocks and guarantee continuous tick data.
- **Three Core Playbooks**:
  - `SLEEPER_REVIVAL`: Detects dormant tokens with flat consolidation breaking out with 3.5x to 10x volume surges.
  - `PENNY_MOMENTUM`: Sub-cent micro-caps (< $0.05) with accelerating turnover and clean order book depth.
  - `NEW_LISTING`: Fresh listings establishing initial accumulation bases.
- **Institutional Safety and Anti-Rug Filter**:
  - Strict maximum bid-ask spread check (<= 2.5%).
  - Minimum 24h turnover requirement ($25,000+ USDT).
  - Anti-top-chasing guard (bans tokens already pumped > 65% in 1 hour or > 300% in 24 hours).
  - Minimum order book depth check.
- **Stepped Ladder Exit Engine**:
  - `TP1 (+18%)`: Sells 50% of the position and moves the Stop Loss to Breakeven (+0.2%).
  - `TP2 (+45%)`: Sells 25% of the position and activates dynamic trailing stop.
  - `Moonbag (Remaining 25%)`: Trails peak price by 12% to capture extended parabolic runs.
  - `Hard Stop (-7.5%)`: Automatic stop loss execution.
  - `Time-Stop (8 Hours)`: Closes stagnant flat trades to redeploy capital.
- **Persistent SQLite Ledger**:
  - Maintains complete balance history, positions, fills, fees, and realized PnL across restarts.
- **Dual Interface**:
  - Web Dashboard: Modern, dark Bloomberg-style interface with real-time KPI cards, position trackers, manual controls, and trade logs.
  - CLI Terminal: Rich live console tables for terminal-only monitoring.

## Getting Started

### 1. Environment Setup

Virtual environment is pre-configured with Python 3.11. To run with the project environment:

```powershell
Set-Location "c:\Users\DELL\Documents\Elijah Workspace\mexc new token scalper"
```

### 2. Launching the Web Dashboard

Start the web server and background autonomous scanner:

```powershell
.\.venv\Scripts\python.exe run_server.py
```

Open your browser to:
[http://localhost:8080](http://localhost:8080)

### 3. Launching the CLI Terminal

To monitor live in your terminal:

```powershell
.\.venv\Scripts\python.exe run_bot.py
```

## Configuration

Adjust settings in `app/config.py`:
- `STARTING_EQUITY`: Initial paper wallet cash (default: 1000.0 USDT).
- `MAX_CONCURRENT_POSITIONS`: Maximum simultaneous open trades (default: 5).
- `TRADE_ALLOCATION_PCT`: Allocation percentage per trade (default: 15.0%).
- `STOP_LOSS_PCT`: Hard stop loss threshold (default: 7.5%).
- `TAKE_PROFIT_1_PCT`: First take-profit target (default: 18.0%).
- `TAKE_PROFIT_2_PCT`: Second take-profit target (default: 45.0%).
- `TRAILING_STOP_PCT`: Trailing distance from peak for moonbag (default: 12.0%).
