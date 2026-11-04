import json

cache_dir = r"c:\Users\DELL\Documents\Elijah Workspace\new-apex\scratch\replay_cache"
with open(f"{cache_dir}/mexc_k15_TUT_USDT.json") as f:
    candles = json.load(f)

moves = []
for i, c in enumerate(candles):
    chg = (c["close"] - c["open"]) / c["open"] * 100.0
    turn = c["close"] * c["volume"]
    if chg > 10.0 and turn > 10000:
        moves.append((i, c["dt"] if "dt" in c else c["time"], c["close"], chg, turn))

print(f"Total big moves on TUT: {len(moves)}")
for m in moves[:15]:
    print(m)
