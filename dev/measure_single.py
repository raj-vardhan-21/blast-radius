"""Run every test alone (one forked job each) to measure isolated CPU cost."""
import json
import sys

sys.path.insert(0, "/app")
import runner  # noqa: E402

tests = json.load(open(sys.argv[1]))["tests"]
root = sys.argv[2]
out = sys.argv[3]
par = int(sys.argv[4])
runner.prewarm()
jobs = [runner.Job(str(t["number"]), root, [t["id"]], {t["id"]: 10 + 10 * t["seconds"]}, 300) for t in tests]
runner.run_jobs(jobs, parallel=par)
res = {}
for t, j in zip(tests, jobs):
    st = j.result["results"].get(t["id"]) if j.result else None
    tc = j.result["cpu"].get(t["id"]) if j.result else None
    res[t["id"]] = {"status": st, "job": round(getattr(j, "cpu", -1), 4), "test": tc}
    if st != "pass":
        print("BAD", t["id"], st, file=sys.stderr)
json.dump(res, open(out, "w"))
