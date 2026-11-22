"""Cheap stand-ins for the two most expensive tests.

test_execute_tpcds parses, pickles, optimizes and plans each query, runs the plan in the
Python executor and compares with duckdb on transpile(sql). Everything before the row
execution is hashed here per query; a query with an unchanged hash, when no change reaches
the code the row execution runs (dev/trace_exec.py -> lite.json), behaves as at the pinned
commit. Queries whose hash changed are executed for real, the way the test does it.

test_max_nodes parses one very wide statement of identical columns. mn_artifact() hashes
everything the same steps can observe on a narrower statement.
"""
import hashlib
import pickle
import time

TEST = "tests/test_executor.py::TestExecutor::test_execute_tpcds"
MN_TEST = "tests/test_parser.py::TestParser::test_max_nodes"


def _dump(o, out):
    from sqlglot.expressions import Expr
    from sqlglot.planner import Step

    if isinstance(o, Expr):
        out.append("(" + type(o).__name__)
        for k in sorted(o.args):
            out.append(k)
            _dump(o.args[k], out)
        t = getattr(o, "_type", None)
        if t is not None:
            out.append(":")
            _dump(t, out)
        c = getattr(o, "comments", None)
        if c:
            out.append(repr(c))
        out.append(")")
    elif isinstance(o, Step):
        out.append("step:" + repr(o.name))
    elif isinstance(o, dict):
        out.append("{")
        for k in sorted(o, key=repr):
            out.append(repr(k))
            _dump(o[k], out)
        out.append("}")
    elif isinstance(o, (set, frozenset)):
        parts = []
        for x in o:
            p = []
            _dump(x, p)
            parts.append("\x00".join(p))
        out.append("<" + "\x01".join(sorted(parts)) + ">")
    elif isinstance(o, (list, tuple)):
        out.append("[")
        for x in o:
            _dump(x, out)
        out.append("]")
    elif isinstance(o, type):
        out.append("T" + o.__module__ + "." + o.__qualname__)
    else:
        out.append(repr(o))


def _dump_plan(plan, out):
    memo = {}

    def step_str(step):
        s = memo.get(id(step))
        if s is None:
            parts = ["#" + type(step).__name__]
            for k, v in sorted(vars(step).items()):
                if k in ("dependents", "dependencies"):
                    continue
                parts.append(k)
                _dump(v, parts)
            deps = sorted(step_str(d) for d in step.dependencies)
            s = memo[id(step)] = "\x00".join(parts) + "\x02" + "\x03".join(deps) + "\x04"
        return s

    out.append(step_str(plan.root))


def query_hashes():
    """[(index, hash)] for every TPC-DS query the test executes."""
    from sqlglot import find_tables, parse_one, transpile
    from sqlglot.optimizer import optimize
    from sqlglot.planner import Plan
    from sqlglot.schema import ensure_schema
    from tests.helpers import TPCDS_SCHEMA, load_sql_fixture_pairs

    list(load_sql_fixture_pairs("optimizer/tpc-h/tpc-h.sql"))
    sqls = list(load_sql_fixture_pairs("optimizer/tpc-ds/tpc-ds.sql"))
    schema_blob = pickle.dumps(TPCDS_SCHEMA)
    res = []
    for i, (meta, sql, _) in enumerate(sqls):
        e = pickle.loads(pickle.dumps(parse_one(sql)))
        if not meta.get("execute"):
            continue
        out = [sql, repr(sorted(meta.items()))]
        out.append(repr(sorted(t.name for t in find_tables(e))))
        schema = ensure_schema(pickle.loads(schema_blob), dialect=None)
        opt = optimize(e, schema, leave_tables_isolated=True, dialect=None)
        _dump(opt, out)
        _dump_plan(Plan(opt), out)
        out.append(transpile(sql, write="duckdb")[0])
        res.append((i, hashlib.sha256("\x00".join(out).encode("utf-8", "surrogatepass")).hexdigest()))
    return res


def tpcds_check(base, max_exec=12, cpu_budget=8.0):
    """("same"|"differ", number of changed queries). Changed queries are run as the test runs them."""
    base = set(base)
    diff = [i for i, h in query_hashes() if h not in base]
    if not diff:
        return "same", 0
    if len(diff) > max_exec:
        return "differ", len(diff)
    t0 = time.process_time()
    import tests.test_executor as te
    from sqlglot import parse_one
    from tests.helpers import TPCDS_SCHEMA

    cls = te.TestExecutor
    cls.setUpClass()
    case = cls("test_execute_tpcds")
    te.initializer(*pickle.loads(pickle.dumps((TPCDS_SCHEMA, cls.tpcds_tables))))
    for i in diff:
        if time.process_time() - t0 > cpu_budget:
            return "differ", len(diff)
        meta, sql, _ = cls.tpcds_sqls[i]
        fn, args = pickle.loads(pickle.dumps((te.mp_execute, (parse_one(sql), meta))))
        table = pickle.loads(pickle.dumps(fn(*args)))
        if table is not None:
            case.subtestHelper(i, table, tpch=False)
    return "same", len(diff)


def mn_artifact(width=300):
    """Hash of everything test_max_nodes can observe, on `width` columns instead of 10000."""
    from sqlglot import parse_one
    from sqlglot.errors import ParseError

    out = []

    def ok(sql, **kw):
        e = parse_one(sql, **kw)
        out.append(type(e).__module__ + "." + type(e).__qualname__)
        out.append(repr(e))

    def err(sql, **kw):
        try:
            parse_one(sql, **kw)
        except ParseError as e:
            out.append("ParseError:" + str(e))
            out.append(repr(e.errors))
        else:
            out.append("no error")

    ok("SELECT 1", max_nodes=1_000_000)
    ok(f"SELECT {','.join(f'col{i}' for i in range(500))} FROM t", max_nodes=1_000_000)
    expr = "x" + "=x" * 100
    sql = f"SELECT {','.join(expr for _ in range(width))}"
    # the test's limits are 1_000_000 and 50_000 on 10000 columns
    err(sql, max_nodes=30_000)
    err(sql, max_nodes=1_500)
    err("SELECT " + ",".join(f"x={i}" for i in range(100)), max_nodes=5)
    ok("SELECT 1, 2, 3")
    if "no error" in out:
        raise AssertionError("limit not reached")
    return hashlib.sha256("\x00".join(out).encode("utf-8", "surrogatepass")).hexdigest()


if __name__ == "__main__":
    import json
    import os
    import sys

    root = sys.argv[1]
    sys.path.insert(0, root)
    os.chdir(root)
    a = {"tpcds": [h for _, h in query_hashes()], "mn": mn_artifact()}
    b = {"tpcds": [h for _, h in query_hashes()], "mn": mn_artifact()}
    # only trust what is deterministic
    print(json.dumps({k: (v if v == b[k] else None) for k, v in a.items()}))
