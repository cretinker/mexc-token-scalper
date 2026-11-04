import os
import json
from datetime import datetime, timezone
from test_wide_trail import load_data, run_sim

dataset = load_data()

# Let's inspect trades from test_wide_trail
with open("test_wide_trail.py", "r") as f:
    code = f.read()

# Let's modify test_wide_trail to print equity progression over time
code = code.replace(
    'print(f"Final Equity:',
    '''
    print("\\nMonthly Equity Progression:")
    # print equity every 15 days
    ts_min = timeline[0]
    ts_max = timeline[-1]
    step = 15 * 86400
    cur_t = ts_min
    while cur_t <= ts_max:
        d_str = datetime.fromtimestamp(cur_t, tz=timezone.utc).strftime("%Y-%m-%d")
        # calculate closed trades up to cur_t
        pnl_so_far = sum(t["pnl"] for t in trades if t["exit_time"] <= cur_t)
        print(f"  {d_str}: Realized Equity = ${starting_equity + pnl_so_far:>8,.2f}")
        cur_t += step
    print(f"Final Equity:'''
)

with open("test_wide_trail.py", "w") as f:
    f.write(code)
