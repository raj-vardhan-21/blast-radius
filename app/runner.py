"""Run selected sqlglot tests against a patched tree, in forked children.

The parent pre-imports heavy third-party modules once (never sqlglot), then
forks one child per commit. Each child puts that commit's overlay tree first
on sys.path, imports the test modules fresh, and runs the tests with unittest.
"""
import json
import os
import pickle
import signal
import sys
import time

TMP = os.environ.get("BR_TMP", "/tmp")
DEBUG = os.environ.get("BR_DEBUG") == "1"


def _dbg(test, err):
    import traceback

    tb = "".join(traceback.format_exception(*err))
    log(f"    FAIL {test}: {tb[-1500:]}")


def log(*a):
    print(*a, file=sys.stderr, flush=True)


_warm = False


def prewarm():
    global _warm
    if _warm:
        return
    _warm = True
    for m in ("unittest", "unittest.mock", "concurrent.futures", "multiprocessing", "doctest", "decimal",
              "datetime", "zoneinfo", "sqlite3", "numpy", "pandas", "pandas.testing", "duckdb", "pytz",
              "dateutil", "tempfile", "runpy", "setuptools", "setuptools.command.build_ext"):
        try:
            __import__(m)
        except Exception as e:  # noqa: BLE001
            log(f"prewarm {m}: {e!r}")


# ------------------------------------------------------------------ overlay


def build_overlay(base, dest, changes):
    """dest mirrors base via symlinks; files in `changes` (rel -> text|None) are replaced/removed."""
    os.makedirs(dest, exist_ok=True)
    realdirs = {""}
    for rel in changes:
        parts = rel.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            realdirs.add("/".join(parts[:i]))

    def fill(rel_dir):
        src = os.path.join(base, rel_dir) if rel_dir else base
        dst = os.path.join(dest, rel_dir) if rel_dir else dest
        os.makedirs(dst, exist_ok=True)
        try:
            names = os.listdir(src)
        except FileNotFoundError:
            names = []
        for name in names:
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if rel in realdirs or rel in changes:
                continue
            os.symlink(os.path.join(src, name), os.path.join(dst, name))

    for d in sorted(realdirs, key=len):
        fill(d)
    for rel, text in changes.items():
        p = os.path.join(dest, rel)
        if text is None:
            continue
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text)
    return dest


# ------------------------------------------------------------------ child side


class TestTimeout(BaseException):
    pass


class _InlineFuture:
    def __init__(self, fn, args, kwargs):
        import concurrent.futures as cf

        self._f = cf.Future()
        try:
            fn, args, kwargs = pickle.loads(pickle.dumps((fn, args, kwargs)))
            r = fn(*args, **kwargs)
            self._f.set_result(pickle.loads(pickle.dumps(r)))
        except TestTimeout:
            raise
        except Exception as e:  # noqa: BLE001
            self._f.set_exception(e)

    def __getattr__(self, name):
        return getattr(self._f, name)


def _install_inline_pool():
    import concurrent.futures as cf

    class InlinePool(cf.Executor):
        def __init__(self, max_workers=None, mp_context=None, initializer=None, initargs=(), **kw):
            if initializer is not None:
                initializer, initargs = pickle.loads(pickle.dumps((initializer, initargs)))
                initializer(*initargs)

        def submit(self, fn, /, *args, **kwargs):
            return _InlineFuture(fn, args, kwargs)._f

        def shutdown(self, wait=True, *, cancel_futures=False):
            pass

    cf.ProcessPoolExecutor = InlinePool
    import concurrent.futures.process as cfp

    cfp.ProcessPoolExecutor = InlinePool


_SCALARS = (int, float, str, bytes, bool, type(None), complex)


def _same(a, b):
    if a is b:
        return True
    t = type(a)
    if t is not type(b):
        return False
    if t in _SCALARS:
        return a == b
    if t in (tuple, frozenset):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b)) if t is tuple else a == b
    return False


def _install_probe(pstats):
    import builtins

    def probe(k, old, new):
        v = new()
        s = pstats.get(k)
        if s is None:
            s = pstats[k] = [0, 0]
        s[0] += 1
        if s[1] == 0 and s[0] <= 20000:
            try:
                o = old()
            except TestTimeout:
                raise
            except BaseException:  # noqa: BLE001
                s[1] += 1
                return v
            try:
                if not _same(o, v):
                    s[1] += 1
            except Exception:  # noqa: BLE001
                s[1] += 1
        return v

    builtins.__br_probe__ = probe


def _child(overlay, ids, budgets, out_path, abort_on_div=False):
    import unittest

    os.setpgid(0, 0)
    os.chdir(overlay)
    sys.path.insert(0, overlay)
    os.environ["SKIP_INTEGRATION"] = "0"
    os.cpu_count = lambda: 1
    if hasattr(os, "process_cpu_count"):
        os.process_cpu_count = lambda: 1
    _install_inline_pool()

    results = {}
    cpu = {}
    timed_out = [False]
    pstats = {}
    _install_probe(pstats)

    def on_alarm(signum, frame):
        raise TestTimeout("test timed out")

    signal.signal(signal.SIGALRM, on_alarm)

    by_class = {}
    for tid in ids:
        f, cls, meth = tid.split("::")
        by_class.setdefault((f, cls), []).append(meth)

    class Result(unittest.TestResult):
        def _mark(self, test, status):
            tid = getattr(test, "_br_id", None)
            if tid is None:
                desc = str(test)
                # class/module fixture failure: fail everything in that class
                for (f, cls), meths in cur_group:
                    for m in meths:
                        results[f"{f}::{cls}::{m}"] = status
                return
            if results.get(tid) in ("fail", "timeout") and status == "pass":
                return
            results[tid] = status

        def addError(self, test, err):
            if DEBUG:
                _dbg(test, err)
            if err[0] is TestTimeout:
                self._mark(test, "timeout")
                timed_out[0] = True
                self.stop()
            else:
                self._mark(test, "fail")

        def addFailure(self, test, err):
            if DEBUG:
                _dbg(test, err)
            self._mark(test, "fail")

        def addSubTest(self, test, subtest, err):
            if err is not None:
                if err[0] is TestTimeout:
                    self._mark(test, "timeout")
                    timed_out[0] = True
                    self.stop()
                else:
                    self._mark(test, "fail")

        def addUnexpectedSuccess(self, test):
            self._mark(test, "fail")

        def addSuccess(self, test):
            self._mark(test, "pass")

        def addSkip(self, test, reason):
            self._mark(test, "skip")

        def addExpectedFailure(self, test, err):
            self._mark(test, "pass")

        def startTest(self, test):
            super().startTest(test)
            tid = getattr(test, "_br_id", None)
            self._t0 = time.process_time()
            signal.setitimer(signal.ITIMER_REAL, budgets.get(tid, 30.0))

        def stopTest(self, test):
            signal.setitimer(signal.ITIMER_REAL, 0)
            tid = getattr(test, "_br_id", None)
            if tid:
                cpu[tid] = round(time.process_time() - self._t0, 4)
            super().stopTest(test)
            if abort_on_div and any(s[1] for s in pstats.values()):
                timed_out[0] = True
                self.stop()

    import importlib

    files = {}
    for (f, cls), meths in by_class.items():
        files.setdefault(f, []).append((cls, meths))
    for f, groups in files.items():
        if timed_out[0]:
            break
        modname = f[:-3].replace("/", ".")
        signal.setitimer(signal.ITIMER_REAL, 60)
        try:
            mod = importlib.import_module(modname)
        except BaseException as e:  # noqa: BLE001
            signal.setitimer(signal.ITIMER_REAL, 0)
            log(f"    import {modname} failed: {e!r}"[:300])
            for cls, meths in groups:
                for m in meths:
                    results[f"{f}::{cls}::{m}"] = "fail"
            continue
        signal.setitimer(signal.ITIMER_REAL, 0)
        suite = unittest.TestSuite()
        cur_group = []
        for cls, meths in groups:
            k = getattr(mod, cls, None)
            if k is None:
                for m in meths:
                    results[f"{f}::{cls}::{m}"] = "fail"
                continue
            cur_group.append(((f, cls), meths))
            for m in meths:
                try:
                    t = k(m)
                except Exception:  # noqa: BLE001
                    results[f"{f}::{cls}::{m}"] = "fail"
                    continue
                t._br_id = f"{f}::{cls}::{m}"
                suite.addTest(t)
        res = Result()
        try:
            suite.run(res)
        except TestTimeout:
            for (ff, cls), meths in cur_group:
                for m in meths:
                    results.setdefault(f"{ff}::{cls}::{m}", "timeout")
        signal.setitimer(signal.ITIMER_REAL, 0)
    for tid in ids:
        results.setdefault(tid, "fail")
    with open(out_path + ".tmp", "w") as fh:
        json.dump({"results": results, "cpu": cpu, "probes": pstats}, fh)
    os.rename(out_path + ".tmp", out_path)


# ------------------------------------------------------------------ parent side


class Job:
    def __init__(self, key, overlay, ids, budgets, wall):
        self.key, self.overlay, self.ids, self.budgets, self.wall = key, overlay, ids, budgets, wall
        self.pid = None
        self.out = os.path.join(TMP, f"res-{key}.json")
        self.result = None


def start(job):
    pid = os.fork()
    if pid == 0:
        code = 0
        try:
            sys.stdout.flush()
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, 1)
            _child(job.overlay, job.ids, job.budgets, job.out, getattr(job, "abort_on_div", False))
        except BaseException as e:  # noqa: BLE001
            log(f"child crash {e!r}")
            code = 1
        finally:
            sys.stderr.flush()
            os._exit(code)
    job.pid = pid
    job.t0 = time.monotonic()


def run_jobs(jobs, parallel=2, deadline=None):
    """Run jobs, at most `parallel` at once. Sets job.result (dict or None)."""
    pending = list(jobs)
    active = {}
    while pending or active:
        while pending and len(active) < parallel:
            if deadline is not None and time.monotonic() > deadline:
                for j in pending:
                    j.result = None
                pending = []
                break
            j = pending.pop(0)
            start(j)
            active[j.pid] = j
        if not active:
            break
        try:
            pid, status, ru = os.wait4(-1, os.WNOHANG)
        except ChildProcessError:
            pid = 0
        if pid == 0:
            now = time.monotonic()
            for j in list(active.values()):
                if now - j.t0 > j.wall or (deadline is not None and now > deadline + 5):
                    log(f"  job {j.key} over wall budget; killing")
                    _kill(j.pid)
            time.sleep(0.01)
            continue
        j = active.pop(pid, None)
        if j is None:
            continue
        j.cpu = ru.ru_utime + ru.ru_stime
        _kill(pid)
        try:
            with open(j.out) as fh:
                j.result = json.load(fh)
            os.unlink(j.out)
        except (OSError, ValueError):
            j.result = None
    return jobs


def _kill(pid):
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
