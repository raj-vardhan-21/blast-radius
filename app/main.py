"""Blast radius selector: for each patch, choose the tests that could fail."""
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze import Analysis, Map, apply_probes, log  # noqa: E402
from pack import pack  # noqa: E402

DATA = Path(os.environ.get("BR_DATA", "/data"))
ROOT = os.environ.get("BR_SQLGLOT", "/opt/sqlglot")
TMP = os.environ.get("BR_TMP", "/tmp")
HERE = os.path.dirname(os.path.abspath(__file__))
NO_RUN = os.environ.get("BR_NO_RUN") == "1"
MIN_EXEC = 20


def aux_check(plans, sels, by_id, ms, runner, deadline):
    """Drop test_max_nodes / test_execute_tpcds where lite.py shows they behave as at the pinned commit."""
    try:
        with open(os.path.join(HERE, "lite_base.txt")) as f:
            base = json.load(f)
        with open(os.path.join(HERE, "lite.json")) as f:
            E = json.load(f)
    except (OSError, ValueError):
        return
    from lite import MN_TEST, TEST

    num = by_id.get(TEST) if base.get("tpcds") else None
    num_mn = by_id.get(MN_TEST) if base.get("mn") else None
    lines = {k: set(v) for k, v in E["lines"].items()}
    names = set(E["names"])

    def cost(sel, drop):
        return pack([(ms[n], n) for n in sel if n not in drop])[0]

    jobs = []
    for cid, pl in plans.items():
        L = pl.get("lite")
        if not L or pl["bad"]:
            continue
        sel = sels[cid]
        fixed = pl["certain"] | pl.get("run", set())
        # test_max_nodes reads nothing but sqlglot's code
        mn = (num_mn in sel and num_mn not in fixed
              and all(r.startswith("sqlglot/") and r.endswith(".py") and isinstance(t, str)
                      for r, t in pl["newfiles"].items()))
        tp = (num in sel and num not in fixed and L["ok"] and not L["names"] & names
              and not any(a <= ln <= b for rel, spans in L["spans"].items() for a, b in spans
                          for ln in lines.get(rel, ())))
        # the TPC-DS check costs about 3 s of CPU, the max_nodes one about 0.5 s
        kept = tp and cost(sel, ()) - cost(sel, {num}) >= 5000
        dropped = tp and mn and cost(sel, {num_mn}) - cost(sel, {num_mn, num}) >= 5000
        if mn and not dropped and cost(sel, ()) - cost(sel, {num_mn}) < 2000:
            mn = False
        if not (mn or kept):
            continue
        job = runner.Job(cid + "-aux", None, [], {}, 60.0)
        job.aux = True
        job.cid = cid
        job.mn = base["mn"] if mn else None
        job.tpcds = base["tpcds"] if tp else None
        job.lite_kept, job.lite_dropped = kept, dropped
        job.overlay = os.path.join(TMP, "ovl", cid)
        runner.build_overlay(ROOT, job.overlay, pl["newfiles"])
        jobs.append(job)
    if not jobs:
        return
    runner.run_jobs(jobs, parallel=2, deadline=deadline)
    shutil.rmtree(os.path.join(TMP, "ovl"), ignore_errors=True)
    for job in jobs:
        r = job.result or {}
        log(f"  {job.cid}: aux {r} ({getattr(job, 'cpu', 0):.2f}s cpu)")
        if r.get("mn") is True:
            sels[job.cid].discard(num_mn)
        if r.get("lite") == "same":
            sels[job.cid].discard(num)


def main():
    t_start = time.monotonic()
    t0 = time.process_time()
    tests = json.loads((DATA / "tests.json").read_text())["tests"]
    M = Map(os.path.join(HERE, "map.json.gz"))
    by_id = {t["id"]: t["number"] for t in tests}
    id_of = {t["number"]: t["id"] for t in tests}
    ms = {t["number"]: round(t["seconds"] * 1000) for t in tests}
    map_ids = M.ids or []
    to_num = [by_id.get(i) for i in map_ids]
    known = set(map_ids)
    unknown = [t for t in tests if t["id"] not in known]
    if unknown:
        log(f"{len(unknown)} tests not in map")
    all_nums = [t["number"] for t in tests]
    A = Analysis(M, ROOT)
    patches = sorted((DATA / "commits").glob("*.patch"))
    n_commits = len(patches)
    # wall budget: 3 min for 30 commits (4.5 for ~45); keep a wide margin
    deadline = t_start + min(150.0, 4.2 * n_commits + 15)

    def nums_of(bits):
        s = set()
        i = 0
        while bits:
            if bits & 1 and to_num[i] is not None:
                s.add(to_num[i])
            bits >>= 1
            i += 1
        return s

    plans = {}
    for patch in patches:
        cid = patch.stem
        try:
            text = patch.read_text(encoding="utf-8", errors="replace")
            log(f"== {cid}")
            cand_bits, certain_bits, newfiles, notes = A.commit(text)
            cand = nums_of(cand_bits)
            certain = nums_of(certain_bits) & cand
            if cand and unknown:
                files = {tests[n]["file"] for n in cand} | set(newfiles)
                cand.update(t["number"] for t in unknown if t["file"] in files)
            bad = any(v is False for v in newfiles.values())
            plans[cid] = {"cand": cand, "certain": certain, "newfiles": newfiles, "bad": bad,
                          "probes": {k: v for k, v in A.probes.items() if v}, "unprobed": A.unprobed,
                          "cond": set(), "trivial": A.data_trivial, "lite": A.lite}
            log(f"  {cid}: {len(cand)} candidates, {len(certain)} certain {notes}")
        except Exception as e:  # never risk a miss on an analysis bug
            log(f"  {cid}: analysis failed: {e!r}; listing everything")
            plans[cid] = {"cand": set(all_nums), "certain": set(all_nums), "newfiles": {}, "bad": True}

    # choose what to execute
    jobs = []
    if not NO_RUN:
        try:
            from plan import CostModel, decide
            import runner

            cm = CostModel(tests)
            for cid, pl in plans.items():
                if pl["bad"] or not pl["cand"]:
                    pl["run"], pl["static"] = set(), set(pl["cand"])
                    continue
                py = any(r.endswith(".py") for r in pl["newfiles"])
                p = 0.6 if py else (0.15 if pl["trivial"] else 0.95)
                run, static = decide(cm, pl["cand"], pl["certain"], p)
                pl["cond"] = set()
                files = dict(pl["newfiles"])
                if pl["unprobed"] == 0 and pl["probes"]:
                    free = pl["cand"] - pl["certain"]
                    # tests far cheaper to run than to list are just run
                    big = {n for n in free if cm.cpu[n] >= 1.5 and cm.cpu[n] > 0.6 * ms[n] / 1000.0}
                    small = free - big
                    if big and small and cm.run_cpu(small) < 0.75 * cm.static_cost(big):
                        keys = []
                        for rel, specs in pl["probes"].items():
                            txt, ks = apply_probes(files[rel], specs, rel)
                            if txt is None:
                                keys = None
                                break
                            files[rel] = txt
                            keys += ks
                        if keys:
                            run, static = small, set(pl["certain"])
                            pl["cond"] = big
                            pl["keys"] = keys
                            log(f"  {cid}: conditional on probes {keys}")
                        else:
                            files = dict(pl["newfiles"])
                pl["files"] = files
                pl["run"], pl["static"] = run, static
                if run:
                    order = sorted(run, key=lambda n: (cm.setup.get(cm.cls[n], 0.05), cm.cls[n], cm.cpu[n]))
                    ids = [id_of[n] for n in order]
                    budgets = {id_of[n]: 3.0 + 4.0 * max(cm.cpu[n], ms[n] / 1000.0) for n in run}
                    wall = 15.0 + 3.0 * cm.run_cpu(run)
                    job = runner.Job(cid, None, ids, budgets, wall)
                    job.abort_on_div = bool(pl["cond"])
                    job.plan = pl
                    jobs.append(job)
                log(f"  {cid}: run {len(run)} (~{cm.run_cpu(run):.1f}s cpu), list {len(static)}")
            if jobs:
                runner.prewarm()
                ov_root = os.path.join(TMP, "ov")
                for job in jobs:
                    job.overlay = os.path.join(ov_root, job.key)
                    runner.build_overlay(ROOT, job.overlay, job.plan["files"])
                runner.run_jobs(jobs, parallel=2, deadline=deadline)
                shutil.rmtree(ov_root, ignore_errors=True)
        except Exception as e:  # noqa: BLE001
            log(f"runner failed: {e!r}; listing all candidates")
            for pl in plans.values():
                pl["run"], pl["static"], pl["cond"] = set(), set(pl["cand"]), set()
            jobs = []

    sels = {}
    for patch in patches:
        cid = patch.stem
        pl = plans[cid]
        sel = sels[cid] = set(pl.get("static", pl["cand"]))
        run = pl.get("run", set())
        if run:
            job = next(j for j in jobs if j.key == cid)
            res = (job.result or {}).get("results")
            if res is None:
                log(f"  {cid}: no run result; listing its run set")
                sel |= run | pl["cond"]
            else:
                failed = {n for n in run if res.get(id_of[n]) not in ("pass", "skip")}
                log(f"  {cid}: ran {len(run)} in {getattr(job, 'cpu', 0):.2f}s cpu, failing {len(failed)}")
                sel |= failed
                if pl["cond"]:
                    ps = job.result.get("probes", {})
                    stats = [ps.get(k, [0, 0]) for k in pl["keys"]]
                    ok = not failed and all(n >= MIN_EXEC and d == 0 for n, d in stats)
                    log(f"  {cid}: probes {stats} -> {'skip' if ok else 'list'} {len(pl['cond'])} big tests")
                    if not ok:
                        sel |= pl["cond"]
    if not NO_RUN:
        try:
            import runner

            aux_check(plans, sels, by_id, ms, runner, deadline)
        except Exception as e:  # noqa: BLE001
            log(f"aux check failed: {e!r}")
    out = []
    for patch in patches:
        cid = patch.stem
        sel = sels[cid]
        _, workers = pack([(ms[n], n) for n in sel])
        out.append(json.dumps({"commit": cid, "workers": workers}, separators=(",", ":")))
    sys.stdout.write("".join(line + "\n" for line in out))
    sys.stdout.flush()
    import resource

    s = resource.getrusage(resource.RUSAGE_SELF)
    c = resource.getrusage(resource.RUSAGE_CHILDREN)
    log(f"main cpu {s.ru_utime + s.ru_stime:.2f}s children {c.ru_utime + c.ru_stime:.2f}s "
        f"wall {time.monotonic() - t_start:.1f}s")


if __name__ == "__main__":
    main()
