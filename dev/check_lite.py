"""Dev: soundness check of the TPC-DS artifact shortcut on a patch dir.

For every patch where main.py would consider the check, compute the artifact; where it matches
the pinned commit's, run the real test on the patched tree. Any failure there is a bug.
docker run ... -v $PWD/app:/app:ro -v $PWD/dev:/tools:ro -v DIR:/p:ro br-dev python /tools/check_lite.py /opt/sqlglot-or-/src /p
"""
import glob
import json
import os
import sys

sys.path.insert(0, "/app")
os.environ.setdefault("BR_TMP", "/tmp")
import lite  # noqa: E402
import runner  # noqa: E402
from analyze import Analysis, Map  # noqa: E402

root, pdir = sys.argv[1], sys.argv[2]
M = Map("/app/map.json.gz")
E = json.load(open("/app/lite.json"))
lines = {k: set(v) for k, v in E["lines"].items()}
names = set(E["names"])
idx = M.ids.index(lite.TEST)
A = Analysis(M, root)
os.chdir(root)
sys.path.insert(0, root)
jobs = []
for p in sorted(glob.glob(f"{pdir}/*.patch")):
    cid = os.path.basename(p)[:-6]
    cand, cert, nf, _ = A.commit(open(p).read())
    L = A.lite
    if not (cand >> idx & 1) or cert >> idx & 1 or not L["ok"] or L["names"] & names:
        continue
    if any(a <= ln <= b for rel, spans in L["spans"].items() for a, b in spans for ln in lines.get(rel, ())):
        continue
    j = runner.Job(cid + "-lite", None, [], {}, 60.0)
    j.lite, j.cid, j.files = True, cid, nf
    j.overlay = f"/tmp/ov/{cid}"
    runner.build_overlay(root, j.overlay, nf)
    jobs.append(j)
print(f"{len(jobs)} eligible", file=sys.stderr, flush=True)
# baseline from an unpatched overlay
b = runner.Job("base-lite", None, [], {}, 60.0)
b.lite, b.overlay = True, runner.build_overlay(root, "/tmp/ov/base", {})
runner.run_jobs([b] + jobs, parallel=3)
base = b.result["lite"]
same = [j for j in jobs if (j.result or {}).get("lite") == base]
print(f"{len(same)} same: {[j.cid for j in same]}", file=sys.stderr, flush=True)
runs = []
for j in same:
    r = runner.Job(j.cid, j.overlay, [lite.TEST], {lite.TEST: 200.0}, 300.0)
    runs.append(r)
runner.run_jobs(runs, parallel=3)
bad = [r.key for r in runs if (r.result or {}).get("results", {}).get(lite.TEST) != "pass"]
print(f"UNSOUND: {bad}" if bad else "all same-artifact patches pass", file=sys.stderr)
