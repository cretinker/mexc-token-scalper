import asyncio
import logging
import uvicorn
from app.config import settings
from app.web.server import app
from app.bot import bot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("mexc_scalper")


@app.on_event("startup")
async def start_background_bot():
    """Launch the autonomous scanner and position guard in the background."""
    asyncio.create_task(bot.start())
    logger.info("MEXC Autonomous Scalper & Radar daemon launched in background.")


if __name__ == "__main__":
    port = 8080
    print(f"""
====================================================================
           MEXC PENNY TOKEN SCALPER & REVIVAL RADAR
====================================================================
  Status: Paper Trading Engine Online (Live MEXC Market Feed)
  Dashboard URL: http://localhost:{port}
  Mirror Routing: api.mexc.co <-> api.mexc.io <-> api.mexc.com
  Stepped Exits: TP1 (+18%) | TP2 (+45%) | Moonbag Trailing Stop
====================================================================
""")
    uvicorn.run("run_server:app", host="0.0.0.0", port=port, reload=False)
