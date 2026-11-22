#!/bin/sh
# Build the image (through the local proxy) and run it on the sample under the scorer's limits.
set -e
S=${1:-$PWD/sample}
docker build -q --build-arg http_proxy=$http_proxy --build-arg https_proxy=$https_proxy --platform linux/amd64 -t blast-radius-entry . > /dev/null
docker run --rm --network none --cpuset-cpus 0-1 --cpus 2 --memory 4096m --memory-swap 4096m --pids-limit 256 --read-only \
  --tmpfs /tmp:rw,size=64m,mode=1777 --cap-drop ALL --security-opt no-new-privileges --user 65534:65534 \
  --env HOME=/tmp -e BR_FAIL_FRAC -e BR_CPU_SCALE -v $S/commits:/data/commits:ro -v $S/tests.json:/data/tests.json:ro \
  blast-radius-entry 2>/tmp/err.txt > output.jsonl
grep -E "conditional|probes|ran |aux|main cpu|crash|Error|failed" /tmp/err.txt | grep -v candidates | cut -c1-200
python3 score.py $S $S/answers.json output.jsonl | head -2
SIM=$(python3 score.py $S $S/answers.json output.jsonl | sed -n 's/Simulated: *\([0-9.]*\).*/\1/p')
CPU=$(sed -n 's/main cpu \([0-9.]*\)s children \([0-9.]*\)s.*/\1 \2/p' /tmp/err.txt)
python3 -c "import sys; s=float('$SIM'); a,b=map(float,'$CPU'.split()); print(f'TOTAL {s+a+b:.1f} (sim {s:.1f} cpu {a+b:.1f})')"
