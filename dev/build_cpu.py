"""Per-test isolated CPU and per-class setup overhead -> app/cpu.json."""
import json, statistics, sys
m = json.load(open(sys.argv[1]))
base = 0.06
over = {}
test = {}
for tid, v in m.items():
    f, c, _ = tid.split("::")
    t = v["test"] or 0.0
    test[tid] = round(t, 4)
    over.setdefault(f"{f}::{c}", []).append(max(0.0, v["job"] - t - base))
setup = {k: round(statistics.median(v), 3) for k, v in over.items()}
json.dump({"base": base, "test": test, "setup": setup}, open(sys.argv[2], "w"), separators=(",", ":"))
for k, v in sorted(setup.items(), key=lambda a: -a[1])[:12]: print(k, v)
