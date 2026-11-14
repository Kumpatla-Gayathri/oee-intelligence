"""Half-open interval helpers: every interval is [start, end)."""


def clip(a0, a1, lo, hi):
    s, e = max(a0, lo), min(a1, hi)
    return (s, e) if e > s else None


def merge(intervals):
    """Union of intervals as a sorted list of disjoint (start, end)."""
    out = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1]:
            if e > out[-1][1]:
                out[-1] = (out[-1][0], e)
        else:
            out.append((s, e))
    return out


def subtract(lo, hi, blocks):
    """[lo, hi) minus merged blocks -> list of working segments."""
    segs, cur = [], lo
    for s, e in blocks:
        if s > cur:
            segs.append((cur, s))
        cur = max(cur, e)
    if hi > cur:
        segs.append((cur, hi))
    return segs


def seconds(a0, a1) -> float:
    return max(0.0, (a1 - a0).total_seconds())


def overlap_seconds(a0, a1, b0, b1) -> float:
    return seconds(max(a0, b0), min(a1, b1))
