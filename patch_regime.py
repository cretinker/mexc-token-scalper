import os
import json
from datetime import datetime, timezone
from test_wide_trail import load_data

dataset = load_data()

with open("test_wide_trail.py", "r") as f:
    code = f.read()

# Replace the BTC check with a strict Bullish Regime Filter
old_btc_check = '''            if btc_bars and len(btc_bars) >= 20:
                btc_curr = btc_bars[-1]["close"]
                btc_past = btc_bars[max(0, len(btc_bars)-96)]["close"]
                btc_pct_24h = ((btc_curr - btc_past) / btc_past) * 100.0 if btc_past > 0 else 0.0
                btc_ema20 = sum(b["close"] for b in btc_bars[-20:]) / 20.0
                if btc_curr < btc_ema20 * 0.99:
                    btc_dumping = True'''

new_btc_check = '''            if btc_bars and len(btc_bars) >= 50:
                btc_curr = btc_bars[-1]["close"]
                btc_past = btc_bars[max(0, len(btc_bars)-96)]["close"]
                btc_pct_24h = ((btc_curr - btc_past) / btc_past) * 100.0 if btc_past > 0 else 0.0
                btc_ema50 = sum(b["close"] for b in btc_bars[-50:]) / 50.0
                # STRICT REGIME FILTER: Only trade alts when BTC is in a healthy uptrend (above 50 EMA and positive 24h)
                if btc_curr < btc_ema50 or btc_pct_24h < -0.5:
                    btc_dumping = True'''

code = code.replace(old_btc_check, new_btc_check)

with open("test_regime_filter.py", "w") as f:
    f.write(code)
print("Created test_regime_filter.py")
