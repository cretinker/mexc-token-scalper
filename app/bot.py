import asyncio
import logging
import time
from typing import Dict, Any, List

from app.config import settings
from app.scanner import scanner
from app.paper_engine import paper_engine
from app.position_guard import position_guard
from app.database import db

logger = logging.getLogger(__name__)


class MexcScalperBot:
    """
    Autonomous Paper Scalper & Radar Engine.
    Periodically scans MEXC spot markets for high-upside penny setups
    and continuously guards open positions with dynamic exits.
    """

    def __init__(self):
        self.is_running = False
        self.last_scan_time: float = 0.0
        self.last_scan_signals: List[Dict[str, Any]] = []

    async def start(self):
        """Starts the autonomous trading loop."""
        self.is_running = True
        logger.info("Starting MEXC Penny Scalper & Revival Radar Bot...")

        # Run initial scan immediately
        await self.run_scan_cycle()

        while self.is_running:
            try:
                # 1. Position Guard: Update open positions every 10s
                position_guard.update_open_positions()

                # 2. Market Scanner: Run full market scan every 45s
                if time.time() - self.last_scan_time >= settings.SCAN_INTERVAL_SECONDS:
                    await self.run_scan_cycle()

            except Exception as e:
                logger.error(f"Error in bot execution loop: {e}", exc_info=True)

            await asyncio.sleep(settings.UPDATE_POSITIONS_SECONDS)

    def stop(self):
        self.is_running = False
        logger.info("MEXC Scalper Bot stopped.")

    async def run_scan_cycle(self) -> List[Dict[str, Any]]:
        """Executes a single market scan cycle and executes trades."""
        self.last_scan_time = time.time()
        logger.info("Triggering MEXC market scan across all spot tokens...")

        # Run scanner in thread pool to avoid blocking async loop
        loop = asyncio.get_event_loop()
        signals = await loop.run_in_executor(None, scanner.scan_market)
        self.last_scan_signals = signals

        # Evaluate trade execution for top signals
        pf_summary = paper_engine.get_portfolio_summary()
        available_slots = pf_summary["max_positions"] - pf_summary["open_positions_count"]

        if available_slots > 0 and signals:
            logger.info(f"{available_slots} position slots available. Evaluating top signals...")
            for sig in signals[:available_slots]:
                if sig.get("score", 0) >= 75:
                    pos_id = paper_engine.execute_buy(sig)
                    if pos_id:
                        sig["traded"] = True

        return signals


bot = MexcScalperBot()
