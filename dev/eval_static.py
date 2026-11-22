"""Offline: evaluate static candidate sets against sample answers."""
import json
import sys
import time

sys.path.insert(0, "app")
from analyze import Analysis, Map  # noqa: E402

M = Map("app/map.json.gz")
tests = json.load(open("sample/tests.json"))["tests"]
ans = json.load(open("sample/answers.json"))["commits"]
A = Analysis(M, "sqlglot")
secs = [t["seconds"] for t in tests]
tot_miss = 0
only = sys.argv[1:]
for cid in sorted(ans):
    if only and cid not in only:
        continue
    t0 = time.process_time()
    text = open(f"sample/commits/{cid}.patch").read()
    print(f"== {cid} ({ans[cid]['kind']})", file=sys.stderr)
    cand, certain, nf, notes = A.commit(text)
    dt = time.process_time() - t0
    ids = [i for i in range(M.n) if cand >> i & 1]
    fail = set(ans[cid]["failing"])
    miss = fail - set(ids)
    tot_miss += bool(miss)
    s = sum(secs[i] for i in ids)
    big = sorted((secs[i], i) for i in ids)[-3:]
    print(f"{cid} {ans[cid]['kind']:8} fail={len(fail):3} cand={len(ids):5} sec={s:7.2f} "
          f"miss={sorted(miss)} big={[(round(a,1), b) for a, b in big]} t={dt:.2f} {notes}")
print("missed commits", tot_miss)

from pack import pack  # noqa: E402
tot = orc = 0
for cid in sorted(ans):
    cand, certain, nf, notes = A.commit(open(f"sample/commits/{cid}.patch").read())
    ids = [i for i in range(M.n) if cand >> i & 1]
    c, _ = pack([(round(secs[i] * 1000), i) for i in ids])
    o, _ = pack([(round(secs[i] * 1000), i) for i in ans[cid]["failing"]])
    tot += c
    orc += o
    print(f"{cid} static={c/1000:.2f} oracle={o/1000:.2f}")
print(f"STATIC {tot/1000:.1f}  ORACLE {orc/1000:.1f}")
