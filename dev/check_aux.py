"""Dev: soundness check of the lite.py stand-ins on a patch dir.

For every patch where main.py would consider a stand-in, run it; where it says "behaves as
pinned", run the real test on the patched tree. Any failure there is a bug.
docker run ... -v $PWD/app:/app:ro -v $PWD/dev:/tools:ro -v DIR:/p:ro br-dev python /tools/check_aux.py /src /p [mn|tpcds|both]
"""
import glob
import json
import os
import subprocess
import sys

sys.path.insert(0, "/app")
os.environ.setdefault("BR_TMP", "/tmp")
import lite  # noqa: E402
import runner  # noqa: E402
from analyze import Analysis, Map  # noqa: E402

root, pdir = sys.argv[1], sys.argv[2]
what = sys.argv[3] if len(sys.argv) > 3 else "both"
PAR = int(os.environ.get("PAR", "5"))
M = Map("/app/map.json.gz")
E = json.load(open("/app/lite.json"))
lines = {k: set(v) for k, v in E["lines"].items()}
names = set(E["names"])
it = M.ids.index(lite.TEST)
im = M.ids.index(lite.MN_TEST)
base = json.loads(subprocess.run([sys.executable, "/app/lite.py", root], capture_output=True, text=True, check=True).stdout)
A = Analysis(M, root)
os.chdir(root)
sys.path.insert(0, root)
jobs = []
for p in sorted(glob.glob(f"{pdir}/*.patch")):
    cid = os.path.basename(p)[:-6]
    cand, cert, nf, _ = A.commit(open(p).read())
    L = A.lite
    if any(v is False for v in nf.values()):
        continue
    mn = (what != "tpcds" and cand >> im & 1 and not cert >> im & 1
          and all(r.startswith("sqlglot/") and r.endswith(".py") and isinstance(t, str) for r, t in nf.items()))
    tp = (what != "mn" and cand >> it & 1 and not cert >> it & 1 and L["ok"] and not L["names"] & names
          and not any(a <= ln <= b for rel, spans in L["spans"].items() for a, b in spans for ln in lines.get(rel, ())))
    if not (mn or tp):
        continue
    j = runner.Job(cid + "-aux", None, [], {}, 90.0)
    j.aux, j.cid = True, cid
    j.mn = base["mn"] if mn else None
    j.tpcds = base["tpcds"] if tp else None
    j.lite_kept = j.lite_dropped = bool(tp)
    j.overlay = f"/tmp/ov/{cid}"
    runner.build_overlay(root, j.overlay, nf)
    jobs.append(j)
print(f"{len(jobs)} eligible", file=sys.stderr, flush=True)
runner.run_jobs(jobs, parallel=PAR)
runs = []
for j in jobs:
    r = j.result or {}
    print(f"  {j.cid}: {r}", file=sys.stderr, flush=True)
    ids = []
    if r.get("mn") is True:
        ids.append(lite.MN_TEST)
    if r.get("lite") == "same":
        ids.append(lite.TEST)
    if ids:
        runs.append(runner.Job(j.cid, j.overlay, ids, {i: 400.0 for i in ids}, 900.0))
print(f"{len(runs)} to verify", file=sys.stderr, flush=True)
runner.run_jobs(runs, parallel=PAR)
bad = []
for r in runs:
    res = (r.result or {}).get("results", {})
    for i in r.ids:
        if res.get(i) != "pass":
            bad.append((r.key, i.split("::")[-1], res.get(i)))
print(f"UNSOUND: {bad}" if bad else "all stand-in passes are real passes", file=sys.stderr)
