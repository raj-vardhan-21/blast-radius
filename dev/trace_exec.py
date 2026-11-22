"""Dev: sqlglot lines test_execute_tpcds runs outside the phases app/lite.py hashes -> app/lite.json.

docker run ... -v $PWD/sqlglot:/src:ro -v $PWD/app:/app -v $PWD/dev:/tools br-dev python /tools/trace_exec.py /src /app/lite.json
"""
import ast
import json
import os
import pickle
import sys
import unittest

src, out = sys.argv[1], sys.argv[2]
sys.path.insert(0, src)
os.chdir(src)
os.environ["SKIP_INTEGRATION"] = "0"
ROOT = os.path.join(src, "sqlglot") + "/"
E = {}
quiet = [0]
mon = sys.monitoring
mon.use_tool_id(4, "exec")


def on_line(code, line):
    fn = code.co_filename
    if not fn.startswith(ROOT):
        return mon.DISABLE
    if quiet[0]:
        return None
    E.setdefault(os.path.relpath(fn, src), set()).add(line)
    return mon.DISABLE


def hush(f):
    def w(*a, **k):
        quiet[0] += 1
        try:
            return f(*a, **k)
        finally:
            quiet[0] -= 1
    return w


import concurrent.futures as cf  # noqa: E402


class Pool(cf.Executor):
    def __init__(self, max_workers=None, mp_context=None, initializer=None, initargs=(), **kw):
        initializer, initargs = pickle.loads(pickle.dumps((initializer, initargs)))
        initializer(*initargs)

    def submit(self, fn, /, *args, **kwargs):
        f = cf.Future()
        fn, args, kwargs = hush(lambda: pickle.loads(pickle.dumps((fn, args, kwargs))))()
        f.set_result(pickle.loads(pickle.dumps(fn(*args, **kwargs))))
        return f

    def shutdown(self, wait=True, *, cancel_futures=False):
        pass


cf.ProcessPoolExecutor = Pool
mon.register_callback(4, mon.events.LINE, on_line)
mon.set_events(4, mon.events.LINE)

import sqlglot.executor as ex  # noqa: E402
import tests.test_executor as te  # noqa: E402

te.ProcessPoolExecutor = Pool
for name in ("parse_one", "transpile", "find_tables", "load_sql_fixture_pairs"):
    setattr(te, name, hush(getattr(te, name)))
for name in ("optimize", "Plan", "ensure_schema"):
    setattr(ex, name, hush(getattr(ex, name)))

suite = unittest.TestSuite([te.TestExecutor("test_execute_tpcds")])
res = unittest.TextTestRunner(verbosity=2).run(suite)
assert res.wasSuccessful()
mon.set_events(4, 0)

names = set()
for rel, lines in E.items():
    tree = ast.parse(open(os.path.join(src, rel), encoding="utf-8").read())
    # module/class-level lines ran at (lazy) import, not in the exec phase
    body = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            body.update(range(node.body[0].lineno, node.end_lineno + 1))
        elif isinstance(node, ast.Lambda):
            body.update(range(node.body.lineno, node.body.end_lineno + 1))
    lines &= body
    for node in ast.walk(tree):
        if getattr(node, "lineno", None) in lines:
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.isidentifier():
                names.add(node.value)
json.dump({"test": "tests/test_executor.py::TestExecutor::test_execute_tpcds",
           "lines": {k: sorted(v) for k, v in sorted(E.items())}, "names": sorted(names)},
          open(out, "w"), separators=(",", ":"))
print(len(E), sum(len(v) for v in E.values()), len(names), file=sys.stderr)
