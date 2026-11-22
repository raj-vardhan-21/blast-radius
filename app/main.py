"""Blast radius selector: for each patch, choose the tests that could fail."""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze import Analysis, Map, log  # noqa: E402
from pack import pack  # noqa: E402

DATA = Path(os.environ.get("BR_DATA", "/data"))
ROOT = os.environ.get("BR_SQLGLOT", "/opt/sqlglot")
HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    t0 = time.process_time()
    tests = json.loads((DATA / "tests.json").read_text())["tests"]
    M = Map(os.path.join(HERE, "map.json.gz"))
    by_id = {t["id"]: t["number"] for t in tests}
    ms = {t["number"]: round(t["seconds"] * 1000) for t in tests}
    map_ids = M.ids or []
    to_num = [by_id.get(i) for i in map_ids]
    known = set(map_ids)
    unknown = [t for t in tests if t["id"] not in known]
    if unknown:
        log(f"{len(unknown)} tests not in map")
    all_items = [(ms[t["number"]], t["number"]) for t in tests]
    A = Analysis(M, ROOT)
    out = []
    for patch in sorted((DATA / "commits").glob("*.patch")):
        cid = patch.stem
        try:
            text = patch.read_text(encoding="utf-8", errors="replace")
            log(f"== {cid}")
            cand, certain, newfiles, notes = A.commit(text)
            nums = set()
            for i in range(M.n):
                if cand >> i & 1 and to_num[i] is not None:
                    nums.add(to_num[i])
            if cand and unknown:
                files = {tests[n]["file"] for n in nums} | set(newfiles)
                nums.update(t["number"] for t in unknown if t["file"] in files)
            _, workers = pack([(ms[n], n) for n in nums])
            log(f"  {cid}: {len(nums)} tests {notes}")
        except Exception as e:  # never risk a miss on an analysis bug
            log(f"  {cid}: analysis failed: {e!r}; running everything")
            _, workers = pack(all_items)
        out.append(json.dumps({"commit": cid, "workers": workers}, separators=(",", ":")))
    sys.stdout.write("".join(line + "\n" for line in out))
    sys.stdout.flush()
    log(f"cpu {time.process_time() - t0:.2f}s")


if __name__ == "__main__":
    main()
