"""Generate synthetic single-line mutants of the pinned sqlglot as git-style patches.

Used only to validate the selector locally (ground truth comes from running pytest).
"""
import difflib
import os
import random
import re
import sys

root, out, n, seed = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
rng = random.Random(seed)
os.makedirs(out, exist_ok=True)

py_files = []
for dp, dn, fn in os.walk(os.path.join(root, "sqlglot")):
    for f in fn:
        if f.endswith(".py"):
            py_files.append(os.path.relpath(os.path.join(dp, f), root))
fixtures = []
for dp, dn, fn in os.walk(os.path.join(root, "tests/fixtures")):
    for f in fn:
        if f.endswith(".sql"):
            fixtures.append(os.path.relpath(os.path.join(dp, f), root))
py_files.sort()
fixtures.sort()
if os.environ.get("MUT_FILES"):
    py_files = [f for f in py_files if re.search(os.environ["MUT_FILES"], f)]
test_files = sorted(
    os.path.relpath(os.path.join(dp, f), root)
    for dp, dn, fn in os.walk(os.path.join(root, "tests")) if "fixtures" not in dp
    for f in fn if f.endswith(".py")
)
WEIGHTS = [int(x) for x in os.environ.get("MUT_WEIGHTS", "70,8,15,7,0,0").split(",")]

SWAPS = [
    (r" == ", " != "), (r" != ", " == "), (r" is not ", " is "), (r" is None", " is not None"),
    (r" and ", " or "), (r" or ", " and "), (r" < ", " <= "), (r" > ", " >= "), (r" <= ", " < "),
    (r" >= ", " > "), (r" not in ", " in "), (r"if not ", "if "), (r"\bTrue\b", "False"),
    (r"\bFalse\b", "True"), (r" \+ 1\b", " - 1"), (r" - 1\b", " + 1"), (r"\[0\]", "[-1]"),
    (r"\[-1\]", "[0]"), (r" is None", " == None"), (r" is ", " == "), (r"return True", "return False"),
    (r"return False", "return True"), (r"\b(\d+)\b", None), (r'"([A-Z_]{3,})"', None),
]


def diff(rel, a, b):
    d = difflib.unified_diff(a.splitlines(True), b.splitlines(True), f"a/{rel}", f"b/{rel}", n=3)
    return f"diff --git a/{rel} b/{rel}\n--- a/{rel}\n+++ b/{rel}\n" + "".join(list(d)[2:])


made = 0
tries = 0
while made < n and tries < n * 200:
    tries += 1
    kind = rng.choices(["code", "comment", "fixture", "delete", "testcode", "docstring"], WEIGHTS)[0]
    if kind == "fixture":
        rel = rng.choice(fixtures)
    elif kind == "testcode":
        rel = rng.choice(test_files)
        kind = "code"
    else:
        rel = rng.choice(py_files)
    src = open(os.path.join(root, rel), encoding="utf-8").read()
    lines = src.split("\n")
    i = rng.randrange(len(lines))
    line = lines[i]
    s = line.strip()
    if kind == "docstring":
        if not (s.startswith('"""') and len(s) > 10):
            continue
        new = line.replace(" ", "  ", 1) if " " in s else None
        if new is None:
            continue
        lines[i] = new
        b = "\n".join(lines)
        cid = f"m{seed}x{made:03d}"
        with open(os.path.join(out, cid + ".patch"), "w") as f:
            f.write(diff(rel, src, b))
        made += 1
        continue
    if not s or s.startswith("#") or s.startswith('"""') or s.startswith("@"):
        continue
    new = None
    if kind == "code":
        rng.shuffle(SWAPS)
        for pat, rep in SWAPS:
            m = re.search(pat, line)
            if not m:
                continue
            if rep is None:
                if pat.startswith(r"\b(\d"):
                    v = int(m.group(1))
                    new = line[: m.start(1)] + str(v + rng.choice([1, -1, 2])) + line[m.end(1):]
                else:
                    w = m.group(1)
                    new = line[: m.start(1)] + w[:-1] + line[m.end(1):]
            else:
                new = line[: m.start()] + re.sub(pat, rep, line[m.start():], count=1)
            break
    elif kind == "comment":
        indent = line[: len(line) - len(line.lstrip())]
        lines.insert(i, indent + rng.choice(["# TODO: revisit", "# Cheap check first", "# noqa"]))
        new = "__inserted__"
    elif kind == "delete":
        if s.endswith((":", ",", "(", "[", "{", "\\")) or s.startswith(("return", "else", "elif", "def ", "class ", ")", "]", "}")):
            continue
        if line.count("(") != line.count(")") or line.count("[") != line.count("]"):
            continue
        indent = line[: len(line) - len(line.lstrip())]
        new = indent + "pass"
    elif kind == "fixture":
        if s.startswith("--") or s.startswith("#"):
            continue
        toks = s.split()
        if len(toks) < 2:
            continue
        j = rng.randrange(len(toks))
        toks[j] = rng.choice(["x", "1", "y.a", toks[j].lower(), toks[j] + "_"])
        new = line[: len(line) - len(line.lstrip())] + " ".join(toks)
    if new is None or new == line:
        continue
    if new != "__inserted__":
        lines[i] = new
    b = "\n".join(lines)
    if kind != "fixture":
        try:
            compile(b, rel, "exec")
        except SyntaxError:
            continue
    cid = f"m{seed}x{made:03d}"
    with open(os.path.join(out, cid + ".patch"), "w") as f:
        f.write(diff(rel, src, b))
    made += 1
print("made", made)
