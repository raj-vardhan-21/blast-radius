"""Per-commit cost vs oracle for output.jsonl on DIR."""
import json, sys
sys.path.insert(0, 'app'); from pack import pack
D = sys.argv[1]
T = json.load(open(f'{D}/tests.json'))['tests']; ms = {t['number']: round(t['seconds'] * 1000) for t in T}
ans = json.load(open(f'{D}/answers.json'))['commits']
tot = orc = 0
for l in open('output.jsonl'):
    d = json.loads(l); w = [x for x in d['workers'] if x]
    c = (max(sum(ms[n] for n in x) for x in w) + 2000 * len(w)) if w else 0
    o = pack([(ms[n], n) for n in ans[d['commit']]['failing']])[0]
    tot += c; orc += o
    if c - o > 500:
        files = [x[6:] for x in open(f'{D}/commits/{d["commit"]}.patch').read().splitlines() if x.startswith('+++ b/')]
        print(f"{d['commit']} cost={c/1000:.1f} oracle={o/1000:.1f} nfail={len(ans[d['commit']]['failing'])} {files}")
print(f"total {tot/1000:.1f} oracle {orc/1000:.1f}")
