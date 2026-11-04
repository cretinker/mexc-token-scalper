import os
from typing import Dict, Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import settings
from app.database import db
from app.paper_engine import paper_engine
from app.position_guard import position_guard
from app.bot import bot

app = FastAPI(title=settings.PROJECT_NAME, version=settings.VERSION)

# Allow CORS for local dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(STATIC_DIR, exist_ok=True)


class ResetRequest(BaseModel):
    starting_equity: float = 1000.0


@app.on_event("startup")
async def startup_event():
    # Background worker for position updates
    pass


@app.get("/api/status")
def get_status() -> Dict[str, Any]:
    """Returns complete real-time dashboard data."""
    position_guard.update_open_positions()
    portfolio = paper_engine.get_portfolio_summary()
    open_positions = db.get_open_positions()

    # Format positions with live calculations
    formatted_positions = []
    for p in open_positions:
        cur_p = p["current_price"]
        entry_p = p["entry_price"]
        pnl_pct = ((cur_p - entry_p) / entry_p) * 100.0 if entry_p > 0 else 0.0
        val = p["remaining_units"] * cur_p
        cost = p["remaining_units"] * entry_p
        formatted_positions.append({
            **p,
            "current_value": round(val, 2),
            "unrealized_pnl": round(val - cost, 2),
            "pnl_pct": round(pnl_pct, 2)
        })

    recent_signals = db.get_recent_signals(limit=25)
    recent_trades = db.get_trades(limit=30)

    return {
        "portfolio": portfolio,
        "open_positions": formatted_positions,
        "signals": recent_signals,
        "trades": recent_trades,
        "bot_running": bot.is_running
    }


@app.post("/api/scan")
async def trigger_scan():
    """Triggers an on-demand market scan."""
    signals = await bot.run_scan_cycle()
    return {"status": "success", "signals_found": len(signals)}


@app.post("/api/trade/close/{pos_id}")
def manual_close(pos_id: int):
    """Closes an open position manually at current market price."""
    success = paper_engine.close_manually(pos_id)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to close position or position not found")
    return {"status": "success", "message": f"Position #{pos_id} closed"}


@app.post("/api/portfolio/reset")
def reset_portfolio(req: ResetRequest):
    """Resets paper wallet to starting amount."""
    db.reset_portfolio(req.starting_equity)
    return {"status": "success", "message": f"Portfolio reset to ${req.starting_equity:.2f} USDT"}


@app.get("/")
def serve_index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return JSONResponse({"status": "Dashboard UI file pending"})
