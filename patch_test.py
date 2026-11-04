import os
import json

with open("test_calibrated.py", "r") as f:
    code = f.read()

# Add losing trade logging to test_calibrated.py
code = code.replace(
    'print(f"Final Equity:',
    '''
    losses = [t for t in trades if t["pnl"] <= 0]
    print(f"Total losses: {len(losses)}")
    loss_by_sym = {}
    for t in losses:
        loss_by_sym[t["symbol"]] = loss_by_sym.get(t["symbol"], 0) + 1
    top_losers = sorted(loss_by_sym.items(), key=lambda x: x[1], reverse=True)[:10]
    print("Top losing symbols (frequency):", top_losers)
    print("Sample 5 losing trades:")
    for t in losses[:5]:
        print(f"  {t['symbol']:<12} {t['play_type']:<16} pnl: ${t['pnl']:>8,.2f} ({t['pnl_pct']:>+6.1f}%) bars: {t['bars_held']}")
    print(f"Final Equity:'''
)

with open("test_calibrated.py", "w") as f:
    f.write(code)
