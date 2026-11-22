"""TPC-DS executor test without its row execution.

test_execute_tpcds parses, pickles, optimizes and plans each query, runs the plan in the
Python executor and compares with duckdb on transpile(sql). Everything before the row
execution is hashed here; with an unchanged hash and no change reaching the code the row
execution runs (dev/trace_exec.py -> lite.json), the test behaves as at the pinned commit.
"""
import hashlib
import pickle

TEST = "tests/test_executor.py::TestExecutor::test_execute_tpcds"


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


def artifact():
    from sqlglot import find_tables, parse_one, transpile
    from sqlglot.optimizer import optimize
    from sqlglot.planner import Plan
    from sqlglot.schema import ensure_schema
    from tests.helpers import TPCDS_SCHEMA, load_sql_fixture_pairs

    h = hashlib.sha256()
    list(load_sql_fixture_pairs("optimizer/tpc-h/tpc-h.sql"))
    sqls = list(load_sql_fixture_pairs("optimizer/tpc-ds/tpc-ds.sql"))
    schema_blob = pickle.dumps(TPCDS_SCHEMA)
    for meta, sql, _ in sqls:
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
        h.update("\x00".join(out).encode("utf-8", "surrogatepass"))
    return h.hexdigest()


if __name__ == "__main__":
    import os
    import sys

    root = sys.argv[1]
    sys.path.insert(0, root)
    os.chdir(root)
    a, b = artifact(), artifact()
    # only trust the check if it is deterministic
    print(a if a == b else "")
