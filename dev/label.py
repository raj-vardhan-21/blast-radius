"""Label patches with ground truth by running the real pytest suite (dev only)."""
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/app")
from analyze import apply_hunks, parse_patch  # noqa: E402

src, pdir, out, par = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
tests = json.load(open("/tools/tests.json"))["tests"]
num = {t["id"]: t["number"] for t in tests}
by_file = {}
for t in tests:
    by_file.setdefault(t["file"], []).append(t["number"])
LINE = re.compile(r"(tests/[^\s:]+\.py)(?:::([^\s:]+)::([^\s\[]+))?")


def label(name):
    cid = name[:-6]
    work = f"/tmp/w_{cid}"
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(src, work, symlinks=True)
    text = open(os.path.join(pdir, name)).read()
    for fd in parse_patch(text):
        rel = fd["new"] or fd["old"]
        p = os.path.join(work, rel)
        old = open(p, encoding="utf-8", newline="").read() if fd["old"] and os.path.exists(p) else None
        new = apply_hunks(old, fd["hunks"])
        if fd["new"] is None:
            os.unlink(p)
        else:
            with open(os.path.join(work, fd["new"]), "w", encoding="utf-8", newline="") as f:
                f.write(new)
    env = dict(os.environ, SKIP_INTEGRATION="0", PYTHONPATH=work, PYTHONDONTWRITEBYTECODE="1", OMP_NUM_THREADS="1")
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rA",
                        "--timeout", "120", "tests"], cwd=work, env=env, capture_output=True, text=True)
    os.makedirs(os.path.dirname(out) + "/logs", exist_ok=True)
    open(os.path.dirname(out) + f"/logs/{cid}.txt", "w").write(r.stdout[-200000:] + r.stderr[-20000:])
    failing = set()
    pending = False
    for line in r.stdout.splitlines():
        start = line.startswith(("FAILED", "ERROR", "SUBFAIL", "SUBERROR"))
        if not start and not pending:
            continue
        pos = line.rfind(" tests/")
        m = LINE.search(line, pos + 1 if pos >= 0 else 0) if pos >= 0 or start else None
        if not m or (pending and not start and m.group(2) is None):
            pending = pending or start
            continue
        pending = False
        f, c, meth = m.group(1), m.group(2), m.group(3)
        if c is None:
            failing.update(by_file.get(f, []))
        else:
            meth = meth.split("[")[0]
            n = num.get(f"{f}::{c}::{meth}")
            if n is not None:
                failing.add(n)
    shutil.rmtree(work, ignore_errors=True)
    tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    print(cid, len(failing), tail[:120], file=sys.stderr, flush=True)
    kind = "fixture" if "tests/fixtures" in text else "code"
    return cid, {"failing": sorted(failing), "kind": kind}


names = sorted(n for n in os.listdir(pdir) if n.endswith(".patch"))
res = {}
if os.path.exists(out):
    res = json.load(open(out))["commits"]
todo = [n for n in names if n[:-6] not in res]
with ThreadPoolExecutor(par) as ex:
    for cid, v in ex.map(label, todo):
        res[cid] = v
        json.dump({"commits": res}, open(out, "w"), indent=1)
