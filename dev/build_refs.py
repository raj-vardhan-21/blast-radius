"""Offline: identifier reference index from the pinned source.

refs[ident] = {"f": [[file, line], ...] in-function refs, "c": [owner, ...]}
owner is "file::Class.Inner" for class-body refs or "file::" for module level.
Also records, per file, the top-level module-level line spans and class spans.
"""
import ast
import json
import os
import sys

root = sys.argv[1]
out = sys.argv[2]

refs = {}


def add(ident, kind, val):
    r = refs.setdefault(ident, {"f": set(), "c": set()})
    r[kind].add(val)


class V(ast.NodeVisitor):
    def __init__(self, rel):
        self.rel = rel
        self.cls = []
        self.fn = 0

    def owner(self):
        return self.rel + "::" + ".".join(self.cls)

    def ref(self, ident, node, qual="", kind="l"):
        if self.fn:
            add(ident, "f", (self.rel, node.lineno))
        else:
            add(ident, "c", (self.owner(), qual, kind))

    @staticmethod
    def _kind(node):
        ctx = getattr(node, "ctx", None)
        return "s" if isinstance(ctx, (ast.Store, ast.Del)) else "l"

    def visit_AugAssign(self, node):
        # an augmented target is read as well as written
        t = node.target
        if isinstance(t, ast.Name):
            self.ref(t.id, t)
        else:
            self.visit(t)
        self.visit(node.value)

    def visit_ClassDef(self, node):
        for d in node.decorator_list + node.bases + [k.value for k in node.keywords]:
            self.visit(d)
        if self.fn:
            for s in node.body:
                self.visit(s)
            return
        self.cls.append(node.name)
        for s in node.body:
            self.visit(s)
        self.cls.pop()

    def _fn(self, node):
        for d in node.decorator_list:
            self.visit(d)
        self.visit(node.args)
        self.fn += 1
        for s in node.body if isinstance(node.body, list) else [node.body]:
            self.visit(s)
        self.fn -= 1

    visit_FunctionDef = visit_AsyncFunctionDef = _fn

    def visit_Lambda(self, node):
        self.visit(node.args)
        self.fn += 1
        self.visit(node.body)
        self.fn -= 1

    def visit_Name(self, node):
        self.ref(node.id, node, kind=self._kind(node))

    def visit_Attribute(self, node):
        parts = []
        v = node.value
        while isinstance(v, ast.Attribute):
            parts.append(v.attr)
            v = v.value
        if isinstance(v, ast.Name):
            parts.append(v.id)
            qual = ".".join(parts[::-1])
        else:
            qual = "?"
        self.ref(node.attr, node, qual, self._kind(node))
        self.visit(node.value)

    def visit_Constant(self, node):
        if isinstance(node.value, str) and node.value.isidentifier() and len(node.value) < 60:
            self.ref(node.value, node)

    def visit_alias(self, node):
        # an import that keeps the name only rebinds it; readers are indexed under the same name
        kind = "i" if not node.asname or node.asname == node.name.split(".")[-1] else "l"
        self.ref((node.asname or node.name).split(".")[0], node, kind=kind)
        self.ref(node.name.split(".")[-1], node, kind=kind)


for base in ("sqlglot", "tests"):
    for dp, dn, fn in os.walk(os.path.join(root, base)):
        for f in fn:
            if not f.endswith(".py"):
                continue
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, root)
            try:
                tree = ast.parse(open(p, encoding="utf-8").read())
            except SyntaxError:
                continue
            V(rel).visit(tree)

data = {k: {"f": sorted(v["f"]), "c": sorted(v["c"])} for k, v in refs.items()}
json.dump(data, open(out, "w"), separators=(",", ":"))
print(len(data), file=sys.stderr)
