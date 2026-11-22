"""Static recall check of candidate sets on a labeled dir: python3 dev/eval_dir.py DIR"""
import json
import sys

sys.path.insert(0, "app")
from analyze import Analysis, Map  # noqa: E402

d = sys.argv[1]
M = Map("app/map.json.gz")
ans = json.load(open(f"{d}/answers.json"))["commits"]
tests = json.load(open("sample/tests.json"))["tests"]
A = Analysis(M, "sqlglot")
miss = 0
for cid in sorted(ans):
    text = open(f"{d}/commits/{cid}.patch").read()
    cand, certain, nf, notes = A.commit(text)
    fail = set(ans[cid]["failing"])
    got = {i for i in range(M.n) if cand >> i & 1}
    m = fail - got
    if m:
        miss += 1
        files = [l[6:] for l in text.splitlines() if l.startswith("+++ b/")]
        print(f"MISS {cid} {files} fail={len(fail)} cand={len(got)} missing={sorted(m)[:8]} "
              f"{[tests[i]['id'].split('::', 1)[1] for i in sorted(m)[:3]]}")
print(f"{miss} missed of {len(ans)}")
