import json
from app.backtester import BacktestEngine

engine = BacktestEngine()
cache_dir = r"c:\Users\DELL\Documents\Elijah Workspace\new-apex\scratch\replay_cache"

with open(f"{cache_dir}/mexc_k15_TUT_USDT.json") as f:
    candles = json.load(f)

print(f"Total candles: {len(candles)}")
first_50 = candles[:100]

for i in range(32, 100):
    c = candles[i]
    prior = candles[:i]
    close = c["close"]
    vol = c["volume"]
    turnover = close * vol
    avg_vol = sum(b["volume"] for b in prior[-96:]) / min(len(prior), 96)
    vmult = vol / avg_vol if avg_vol > 0 else 0
    change = (close - c["open"]) / c["open"] * 100.0

    # Test conditions one by one
    cands = candles[:i+1]
    sig = engine._detect_signal("TUT_USDT", cands)
    if sig:
        print(f"SUCCESS at {i}: {sig}")
    elif change > 3.0:
        # trace rejection reason
        brange = c["high"] - c["low"]
        cr = (close - c["low"]) / brange if brange > 0 else 0
        ema20 = sum(b["close"] for b in prior[-20:]) / 20.0
        print(f"Bar {i} ({change:.1f}%): turnover=${turnover:.0f}, vmult={vmult:.1f}x, cr={cr:.2f}, c>ema={close>ema20}, p={close}")
