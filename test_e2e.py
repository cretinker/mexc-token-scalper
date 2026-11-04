import asyncio
from app.bot import bot
from app.paper_engine import paper_engine
from app.position_guard import position_guard
from app.database import db


async def test():
    print("Testing MEXC Market Scan on live data...")
    signals = await bot.run_scan_cycle()
    print(f"\nTotal qualified signals found: {len(signals)}")
    for s in signals[:5]:
        sym = s["symbol"]
        play = s["play_type"]
        sc = s["score"]
        pr = s["price"]
        chg = s["change_24h"]
        vol = s["volume_24h"]
        print(f"  Signal: {sym:<12} | Play: {play:<16} | Score: {sc:<3} | Price: {pr:<10} | 24h: {chg:+.1f}% | Vol: ${vol:,.0f}")

    pf = paper_engine.get_portfolio_summary()
    print("\nPortfolio Status:")
    print(f"  Total Equity: ${pf['total_equity']:.2f}")
    print(f"  Cash: ${pf['cash']:.2f}")
    print(f"  Open Positions: {pf['open_positions_count']}/{pf['max_positions']}")

    open_pos = db.get_open_positions()
    for p in open_pos:
        sym = p["symbol"]
        entry = p["entry_price"]
        units = p["remaining_units"]
        inv = p["invested_amount"]
        tp1 = p["tp1"]
        sl = p["stop_loss"]
        print(f"  [OPEN] {sym:<12} | Entry: {entry:<10} | Units: {units:,.0f} | Invested: ${inv:.2f} | TP1: {tp1:<10} | SL: {sl:<10}")

    print("\nTesting Position Guard live evaluation...")
    updates = position_guard.update_open_positions()
    print(f"Position updates evaluated: {len(updates)}")
    for u in updates:
        print(f"  Update: {u}")


if __name__ == "__main__":
    asyncio.run(test())
