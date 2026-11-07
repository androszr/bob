"""A small dot map of the United Kingdom for the site card, drawn from a hand-traced coast (longitude, latitude).

Coarse on purpose: it says where in the UK a site is, at the size of a thumbnail. No map file, no network, no tiles.
"""

from __future__ import annotations

import math
from functools import lru_cache
from html import escape

_GREAT_BRITAIN = (
    (-5.7, 50.07), (-5.2, 49.96), (-4.1, 50.33), (-3.64, 50.22), (-3.4, 50.6), (-2.9, 50.7), (-2.45, 50.52),
    (-1.95, 50.65), (-1.3, 50.58), (-0.79, 50.72), (-0.14, 50.81), (0.25, 50.74), (0.97, 50.91), (1.37, 51.13),
    (1.45, 51.38), (0.7, 51.5), (1.15, 51.79), (1.35, 51.96), (1.58, 52.08), (1.76, 52.48), (1.74, 52.6),
    (1.3, 52.93), (0.49, 52.95), (0.2, 52.8), (0.34, 53.14), (0.12, 53.58), (-0.08, 54.12), (-0.6, 54.49),
    (-1.15, 54.63), (-1.37, 54.91), (-1.43, 55.02), (-2.0, 55.77), (-2.5, 56.0), (-3.2, 55.98), (-3.6, 56.03),
    (-2.59, 56.28), (-2.97, 56.46), (-2.58, 56.56), (-2.08, 57.14), (-1.78, 57.5), (-2.0, 57.69), (-2.96, 57.68),
    (-4.2, 57.49), (-3.78, 57.86), (-3.08, 58.44), (-3.03, 58.64), (-3.37, 58.67), (-5.0, 58.62), (-5.25, 58.15),
    (-5.4, 57.9), (-5.85, 57.45), (-5.83, 57.0), (-6.22, 56.73), (-5.47, 56.41), (-5.77, 55.31), (-4.8, 55.95),
    (-4.65, 55.46), (-5.1, 54.95), (-4.86, 54.63), (-3.6, 54.97), (-3.2, 54.95), (-3.63, 54.5), (-3.25, 54.1),
    (-2.85, 54.1), (-3.05, 53.82), (-3.05, 53.42), (-3.85, 53.33), (-4.68, 53.31), (-4.75, 52.8), (-4.05, 52.72),
    (-4.09, 52.41), (-4.7, 52.12), (-5.3, 51.88), (-4.7, 51.65), (-4.3, 51.57), (-3.9, 51.58), (-3.17, 51.45),
    (-2.7, 51.58), (-3.0, 51.33), (-3.45, 51.22), (-4.12, 51.21), (-4.53, 51.02), (-4.55, 50.83), (-5.0, 50.55),
    (-5.48, 50.22),
)
_NORTHERN_IRELAND = (
    (-7.3, 55.05), (-7.0, 55.15), (-6.65, 55.21), (-6.15, 55.22), (-5.75, 54.85), (-5.45, 54.45), (-5.55, 54.3),
    (-5.88, 54.2), (-6.3, 54.1), (-6.6, 54.05), (-7.0, 54.25), (-7.55, 54.12), (-8.15, 54.45), (-7.85, 54.55),
    (-7.5, 54.8),
)
_HEBRIDES = ((-7.55, 56.95), (-7.1, 56.95), (-6.9, 57.7), (-6.15, 58.2), (-6.2, 58.52), (-7.1, 58.2), (-7.55, 57.5))
_SKYE = ((-6.75, 57.45), (-6.1, 57.1), (-5.75, 57.25), (-6.2, 57.7), (-6.6, 57.65))
_MULL = ((-6.4, 56.3), (-5.7, 56.35), (-5.85, 56.6), (-6.3, 56.6))
_ORKNEY = ((-3.45, 58.85), (-2.65, 58.85), (-2.4, 59.3), (-3.3, 59.25))
_SHETLAND = ((-1.75, 59.85), (-1.1, 59.85), (-0.8, 60.8), (-1.7, 60.65))
_LAND = (_GREAT_BRITAIN, _NORTHERN_IRELAND, _HEBRIDES, _SKYE, _MULL, _ORKNEY, _SHETLAND)

_WEST, _EAST, _SOUTH, _NORTH = -9.4, 2.4, 49.7, 61.0     # the frame; the west margin leaves room for a city's name
_PER_DEGREE = 20
_SQUASH = math.cos(math.radians(54.5))                  # how much shorter a degree of longitude is than one of latitude
WIDTH = round((_EAST - _WEST) * _SQUASH * _PER_DEGREE)
HEIGHT = round((_NORTH - _SOUTH) * _PER_DEGREE)
STEP = 6
_LABEL_SIZE = 11


def _inside(lon: float, lat: float, ring: tuple) -> bool:
    hit = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1], strict=True):
        if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) / (y2 - y1) * (x2 - x1):
            hit = not hit
    return hit


def on_land(lon: float, lat: float) -> bool:
    return any(_inside(lon, lat, ring) for ring in _LAND)


def project(lat, lon) -> tuple[float, float] | None:
    """Where a coordinate lands on the map, or None when it is missing or outside the frame."""
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    if not (_SOUTH <= lat <= _NORTH and _WEST <= lon <= _EAST):
        return None
    return (lon - _WEST) * _SQUASH * _PER_DEGREE, (_NORTH - lat) * _PER_DEGREE


@lru_cache(maxsize=1)
def land_dots() -> tuple[tuple[int, int], ...]:
    """Every grid point of the map that falls on land."""
    return tuple((x, y) for y in range(3, HEIGHT, STEP) for x in range(3, WIDTH, STEP)
                 if on_land(_WEST + x / (_SQUASH * _PER_DEGREE), _NORTH - y / _PER_DEGREE))


def _label(city: str, cx: float, cy: float) -> str:
    """The city's name beside the marker, on whichever side has room."""
    east = cx < WIDTH * .35
    x, anchor = (cx + 10, "start") if east else (cx - 10, "end")
    baseline = cy + _LABEL_SIZE * .35
    common = f'x="{x:.0f}" y="{baseline:.0f}" text-anchor="{anchor}" font-size="{_LABEL_SIZE}" font-weight="700"'
    # a dark copy under the white one keeps the name readable over the dots
    return (f'<text {common} fill="none" stroke="#14382a" stroke-width="3">{escape(city)}</text>'
            f'<text {common} fill="#fff">{escape(city)}</text>')


def svg(lat, lon, city: str, cls: str) -> str:
    """The map with the site marked, or nothing at all when the site has no coordinates inside the UK: a map of the UK
    with no marker on it would say something untrue about where the site is."""
    place = project(lat, lon)
    if place is None:
        return ""
    cx, cy = place
    big = cls == "big"
    glow, core = (11, 5) if big else (26, 13)     # the thumbnail is drawn at about a quarter of the size
    dots = "".join(f"M{x} {y}h.01" for x, y in land_dots())
    marker = (f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{glow}" fill="#9BE8A8" opacity=".3"/>'
              f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{core}" fill="#9BE8A8" stroke="#fff" stroke-width="2"/>')
    name = f"Map of the UK with {escape(city, quote=True)} marked" if city else "Map of the UK with the site marked"
    return (f'<svg class="pl {cls}" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-label="{name}">'
            f'<path d="{dots}" stroke="currentColor" stroke-width="3.4" stroke-linecap="round" '
            'fill="none" opacity=".42"/>'
            f'{marker}{_label(city, cx, cy) if big and city else ""}</svg>')
