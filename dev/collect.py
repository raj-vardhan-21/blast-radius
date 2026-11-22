"""Run each test id in its own pytest process with the tracer; parallel."""
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

tests = json.load(open(sys.argv[1]))["tests"]
outdir = sys.argv[2]
jobs = int(sys.argv[3]) if len(sys.argv) > 3 else 32
os.makedirs(outdir, exist_ok=True)
env = dict(os.environ, TRACE_ROOT="/src", PYTHONPATH="/src:/tools:/tools/site", SKIP_INTEGRATION="0",
           PYTHONDONTWRITEBYTECODE="1", OMP_NUM_THREADS="1")


def run(t):
    out = f"{outdir}/{t['number']}.json"
    if os.path.exists(out):
        return 0
    e = dict(env, TRACE_OUT=out)
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "tracer",
                        "-x", t["id"]], cwd="/src", env=e, capture_output=True, text=True)
    if r.returncode != 0:
        print("FAIL", t["id"], r.stdout[-500:], file=sys.stderr)
    return r.returncode


with ThreadPoolExecutor(jobs) as ex:
    rc = list(ex.map(run, tests))
print("nonzero:", sum(1 for r in rc if r))
