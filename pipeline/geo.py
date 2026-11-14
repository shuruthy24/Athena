"""Small geometry helpers (no GIS dependency): distances in metres on a local flat projection, which is
accurate to well under 1% across the ~10 km study area, plus a grid index for "points near a line"."""
from __future__ import annotations

import math
from collections import defaultdict

EARTH_M = 6_371_000.0
REF_LAT = -33.89  # centre of the study area; sets the east-west scale of the local projection


def to_xy(lat: float, lon: float) -> tuple[float, float]:
    x = math.radians(lon) * EARTH_M * math.cos(math.radians(REF_LAT))
    y = math.radians(lat) * EARTH_M
    return x, y


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_M * math.asin(math.sqrt(a))


def line_length_m(coords: list[tuple[float, float]]) -> float:
    """coords are (lat, lon) pairs."""
    return sum(haversine_m(*coords[i], *coords[i + 1]) for i in range(len(coords) - 1))


def point_segment_m(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    """Distance from point p to segment ab, all in projected metres."""
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


class PointGrid:
    """Grid index of projected points, so 'points within r metres of a line' avoids an all-pairs scan."""

    def __init__(self, points: list[tuple[float, float]], cell_m: float = 100.0):
        self.cell = cell_m
        self.points = [to_xy(lat, lon) for lat, lon in points]
        self.cells: dict[tuple[int, int], list[int]] = defaultdict(list)
        for i, (x, y) in enumerate(self.points):
            self.cells[(int(x // cell_m), int(y // cell_m))].append(i)

    def near_line(self, coords: list[tuple[float, float]], radius_m: float) -> set[int]:
        """Indexes of points within radius_m of the polyline given as (lat, lon) pairs."""
        xy = [to_xy(lat, lon) for lat, lon in coords]
        found: set[int] = set()
        for a, b in zip(xy, xy[1:]):
            x0, x1 = sorted((a[0], b[0]))
            y0, y1 = sorted((a[1], b[1]))
            for cx in range(int((x0 - radius_m) // self.cell), int((x1 + radius_m) // self.cell) + 1):
                for cy in range(int((y0 - radius_m) // self.cell), int((y1 + radius_m) // self.cell) + 1):
                    for i in self.cells.get((cx, cy), ()):
                        if i not in found and point_segment_m(self.points[i], a, b) <= radius_m:
                            found.add(i)
        return found
