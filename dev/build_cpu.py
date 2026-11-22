"""CPU cost table -> app/cpu.json: warm per-test CPU (file-sequential run) and per-class setup.

Setup comes from isolated single-test jobs, scaled to match observed job CPU.
"""
import json, statistics, sys
single = json.load(open(sys.argv[1]))
warm = {}
for v in json.load(open(sys.argv[2])).values():
    warm.update(v["tcpu"])
over = {}
test = {}
for tid, v in single.items():
    f, c, _ = tid.split("::")
    t = v["test"] or 0.0
    test[tid] = round(max(warm.get(tid, t), 0.5 * t) * 1.1, 4)
    over.setdefault(f"{f}::{c}", []).append(max(0.0, v["job"] - t - 0.06))
setup = {k: round(0.4 * statistics.median(v), 3) for k, v in over.items()}
json.dump({"base": 0.1, "test": test, "setup": setup}, open(sys.argv[3], "w"), separators=(",", ":"))
