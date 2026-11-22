"""Compile per-test traces into an inverted, deduplicated map (map.json.gz)."""
import glob
import gzip
import json
import sys

src, n, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
lines, types, opened, toks, words, tclass = {}, {}, {}, {}, {}, {}
for i in range(n):
  for path in glob.glob(f"{src}/{i}.json*"):
    d = json.load(open(path))
    bit = 1 << i
    for f, ls in d["lines"].items():
        m = lines.setdefault(f, {})
        for ln in ls:
            m[ln] = m.get(ln, 0) | bit
    for t in d["types"]:
        types[t] = types.get(t, 0) | bit
    for t in d.get("toks", []):
        toks[t] = toks.get(t, 0) | bit
    for t in d.get("words", []):
        words[t] = words.get(t, 0) | bit
    for t in d.get("tclass", []):
        tclass[t] = tclass.get(t, 0) | bit
    for o in d["opened"]:
        if "__pycache__" in o:
            continue
        opened[o] = opened.get(o, 0) | bit

sets, idx = [], {}


def sid(v):
    if v not in idx:
        idx[v] = len(sets)
        sets.append(format(v, "x"))
    return idx[v]


data = {
    "n": n,
    "ids": [t["id"] for t in json.load(open(sys.argv[5]))["tests"]] if len(sys.argv) > 5 else None,
    "lines": {f: {str(ln): sid(v) for ln, v in sorted(m.items())} for f, m in lines.items()},
    "types": {t: sid(v) for t, v in types.items()},
    "opened": {o: sid(v) for o, v in opened.items()},
    "toks": {t: sid(v) for t, v in toks.items()},
    "words": {t: sid(v) for t, v in words.items()},
    "tclass": {t: sid(v) for t, v in tclass.items()},
}
data["sets"] = sets
if len(sys.argv) > 4:
    refs = json.load(open(sys.argv[4]))
    R = {}
    for ident, r in refs.items():
        v = 0
        for f, ln in r["f"]:
            v |= lines.get(f, {}).get(ln, 0)
        R[ident] = [sid(v), r["c"]]
    data["refs"] = R
with gzip.open(out, "wt", compresslevel=9) as f:
    json.dump(data, f, separators=(",", ":"))
print("sets", len(sets), file=sys.stderr)
