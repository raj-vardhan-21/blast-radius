"""Run every test through app/runner in the unmodified tree; report status and CPU vs recorded."""
import json
import os
import resource
import sys
import time

sys.path.insert(0, "/app")
import runner  # noqa: E402

tests = json.load(open(sys.argv[1]))["tests"]
root = sys.argv[2]
out = sys.argv[3]
only = sys.argv[4:] 
t0 = time.time()
runner.prewarm()
r0 = resource.getrusage(resource.RUSAGE_SELF)
print("prewarm cpu", r0.ru_utime + r0.ru_stime, file=sys.stderr)
groups = {}
for t in tests:
    if only and t["file"] not in only:
        continue
    groups.setdefault(t["file"], []).append(t)
jobs = []
for f, ts in groups.items():
    ids = [t["id"] for t in ts]
    jobs.append(runner.Job(f.replace("/", "_"), root, ids, {t["id"]: 5 + 10 * t["seconds"] for t in ts}, 600))
c0 = resource.getrusage(resource.RUSAGE_CHILDREN)
res = {}
for j in jobs:
    b = resource.getrusage(resource.RUSAGE_CHILDREN)
    runner.run_jobs([j], parallel=1)
    a = resource.getrusage(resource.RUSAGE_CHILDREN)
    jc = a.ru_utime + a.ru_stime - b.ru_utime - b.ru_stime
    rec = sum(t["seconds"] for t in groups[j.ids[0].split("::")[0]])
    st = j.result["results"] if j.result else {}
    bad = {k: v for k, v in st.items() if v != "pass"}
    print(f"{j.key:50} rss={getattr(j, 'maxrss', 0)//1024}MB cpu={jc:7.2f} rec={rec:7.2f} bad={bad}", file=sys.stderr)
    res[j.key] = {"cpu": jc, "rec": rec, "res": st, "tcpu": j.result["cpu"] if j.result else {}}
c1 = resource.getrusage(resource.RUSAGE_CHILDREN)
print("children cpu", c1.ru_utime + c1.ru_stime - c0.ru_utime - c0.ru_stime, "wall", time.time() - t0, file=sys.stderr)
json.dump(res, open(out, "w"))
