"""pytest plugin: records every (file, line) executed in this process, the
sqlglot classes instantiated, and files opened under the repo. Written to
$TRACE_OUT as JSON at session end."""
import builtins
import io
import json
import os
import sys

ROOT = os.environ.get("TRACE_ROOT", "/src") + "/"
OUT = os.environ["TRACE_OUT"]
mon = sys.monitoring
TOOL = 3
mon.use_tool_id(TOOL, "tracer")

lines = {}
types = set()
toks = set()
words = set()
import re
WORD = re.compile(r"[A-Z_][A-Z0-9_]*")
opened = set()
_skip = set()


def _file_ok(fn):
    return fn.startswith(ROOT)


def on_line(code, line):
    fn = code.co_filename
    if not fn.startswith(ROOT):
        return mon.DISABLE
    s = lines.get(fn)
    if s is None:
        s = lines[fn] = set()
    s.add(line)
    return mon.DISABLE


def on_start(code, offset):
    name = code.co_name
    if (name != "__init__" and name != "tokenize") or not code.co_filename.startswith(ROOT):
        return mon.DISABLE
    f = sys._getframe(1)
    if name == "tokenize":
        sql = f.f_locals.get("sql")
        if isinstance(sql, str):
            words.update(WORD.findall(sql.upper()))
        return None
    if code.co_varnames and code.co_argcount:
        self = f.f_locals.get(code.co_varnames[0])
        if self is not None:
            c = type(self)
            types.add(c)
            if c.__name__ == "DataType":
                a = f.f_locals.get("args")
                if isinstance(a, dict) and a.get("this") is not None:
                    toks.add("D:" + str(getattr(a["this"], "name", a["this"])))
            if code.co_qualname == "Token.__init__":
                tt = f.f_locals.get("token_type")
                if tt is not None:
                    toks.add(getattr(tt, "name", str(tt)))
    return None


mon.register_callback(TOOL, mon.events.LINE, on_line)
mon.register_callback(TOOL, mon.events.PY_START, on_start)
mon.set_events(TOOL, mon.events.LINE | mon.events.PY_START)

_open = builtins.open


def _rec(p):
    try:
        p = os.path.abspath(os.fspath(p))
    except Exception:
        return
    if p.startswith(ROOT):
        opened.add(p[len(ROOT):])


def open_hook(file, *a, **k):
    if not isinstance(file, int):
        _rec(file)
    return _open(file, *a, **k)


builtins.open = open_hook
io.open = open_hook
_oslist = os.listdir


def listdir_hook(p="."):
    _rec(p)
    return _oslist(p)


os.listdir = listdir_hook
_osopen = os.open


def osopen_hook(p, *a, **k):
    _rec(p)
    return _osopen(p, *a, **k)


os.open = osopen_hook

outcome = {}
tclass = set()


def pytest_runtest_setup(item):
    c = getattr(item, "cls", None)
    if c is not None:
        for b in c.__mro__:
            m = getattr(b, "__module__", "") or ""
            if m.startswith("tests"):
                tclass.add(m + ":" + b.__qualname__)


def pytest_runtest_logreport(report):
    if report.failed:
        outcome["failed"] = True


def pytest_sessionfinish(session, exitstatus):
    dump(OUT, int(exitstatus))


def dump(out, exitstatus=0):
    mon.set_events(TOOL, 0)
    tnames = set()
    for c in types:
        for b in c.__mro__:
            m = getattr(b, "__module__", "") or ""
            if m.startswith("sqlglot"):
                tnames.add(m + ":" + b.__qualname__)
    data = {
        "lines": {k[len(ROOT):]: sorted(v) for k, v in lines.items()},
        "types": sorted(tnames),
        "toks": sorted(toks),
        "words": sorted(words),
        "tclass": sorted(tclass),
        "opened": sorted(opened),
        "exit": exitstatus,
        "failed": bool(outcome.get("failed")),
    }
    with _open(out, "w") as f:
        json.dump(data, f)


if os.environ.get("TRACE_CHILD") == "1":
    import atexit

    atexit.register(lambda: dump(f"{OUT}.{os.getpid()}"))
else:
    os.environ["TRACE_CHILD"] = "1"
