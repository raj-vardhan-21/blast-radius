"""Decide, per commit, which candidate tests to execute locally and which to list blindly."""
import json
import os

from pack import pack

HERE = os.path.dirname(os.path.abspath(__file__))
# share of a run set's listing cost expected to come back as failures
FAIL_FRAC = float(os.environ.get("BR_FAIL_FRAC", "0.15"))
CPU_SCALE = float(os.environ.get("BR_CPU_SCALE", "1.0"))


class CostModel:
    def __init__(self, tests):
        with open(os.path.join(HERE, "cpu.json")) as f:
            d = json.load(f)
        self.base = d["base"]
        self.setup = d["setup"]
        self.ms = {t["number"]: round(t["seconds"] * 1000) for t in tests}
        self.cpu = {}
        self.cls = {}
        for t in tests:
            c = d["test"].get(t["id"])
            self.cpu[t["number"]] = (c if c is not None else t["seconds"]) + 0.002
            f, k, _ = t["id"].split("::")
            self.cls[t["number"]] = f"{f}::{k}"

    def run_cpu(self, nums):
        if not nums:
            return 0.0
        classes = {self.cls[n] for n in nums}
        return self.base + sum(self.cpu[n] for n in nums) + sum(self.setup.get(c, 0.02) for c in classes)

    def static_cost(self, nums):
        return pack([(self.ms[n], n) for n in nums])[0] / 1000.0

    def expected(self, run, static, p):
        cpu = self.run_cpu(run)
        if static:
            out = self.static_cost(static) + (p * 1.0 if run else 0.0)
        else:
            out = p * (2.3 + FAIL_FRAC * max(0.0, self.static_cost(run) - 2.0)) if run else 0.0
        return cpu * CPU_SCALE + out


def decide(cm, cand, forced, p):
    """Return (run set, static set)."""
    forced = set(forced)
    rest = sorted(set(cand) - forced, key=lambda n: -cm.cpu[n])
    best = (cm.static_cost(sorted(set(cand))), set(), set(cand))
    if not rest:
        return best[1], best[2]
    groups = {}
    for n in rest:
        groups.setdefault(cm.cls[n], []).append(n)
    # per class: how many of its most expensive tests to list blindly (len == whole class)
    choice = {c: 0 for c in groups}

    def build():
        run, static = set(), set(forced)
        for c, ns in groups.items():
            k = choice[c]
            static.update(ns[:k])
            run.update(ns[k:])
        return run, static

    def score():
        run, static = build()
        return cm.expected(run, static, p), run, static

    cur = score()
    for _ in range(6):
        improved = False
        for c, ns in groups.items():
            opts = sorted(set([0, 1, 2, 3, 4, len(ns)]) & set(range(len(ns) + 1)))
            old = choice[c]
            for k in opts:
                if k == old:
                    continue
                choice[c] = k
                s = score()
                if s[0] < cur[0] - 1e-9:
                    cur = s
                    old = k
                    improved = True
                choice[c] = old
        if not improved:
            break
    if cur[0] < best[0]:
        return cur[1], cur[2]
    return best[1], best[2]
