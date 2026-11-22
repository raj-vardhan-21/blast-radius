"""Worker packing: choose worker count and assignment minimising makespan + 2s/worker."""


def pack(items, max_workers=8, worker_ms=2000):
    """items: list of (ms, id). Returns (cost_ms, workers)."""
    if not items:
        return 0, []
    items = sorted(items, reverse=True)
    total = sum(ms for ms, _ in items)
    best = None
    for w in range(1, max_workers + 1):
        if w > len(items):
            break
        loads = [0] * w
        bins = [[] for _ in range(w)]
        for ms, i in items:
            k = loads.index(min(loads))
            loads[k] += ms
            bins[k].append(i)
        cost = max(loads) + worker_ms * w
        if best is None or cost < best[0]:
            best = (cost, bins)
        if items[0][0] >= total / (w + 1) and w > 1 and max(loads) == items[0][0]:
            break
    return best
