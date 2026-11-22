#!/bin/sh
# Rebuild app/map.json.gz from a fresh per-test trace (run from repo root).
set -e
docker run --rm --memory 16g --memory-swap 16g --cpus 16 --pids-limit 1024 -v "$PWD/sqlglot:/src:ro" -v "$PWD/dev:/tools" -v "$PWD/maps:/out" br-dev sh -c \
  "rm -rf /out/cov && python /tools/collect.py /tools/tests.json /out/cov 16 2>&1 | tail -5 && \
   python /tools/build_refs.py /src /out/refs.json && \
   python /tools/build_map.py /out/cov 1620 /out/map.json.gz /out/refs.json /tools/tests.json && chmod -R a+rwX /out"
cp maps/map.json.gz app/map.json.gz
ls -la app/map.json.gz
