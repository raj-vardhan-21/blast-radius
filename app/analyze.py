"""Static change analysis: patch -> candidate test bitset.

Everything here errs on the side of selecting more tests; the runtime
then executes the cheap candidates to find which actually fail.
"""
import ast
import copy
import difflib
import gzip
import json
import os
import re
import sys

BODY_FIELDS = ("body", "orelse", "finalbody", "handlers")
FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)


def log(*a):
    print(*a, file=sys.stderr)


# ---------------------------------------------------------------- map


class Map:
    def __init__(self, path):
        with gzip.open(path, "rt") as f:
            d = json.load(f)
        self.n = d["n"]
        self.ids = d.get("ids")
        self.ALL = (1 << self.n) - 1
        self.sets = [int(h, 16) for h in d["sets"]]
        self.lines = d["lines"]
        self.types = d["types"]
        self.opened = d["opened"]
        self.toks = d.get("toks", {})
        self.words = d.get("words", {})
        self.refs = d.get("refs", {})
        self.tclass = d.get("tclass", {})
        self._imp = {}
        self.by_classname = {}
        for k in self.types:
            name = k.split(":", 1)[1].split(".")[-1]
            self.by_classname.setdefault(name, []).append(k)

    def line(self, f, ln):
        m = self.lines.get(f)
        if not m:
            return 0
        s = m.get(str(ln))
        return self.sets[s] if s is not None else 0

    def span(self, f, a, b):
        m = self.lines.get(f)
        if not m:
            return 0
        v = 0
        for ln in range(a, b + 1):
            s = m.get(str(ln))
            if s is not None:
                v |= self.sets[s]
        return v

    def importers(self, f):
        if f not in self._imp:
            v = 0
            for s in self.lines.get(f, {}).values():
                v |= self.sets[s]
            self._imp[f] = v
        return self._imp[f]

    def users(self, key):
        s = self.types.get(key)
        return None if s is None else self.sets[s]

    def classname_users(self, name):
        v = 0
        for k in self.by_classname.get(name, ()):
            v |= self.sets[self.types[k]]
        return v

    def tok(self, name):
        s = self.toks.get(name)
        return self.sets[s] if s is not None else 0

    def word(self, w):
        s = self.words.get(w)
        return self.sets[s] if s is not None else 0

    def opened_by(self, path):
        s = self.opened.get(path)
        v = self.sets[s] if s is not None else 0
        d = os.path.dirname(path)
        s = self.opened.get(d)
        if s is not None:
            v |= self.sets[s]
        return v


# ---------------------------------------------------------------- patches


HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _path(p):
    p = p.split("\t")[0].strip()
    if p == "/dev/null":
        return None
    if p.startswith('"') and p.endswith('"'):
        p = p[1:-1]
    if p[:2] in ("a/", "b/"):
        p = p[2:]
    return p


def parse_patch(text):
    files = []
    cur = None
    lines = text.split("\n")
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if line.startswith("diff --git "):
            cur = {"old": None, "new": None, "hunks": [], "binary": False, "created": False,
                   "deleted": False, "mode": False}
            m = re.match(r"diff --git a/(.*) b/(.*)$", line)
            if m:
                cur["old"], cur["new"] = m.group(1), m.group(2)
            files.append(cur)
        elif line.startswith("--- ") and i + 1 < n and lines[i + 1].startswith("+++ "):
            if cur is None or cur["hunks"]:
                cur = {"old": None, "new": None, "hunks": [], "binary": False, "created": False,
                       "deleted": False, "mode": False}
                files.append(cur)
            cur["old"] = _path(line[4:])
            cur["new"] = _path(lines[i + 1][4:])
            if cur["old"] is None:
                cur["created"] = True
            if cur["new"] is None:
                cur["deleted"] = True
            i += 1
        elif line.startswith("@@") and cur is not None:
            m = HUNK.match(line)
            if m:
                os_, ol, ns, nl = m.groups()
                ol = 1 if ol is None else int(ol)
                nl = 1 if nl is None else int(nl)
                h = {"os": int(os_), "ol": ol, "ns": int(ns), "nl": nl, "lines": []}
                i += 1
                co = cn = 0
                while i < n and (co < ol or cn < nl):
                    l2 = lines[i]
                    if l2.startswith("\\"):
                        h["lines"].append(l2)
                        i += 1
                        continue
                    t = l2[:1]
                    if t == "-":
                        co += 1
                    elif t == "+":
                        cn += 1
                    elif t == " " or l2 == "":
                        co += 1
                        cn += 1
                        if l2 == "":
                            l2 = " "
                    else:
                        break
                    h["lines"].append(l2)
                    i += 1
                while i < n and lines[i].startswith("\\"):
                    h["lines"].append(lines[i])
                    i += 1
                cur["hunks"].append(h)
                continue
        elif cur is not None:
            if line.startswith("new file mode"):
                cur["created"] = True
            elif line.startswith("deleted file mode"):
                cur["deleted"] = True
            elif line.startswith("Binary files") or line.startswith("GIT binary patch"):
                cur["binary"] = True
            elif line.startswith("rename from "):
                cur["old"] = line[len("rename from "):]
            elif line.startswith("rename to "):
                cur["new"] = line[len("rename to "):]
            elif line.startswith("old mode") or line.startswith("new mode"):
                cur["mode"] = True
        i += 1
    for f in files:
        if f["created"]:
            f["old"] = None
        if f["deleted"]:
            f["new"] = None
    return files


def apply_hunks(old_text, hunks):
    """Return new text, or None if the hunks do not apply."""
    old = old_text.split("\n") if old_text is not None else []
    if old_text is not None and old_text.endswith("\n"):
        old = old[:-1]
        old_nl = True
    else:
        old_nl = old_text is None
    out = []
    pos = 0
    new_nl = old_nl
    for h in hunks:
        src, dst = [], []
        last = None
        for l2 in h["lines"]:
            if l2.startswith("\\"):
                if last == "-":
                    old_nl = False
                elif last == "+":
                    new_nl = False
                else:
                    old_nl = new_nl = False
                continue
            t, body = l2[:1], l2[1:]
            if t in (" ", "-"):
                src.append(body)
            if t in (" ", "+"):
                dst.append(body)
            last = t if t != " " else " "
        start = h["os"] - 1 if h["ol"] > 0 else h["os"]
        found = None
        for delta in range(0, 200):
            for s in ((start + delta,) if delta == 0 else (start + delta, start - delta)):
                if s < pos or s < 0:
                    continue
                if old[s:s + len(src)] == src:
                    found = s
                    break
            if found is not None:
                break
        if found is None:
            return None
        out.extend(old[pos:found])
        out.extend(dst)
        pos = found + len(src)
    out.extend(old[pos:])
    text = "\n".join(out)
    if out and new_nl:
        text += "\n"
    return text


# ---------------------------------------------------------------- python ast diff


def _dump(node):
    return ast.dump(node, include_attributes=False)


def _header(node):
    c = copy.copy(node)
    for f in BODY_FIELDS + ("cases",):
        if hasattr(c, f):
            setattr(c, f, [])
    return _dump(c)


def _blocks(node):
    for f in BODY_FIELDS:
        v = getattr(node, f, None)
        if isinstance(v, list) and v:
            yield f, v


def _first_body_line(node):
    lines = []
    for f in BODY_FIELDS + ("cases",):
        v = getattr(node, f, None)
        if isinstance(v, list) and v:
            first = v[0]
            ln = getattr(first, "lineno", None)
            if ln is None and hasattr(first, "pattern"):
                ln = first.pattern.lineno
            if ln is not None:
                lines.append(ln)
    return min(lines) if lines else None


def _header_span(node):
    a = node.lineno
    if isinstance(node, FUNC + (ast.ClassDef,)) and node.decorator_list:
        a = min(d.lineno for d in node.decorator_list)
    fb = _first_body_line(node)
    b = (fb - 1) if fb is not None else node.end_lineno
    return a, max(a, b)


def _span(node):
    a = node.lineno
    if isinstance(node, FUNC + (ast.ClassDef,)) and node.decorator_list:
        a = min(d.lineno for d in node.decorator_list)
    return a, node.end_lineno


class Region:
    __slots__ = ("kind", "a", "b", "stack", "old", "new")

    def __init__(self, kind, a, b, stack, old, new):
        self.kind, self.a, self.b, self.stack, self.old, self.new = kind, a, b, list(stack), old, new

    def __repr__(self):
        return f"<{self.kind} {self.a}-{self.b} {type(self.old).__name__}/{type(self.new).__name__}>"


def diff_regions(old_tree, new_tree):
    regions = []

    def emit(kind, a, b, stack, o, n):
        regions.append(Region(kind, a, b, stack, o, n))

    def cmp_block(olds, news, stack, parent):
        od = [_dump(s) for s in olds]
        nd = [_dump(s) for s in news]
        if od == nd:
            return
        sm = difflib.SequenceMatcher(None, od, nd, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            if tag == "replace" and i2 - i1 == j2 - j1:
                for k in range(i2 - i1):
                    cmp_stmt(olds[i1 + k], news[j1 + k], stack)
                continue
            for s in olds[i1:i2]:
                a, b = _span(s)
                emit("stmt", a, b, stack, s, None)
            if j2 > j1:
                if i2 > i1:
                    for s in news[j1:j2]:
                        emit("ins", olds[i1].lineno, olds[i2 - 1].end_lineno, stack, None, s)
                    continue
                if i1 > 0:
                    anchor = olds[i1 - 1]
                    a, b = _span(anchor)
                elif i1 < len(olds):
                    anchor = olds[i1]
                    a, b = _span(anchor)
                elif parent is not None:
                    a, b = _header_span(parent)
                else:
                    a, b = 1, 1
                for s in news[j1:j2]:
                    emit("ins", a, b, stack, None, s)

    def cmp_stmt(o, n, stack):
        if type(o) is not type(n):
            a, b = _span(o)
            emit("stmt", a, b, stack, o, n)
            return
        if _header(o) != _header(n):
            a, b = _header_span(o)
            emit("header", a, b, stack, o, n)
        ob = dict(_blocks(o))
        nb = dict(_blocks(n))
        if isinstance(o, ast.Match) or set(ob) != set(nb) and not (set(ob) | set(nb)) <= set(BODY_FIELDS):
            a, b = _span(o)
            emit("stmt", a, b, stack, o, n)
            return
        inner = stack + [o]
        for f in BODY_FIELDS:
            ol = getattr(o, f, None) or []
            nl = getattr(n, f, None) or []
            if not isinstance(ol, list):
                continue
            if f == "handlers":
                if len(ol) != len(nl):
                    a, b = _span(o)
                    emit("stmt", a, b, stack, o, n)
                    continue
                for oh, nh in zip(ol, nl):
                    if _header(oh) != _header(nh):
                        a, b = _header_span(oh)
                        emit("header", a, b, inner, oh, nh)
                    cmp_block(oh.body, nh.body, inner + [oh], oh)
                continue
            if not ol and nl:
                a, b = _header_span(o)
                for s in nl:
                    emit("ins", a, b, inner, None, s)
                continue
            cmp_block(ol, nl, inner, o)

    cmp_block(old_tree.body, new_tree.body, [], None)
    return regions


def _fn_signature(fn):
    """Names bound locally and generator-ness; changes here affect the whole function."""
    names = set()
    flags = set()

    def walk(node, top):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, FUNC + (ast.ClassDef,)):
                names.add(ch.name)
                continue
            if isinstance(ch, ast.Lambda):
                continue
            if isinstance(ch, (ast.Yield, ast.YieldFrom)):
                flags.add("gen")
            if isinstance(ch, ast.Await):
                flags.add("await")
            if isinstance(ch, ast.Name) and isinstance(ch.ctx, (ast.Store, ast.Del)):
                names.add(ch.id)
            if isinstance(ch, (ast.Global, ast.Nonlocal)):
                flags.update(f"{type(ch).__name__}:{x}" for x in ch.names)
            if isinstance(ch, (ast.Import, ast.ImportFrom)):
                for al in ch.names:
                    names.add((al.asname or al.name).split(".")[0])
            if isinstance(ch, ast.ExceptHandler) and ch.name:
                names.add(ch.name)
            if isinstance(ch, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                walk(ch, False)
                continue
            walk(ch, top)

    for s in fn.body:
        if isinstance(s, FUNC + (ast.ClassDef,)):
            names.add(s.name)
        walk(ast.Module(body=[s], type_ignores=[]), True)
    a = fn.args
    for x in a.posonlyargs + a.args + a.kwonlyargs + [a.vararg, a.kwarg]:
        if x is not None:
            names.add(x.arg)
    return names, flags


def _find_new_fn(new_tree, old_fn_stack):
    """Find the function in new_tree with the same qualified path."""
    path = [s.name for s in old_fn_stack if isinstance(s, FUNC + (ast.ClassDef,))]
    node = new_tree
    for name in path:
        nxt = None
        for ch in ast.walk(node):
            if ch is not node and isinstance(ch, FUNC + (ast.ClassDef,)) and ch.name == name:
                nxt = ch
                break
        if nxt is None:
            return None
        node = nxt
    return node


def _walk_no_comp(node):
    """ast.walk that skips comprehension targets (they bind only inside the comprehension)."""
    todo = [node]
    while todo:
        n = todo.pop()
        yield n
        if isinstance(n, ast.comprehension):
            todo.append(n.iter)
            todo.extend(n.ifs)
            continue
        todo.extend(ast.iter_child_nodes(n))


def _assigned_names(stmt):
    names = set()
    if stmt is None:
        return names
    if isinstance(stmt, FUNC + (ast.ClassDef,)):
        names.add(stmt.name)
        return names
    if isinstance(stmt, (ast.Import, ast.ImportFrom)):
        for al in stmt.names:
            names.add((al.asname or al.name).split(".")[0])
            names.add(al.name.split(".")[-1])
        return names
    for node in _walk_no_comp(stmt):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.attr)
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)):
            v = node.value
            while isinstance(v, (ast.Attribute, ast.Subscript)):
                if isinstance(v, ast.Attribute):
                    names.add(v.attr)
                v = v.value
            if isinstance(v, ast.Name):
                names.add(v.id)
        elif isinstance(node, FUNC + (ast.ClassDef,)) and node is not stmt:
            names.add(node.name)
    return names


def _attr_chain(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    else:
        return None
    return parts[::-1]


def _features(nodes, M):
    """Usage features of changed literal entries. Returns bitset or None if uninformative."""
    total = 0
    for node in nodes:
        if node is None:
            return None
        v = 0
        informative = False
        for ch in ast.walk(node):
            if isinstance(ch, ast.Attribute):
                chain = _attr_chain(ch)
                if not chain or len(chain) < 2:
                    continue
                owner, attr = chain[-2], chain[-1]
                if owner == "TokenType":
                    v |= M.tok(attr)
                    informative = True
                elif owner in ("DType", "Type") and attr.isupper():
                    v |= M.tok("D:" + attr)
                    informative = True
                elif attr in M.by_classname:
                    v |= M.classname_users(attr)
                    informative = True
            elif isinstance(ch, ast.Constant) and isinstance(ch.value, str):
                ws = re.findall(r"[A-Z_][A-Z0-9_]*", ch.value.upper())
                if not ws or any(len(w) < 2 for w in ws):
                    continue
                for w in ws:
                    v |= M.word(w)
        if not informative:
            return None
        total |= v
    return total


def _literal_diff(o, n):
    """If o/n are assignments of the same target to container literals, return changed element nodes."""
    def val(s):
        if isinstance(s, ast.Assign) and len(s.targets) == 1:
            return _dump(s.targets[0]), s.value
        if isinstance(s, ast.AnnAssign) and s.value is not None:
            return _dump(s.target), s.value
        return None, None

    to, vo = val(o)
    tn, vn = val(n)
    if to is None or to != tn or type(vo) is not type(vn):
        return None
    if isinstance(vo, ast.Dict):
        eo = [(k, v) for k, v in zip(vo.keys, vo.values)]
        en = [(k, v) for k, v in zip(vn.keys, vn.values)]
        do = [_dump(k) if k else "**" + _dump(v) for k, v in eo]
        dn = [_dump(k) if k else "**" + _dump(v) for k, v in en]
        dvo = [_dump(v) for k, v in eo]
        dvn = [_dump(v) for k, v in en]
        changed = []
        sm = difflib.SequenceMatcher(None, [a + b for a, b in zip(do, dvo)], [a + b for a, b in zip(dn, dvn)], autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            for k, v in eo[i1:i2] + en[j1:j2]:
                if k is None:
                    return None
                changed.append(ast.Tuple(elts=[k, v], ctx=ast.Load()))
        return changed
    if isinstance(vo, (ast.Set, ast.List, ast.Tuple)):
        do = [_dump(e) for e in vo.elts]
        dn = [_dump(e) for e in vn.elts]
        changed = []
        sm = difflib.SequenceMatcher(None, do, dn, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            for e in vo.elts[i1:i2] + vn.elts[j1:j2]:
                if isinstance(e, ast.Starred):
                    return None
                changed.append(e)
        return changed
    return None


def _module_name(path):
    p = path[:-3] if path.endswith(".py") else path
    if p.endswith("/__init__"):
        p = p[: -len("/__init__")]
    return p.replace("/", ".")


# ---------------------------------------------------------------- probes

MUTATORS = {
    "pop", "append", "extend", "add", "remove", "update", "insert", "clear", "discard", "setdefault",
    "popitem", "send", "throw", "close", "set", "replace", "pop_comments", "transform", "write",
    "seek", "read", "readline", "next", "__next__", "push", "appendleft", "popleft", "sort", "reverse",
    "execute", "advance", "_advance", "_retreat", "_match", "_match_set", "_match_text_seq",
    "_match_texts", "_match_pair", "_parse", "retreat", "_add", "_set", "setattr", "delattr",
    "exec", "eval", "print", "input", "open", "copy", "deepcopy",
}


def _probe_safe(node):
    for ch in ast.walk(node):
        if isinstance(ch, (ast.Yield, ast.YieldFrom, ast.Await, ast.NamedExpr, ast.Starred, ast.Slice,
                           ast.JoinedStr, ast.Lambda, ast.GeneratorExp)):
            return False
        if isinstance(ch, ast.Name) and ch.id in ("super", "__class__", "locals", "vars"):
            return False
        if isinstance(ch, ast.Call):
            f = ch.func
            name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None
            if name is None or name in MUTATORS or name.startswith(("_parse", "_match", "_advance")):
                return False
    return True


def _diff_expr(o, n):
    """Smallest pair of differing expression nodes, ignoring statement bodies."""
    if type(o) is not type(n):
        return (o, n) if isinstance(o, ast.expr) and isinstance(n, ast.expr) else None
    diffs = []
    for field in o._fields:
        if isinstance(o, ast.stmt) and field in BODY_FIELDS + ("cases",):
            continue
        a = getattr(o, field, None)
        b = getattr(n, field, None)
        if isinstance(a, ast.AST) and isinstance(b, ast.AST):
            if _dump(a) != _dump(b):
                diffs.append((a, b))
        elif isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                return (o, n) if isinstance(o, ast.expr) else None
            for x, y in zip(a, b):
                if isinstance(x, ast.AST) and isinstance(y, ast.AST):
                    if _dump(x) != _dump(y):
                        diffs.append((x, y))
                elif x != y:
                    return (o, n) if isinstance(o, ast.expr) else None
        elif a != b and not (isinstance(a, ast.AST) or isinstance(b, ast.AST)):
            return (o, n) if isinstance(o, ast.expr) else None
        elif (a is None) != (b is None):
            return (o, n) if isinstance(o, ast.expr) else None
    if len(diffs) == 1:
        r = _diff_expr(*diffs[0])
        if r is not None:
            return r
    if diffs and isinstance(o, ast.expr):
        return (o, n)
    return None


def make_probe(o, n, new_src):
    pair = _diff_expr(o, n)
    if pair is None:
        return None
    eo, en = pair
    if not (isinstance(eo, ast.expr) and isinstance(en, ast.expr)):
        return None
    ctx = getattr(en, "ctx", None)
    if ctx is not None and not isinstance(ctx, ast.Load):
        return None
    # the probed node must not sit inside an f-string, comprehension or lambda of the statement
    for ch in ast.walk(n):
        if isinstance(ch, (ast.JoinedStr, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.Lambda)):
            if any(x is en for x in ast.walk(ch)):
                return None
    if not (_probe_safe(eo) and _probe_safe(en)):
        return None
    seg = ast.get_source_segment(new_src, en)
    if seg is None:
        return None
    try:
        old_txt = ast.unparse(eo)
    except Exception:  # noqa: BLE001
        return None
    return {"lineno": en.lineno, "col": en.col_offset, "end_lineno": en.end_lineno,
            "end_col": en.end_col_offset, "old": old_txt, "new": seg}


def apply_probes(text, specs, prefix):
    """Splice probe calls into source text; returns (text, keys)."""
    lines = text.split("\n")
    keys = []
    done = []
    for spec in sorted(specs, key=lambda s: (s["lineno"], s["col"]), reverse=True):
        a = (spec["lineno"], spec["col"])
        b = (spec["end_lineno"], spec["end_col"])
        if any(not (b <= d[0] or a >= d[1]) for d in done):
            return None, []
        done.append((a, b))
        k = f"{prefix}:{spec['lineno']}:{spec['col']}"
        l1 = lines[spec["lineno"] - 1].encode()
        l2 = lines[spec["end_lineno"] - 1].encode()
        head = l1[: spec["col"]].decode()
        tail = l2[spec["end_col"]:].decode()
        new_lines = spec["new"].split("\n")
        new_lines[0] = f"__br_probe__({k!r}, lambda: ({spec['old']}), lambda: (" + new_lines[0]
        new_lines[-1] = new_lines[-1] + "))"
        new_lines[0] = head + new_lines[0]
        new_lines[-1] = new_lines[-1] + tail
        lines[spec["lineno"] - 1: spec["end_lineno"]] = new_lines
        keys.append(k)
    out = "\n".join(lines)
    try:
        compile(out, prefix, "exec", dont_inherit=True)
    except SyntaxError:
        return None, []
    return out, keys


class PyAnalysis:
    def __init__(self, M, path, old_src, new_src):
        self.M = M
        self.path = path
        self.old_src = old_src
        self.new_src = new_src
        self.notes = []
        self.probes = []
        self.unprobed = 0
        # for lite.py: changed function bodies (old line spans) and module/class-level names
        self.lite_ok = True
        self.lite_spans = []
        self.lite_names = set()

    def lite_note(self, r):
        if all(_is_str_expr(s) for s in (r.old, r.new) if s is not None):
            return
        fns = [i for i, s in enumerate(r.stack) if isinstance(s, FUNC + (ast.Lambda,))]
        if fns:
            outer = r.stack[fns[0]]
            if isinstance(outer, ast.Lambda):
                self.lite_ok = False
            else:
                self.lite_spans.append((outer.body[0].lineno, outer.end_lineno))
            return
        names = set()
        for s in (r.old, r.new):
            if s is None:
                continue
            if isinstance(s, ast.ClassDef):
                self.lite_ok = False
            for node in ast.walk(s):
                if isinstance(node, ast.stmt):
                    names |= _assigned_names(node)
        if isinstance(r.old, FUNC):
            self.lite_spans.append((r.old.body[0].lineno, r.old.end_lineno))
        if not names:
            self.lite_ok = False
        self.lite_names |= names

    def run(self):
        M = self.M
        try:
            old_tree = ast.parse(self.old_src)
        except SyntaxError:
            self.notes.append("old-unparsable")
            return M.importers(self.path), True
        try:
            new_tree = ast.parse(self.new_src)
        except (SyntaxError, ValueError):
            self.notes.append("syntax-error")
            return M.importers(self.path), True
        try:
            compile(self.new_src, self.path, "exec", dont_inherit=True)
        except SyntaxError:
            self.notes.append("compile-error")
            return M.importers(self.path), True
        self.new_tree = new_tree
        regions = diff_regions(old_tree, new_tree)
        if not regions:
            self.notes.append("ast-equal")
            return 0, False
        cand = 0
        for r in regions:
            self.lite_note(r)
            c = self.region(r)
            self.notes.append(f"{r!r}:{bin(c).count('1')}")
            cand |= c
        return cand, False

    def region(self, r):
        M, path = self.M, self.path
        fns = [i for i, s in enumerate(r.stack) if isinstance(s, FUNC + (ast.Lambda,))]
        if fns:
            fn = r.stack[fns[-1]]
            if (r.stack[-1] is fn and fn.body and r.old is fn.body[0] and _is_str_expr(r.old)
                    and r.new is not None and _is_str_expr(r.new)):
                return self.doc_users(r.old, r.new)
            nf = _find_new_fn(self.new_tree, r.stack[: fns[-1] + 1])
            whole = nf is None or _fn_signature(fn) != _fn_signature(nf)
            if whole:
                self.unprobed += 1
                a, b = _span(fn)
                return M.span(path, a, b)
            spec = None
            if r.old is not None and r.new is not None and type(r.old) is type(r.new):
                spec = make_probe(r.old, r.new, self.new_src)
            if spec is None:
                self.unprobed += 1
            else:
                self.probes.append(spec)
            return M.span(path, r.a, r.b)
        self.unprobed += 1
        # module or class level
        cls = [s for s in r.stack if isinstance(s, ast.ClassDef)]
        stmt = r.old if r.old is not None else r.new
        if r.kind == "header" and isinstance(r.old, FUNC):
            # a def's signature/decorators: affects whoever calls it
            a, b = _span(r.old)
            c = M.span(path, a, b)
            if [_dump(d) for d in r.old.decorator_list] != [_dump(d) for d in r.new.decorator_list]:
                c |= self.names_users({r.old.name}, cls)
            return c
        if isinstance(stmt, FUNC) and r.kind in ("stmt", "ins"):
            c = 0
            if r.old is not None:
                a, b = _span(r.old)
                c |= M.span(path, a, b)
            return c | self.names_users(_assigned_names(r.old) | _assigned_names(r.new), cls)
        if r.kind == "header" and isinstance(r.old, ast.ClassDef):
            key = _module_name(path) + ":" + ".".join([c.name for c in cls] + [r.old.name])
            u = M.users(key)
            return (u or 0) | self.names_users({r.old.name}, cls)
        if r.kind == "header" and _first_body_line(r.old) is not None:
            names = set()
            for s in (r.old, r.new):
                for node in ast.walk(s):
                    if isinstance(node, ast.stmt):
                        names |= _assigned_names(node)
            if not names:
                return M.importers(path)
            return self.names_users(names, cls)
        names = _assigned_names(r.old) | _assigned_names(r.new)
        if all(_is_str_expr(s) for s in (r.old, r.new) if s is not None):
            if r.old is not None and r.new is not None:
                return self.doc_users(r.old, r.new)
            return self.names_users({"__doc__"}, [])
        if not names:
            # bare expression / call at import time
            return M.importers(path)
        unbound = 0
        removed = _assigned_names(r.old) - _assigned_names(r.new)
        if removed and _loads_any(self.new_tree, removed):
            # a later read of a name no longer bound here can fail at import
            self.notes.append("unbound")
            self.lite_ok = False
            unbound = M.importers(path)
        data = all(isinstance(s, (ast.Assign, ast.AnnAssign)) for s in (r.old, r.new) if s is not None)
        base = self.names_users(names, cls, data) | unbound
        if r.old is not None and r.new is not None and not unbound:
            changed = _literal_diff(r.old, r.new)
            if changed is not None:
                feat = _features(changed, M)
                if feat is not None:
                    self.notes.append("refined")
                    return base & feat
        return base

    def doc_users(self, old, new):
        # pytest does not collect test_docs' load_tests doctests; the only reader of __doc__
        # (the Expr metaclass) just checks it for emptiness
        if bool(old.value.value) == bool(new.value.value):
            self.notes.append("docstring")
            return 0
        return self.names_users({"__doc__"}, [])

    def names_users(self, names, cls, data=False):
        """Tests that could observe a module/class level binding of `names`.

        With `data`, class/module-level stores and plain imports of the name are not readers.
        """
        M, path = self.M, self.path
        mod = _module_name(path)
        chain = [mod.split(".")[-1]] + [c.name for c in cls]
        U = None
        if cls:
            U = M.users(mod + ":" + ".".join(c.name for c in cls))
            if U is None and path.startswith("tests/"):
                U = self.test_class_users(cls)
        total = 0
        clsq = ".".join(c.name for c in cls)
        for name in names:
            r = M.refs.get(name)
            if r is None:
                continue
            fbits = M.sets[r[0]]
            if U is not None:
                fbits &= U
            total |= fbits
            for ent in r[1]:
                owner, qual = ent[0], ent[1]
                if data and len(ent) > 2 and ent[2] in ("s", "i"):
                    continue
                ofile, oq = owner.split("::", 1)
                if qual:
                    if qual.split(".")[-1] != chain[-1]:
                        continue
                elif cls and not (ofile == path and _scope_inside(oq, [c.name for c in cls])):
                    continue
                u = self.owner_users(ofile, oq)
                if U is not None and ofile == path and oq == clsq:
                    u &= U
                total |= u
        return total

    def owner_users(self, ofile, oq):
        M = self.M
        if oq:
            u = M.users(_module_name(ofile) + ":" + oq)
            if u is not None:
                return u
            if ofile.startswith("tests/"):
                return self.test_class_users_by_name(oq.split(".")[0], ofile)
        return M.importers(ofile)

    def test_class_users(self, cls):
        return self.test_class_users_by_name(cls[0].name, self.path)

    def test_class_users_by_name(self, name, path):
        s = self.M.tclass.get(_module_name(path) + ":" + name)
        if s is None:
            return self.M.importers(path)
        return self.M.sets[s]


def _scope_inside(oq, clsnames):
    parts = oq.split(".") if oq else []
    return parts[: len(clsnames)] == clsnames


def _is_str_expr(s):
    return isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str)


def _loads_any(tree, names):
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in names and not isinstance(node.ctx, ast.Store):
            return True
        if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) and node.target.id in names:
            return True
    return False


CODING = re.compile(r"^[ \t\f]*#.*?coding[:=][ \t]*([-\w.]+)")


def _coding(text):
    if text is None:
        return None
    for line in text.split("\n")[:2]:
        m = CODING.match(line)
        if m:
            return m.group(1)
    return None


# ---------------------------------------------------------------- commit


class Analysis:
    def __init__(self, M, root):
        self.M = M
        self.root = root

    def read(self, rel):
        p = os.path.join(self.root, rel)
        try:
            with open(p, encoding="utf-8", newline="") as f:
                return f.read()
        except (OSError, UnicodeDecodeError):
            return None

    def commit(self, text):
        """Returns (cand bitset, certain bitset, new_files {rel: text|None}, notes)."""
        M = self.M
        files = parse_patch(text)
        cand = 0
        certain = 0
        newfiles = {}
        notes = []
        self.probes = {}
        self.unprobed = 0
        self.data_trivial = True
        self.lite = {"ok": bool(files), "spans": {}, "names": set()}
        if not files:
            notes.append("empty-patch")
        for fd in files:
            old, new = fd["old"], fd["new"]
            rel = new or old
            if (fd["binary"] or fd["mode"] or old != new or not fd["hunks"]
                    or not (rel.startswith("sqlglot/") and rel.endswith(".py")
                            or rel.startswith("tests/fixtures/") and rel.endswith(".sql"))):
                self.lite["ok"] = False
            if fd["binary"]:
                self.unprobed += 1
                notes.append(f"binary:{rel}")
                c = self.file_users(old) | self.file_users(new)
                cand |= c
                newfiles[rel] = False  # unknown content
                continue
            old_text = self.read(old) if old else None
            if old and old_text is None:
                notes.append(f"missing-old:{old}")
            if fd["hunks"]:
                new_text = apply_hunks(old_text if old_text is not None else ("" if old is None else None), fd["hunks"]) if (old_text is not None or old is None) else None
                if new_text is None:
                    self.unprobed += 1
                    notes.append(f"apply-failed:{rel}")
                    cand |= self.file_users(old) | self.file_users(new)
                    newfiles[rel] = False
                    continue
            else:
                new_text = old_text
            if new is None:
                self.unprobed += 1
                newfiles[old] = None
                c = self.file_users(old)
                if old.endswith(".py"):
                    certain |= c
                cand |= c
                notes.append(f"deleted:{old}")
                continue
            if old is not None and old != new:
                self.unprobed += 1
                newfiles[old] = None
                cand |= self.file_users(old)
                notes.append(f"renamed:{old}->{new}")
            newfiles[new] = new_text
            if old is not None and old == new and new_text == old_text and not fd["mode"]:
                continue
            c, cert = self.file_change(new, old_text if old == new else None, new_text)
            if not new.endswith(".py"):
                for h in fd["hunks"]:
                    for l2 in h["lines"]:
                        if l2[:1] in "+-" and l2[1:].strip() and not l2[1:].startswith("--"):
                            self.data_trivial = False
                if old != new or not fd["hunks"]:
                    self.data_trivial = False
            cand |= c
            if cert:
                certain |= c
        return cand, certain, newfiles, notes

    def file_users(self, rel):
        if rel is None:
            return 0
        M = self.M
        if rel.endswith(".py"):
            return M.importers(rel) | M.opened_by(rel)
        return M.opened_by(rel)

    def file_change(self, rel, old_text, new_text):
        M = self.M
        if rel.endswith(".py"):
            if not (rel.startswith("sqlglot/") or rel.startswith("tests/")):
                return 0, False
            if old_text is None:
                # new module: only matters if something imports it (another change would)
                base = os.path.basename(rel)
                self.unprobed += 1
                if base == "conftest.py":
                    return M.ALL, False
                return 0, False
            pa = PyAnalysis(M, rel, old_text, new_text)
            c, cert = pa.run()
            log(f"  {rel}: {' '.join(pa.notes)[:300]}")
            self.probes[rel] = pa.probes
            self.unprobed += pa.unprobed + (1 if cert else 0)
            if cert or not pa.lite_ok:
                self.lite["ok"] = False
            self.lite["spans"].setdefault(rel, []).extend(pa.lite_spans)
            self.lite["names"] |= pa.lite_names
            if _coding(old_text) != _coding(new_text):
                self.unprobed += 1
                self.lite["ok"] = False
                return M.importers(rel), True
            if pa.notes == ["ast-equal"]:
                return 0, False
            return c | M.opened_by(rel), cert
        # data files
        self.unprobed += 1
        c = M.opened_by(rel)
        if rel.startswith("sqlglot/") and not c:
            c = 0
        return c, False
