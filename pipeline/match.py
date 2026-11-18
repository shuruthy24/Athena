"""Match OTP walk/bike legs to OSM ways by location, not by name.

OTP's step names are unreliable for scoring: unnamed ways come back as "path" or "sidewalk", and some steps carry
a cycle-route name. Instead we follow each leg's route line (OTP's encoded polyline), take a point every SAMPLE_M
metres, and attach it to the nearest OSM way within MAX_SNAP_M. A point stays on the previous way while that way
is still within MAX_SNAP_M, so a route along a road does not flip between the road and its footpath. Points with
no way nearby are unmatched, which the scoring counts as unknown.
"""
from __future__ import annotations

import math
from collections import defaultdict

from .geo import haversine_m, point_segment_m, to_xy

SAMPLE_M = 10.0
MAX_SNAP_M = 15.0


def decode_polyline(encoded: str, precision: int = 5) -> list[tuple[float, float]]:
    """Google encoded polyline -> [(lat, lon)]."""
    coords, index, lat, lon, factor = [], 0, 0, 0, 10 ** precision
    while index < len(encoded):
        for which in (0, 1):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if which == 0:
                lat += delta
            else:
                lon += delta
        coords.append((lat / factor, lon / factor))
    return coords


def sample_line(coords: list[tuple[float, float]], step_m: float = SAMPLE_M) -> list[tuple[float, float, float]]:
    """Points every step_m along the line, as (lat, lon, metres this point stands for)."""
    out: list[tuple[float, float, float]] = []
    for (a_lat, a_lon), (b_lat, b_lon) in zip(coords, coords[1:]):
        seg = haversine_m(a_lat, a_lon, b_lat, b_lon)
        if seg == 0:
            continue
        n = max(1, math.ceil(seg / step_m))
        for i in range(n):
            t = (i + 0.5) / n
            out.append((a_lat + t * (b_lat - a_lat), a_lon + t * (b_lon - a_lon), seg / n))
    return out


class WayIndex:
    """Grid index over the segments of many ways, for 'nearest way to this point'."""

    def __init__(self, ways: list[dict], cell_m: float = 50.0):
        """ways: [{"way_id" (or "id"): ..., "coords": [(lat, lon), ...]}]"""
        self.cell = cell_m
        self.segs: list[tuple[object, tuple[float, float], tuple[float, float]]] = []
        self.cells: dict[tuple[int, int], list[int]] = defaultdict(list)
        for w in ways:
            xy = [to_xy(lat, lon) for lat, lon in w["coords"]]
            for a, b in zip(xy, xy[1:]):
                i = len(self.segs)
                self.segs.append((w["way_id"] if "way_id" in w else w["id"], a, b))
                for cx in range(int(min(a[0], b[0]) // cell_m), int(max(a[0], b[0]) // cell_m) + 1):
                    for cy in range(int(min(a[1], b[1]) // cell_m), int(max(a[1], b[1]) // cell_m) + 1):
                        self.cells[(cx, cy)].append(i)

    def distances(self, lat: float, lon: float, radius_m: float = MAX_SNAP_M) -> dict[object, float]:
        """{way id: distance in metres} for every way within radius_m of the point."""
        p = to_xy(lat, lon)
        cx, cy = int(p[0] // self.cell), int(p[1] // self.cell)
        r = int(radius_m // self.cell) + 1
        best: dict[object, float] = {}
        for x in range(cx - r, cx + r + 1):
            for y in range(cy - r, cy + r + 1):
                for i in self.cells.get((x, y), ()):
                    wid, a, b = self.segs[i]
                    d = point_segment_m(p, a, b)
                    if d <= radius_m and d < best.get(wid, math.inf):
                        best[wid] = d
        return best


def match_line(coords: list[tuple[float, float]], index: WayIndex, total_m: float | None = None,
               step_m: float = SAMPLE_M, radius_m: float = MAX_SNAP_M) -> list[tuple[object, float]]:
    """[(way id or None, metres)] along the line, consecutive points on the same way merged. When total_m is
    given, the metres are scaled so they add up to it (OTP's own leg distance)."""
    runs: list[list] = []
    current = None
    for lat, lon, m in sample_line(coords, step_m):
        near = index.distances(lat, lon, radius_m)
        if current is not None and current in near:
            way = current                              # stay on the way we are following
        else:
            way = min(near, key=near.get) if near else None
        current = way
        if runs and runs[-1][0] == way:
            runs[-1][1] += m
        else:
            runs.append([way, m])
    length = sum(m for _, m in runs)
    scale = (total_m / length) if total_m and length else 1.0
    return [(way, m * scale) for way, m in runs]
