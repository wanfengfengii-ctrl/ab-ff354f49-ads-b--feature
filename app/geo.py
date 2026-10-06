"""Great-circle helpers used to sanity-check locally decoded positions.

Distances follow the shortest surface path, so a fix and a reference on
opposite sides of the antimeridian (e.g. 179.98° and -179.98°) stay a few
nautical miles apart instead of 21 600.
"""
from __future__ import annotations

import math

#: Mean Earth radius in nautical miles (WGS-84 mean radius / 1852 m).
EARTH_RADIUS_NM = 3440.065


def wrap_longitude_delta(delta: float) -> float:
    """Shortest signed longitude difference, normalised to [-180, 180)."""
    return (delta + 180.0) % 360.0 - 180.0


def great_circle_distance_nm(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Haversine distance between two decimal-degree points in nautical miles.

    The longitude gap is wrapped before the trigonometry so the result is the
    shortest path across the antimeridian.
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(wrap_longitude_delta(lon2 - lon1))
    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    # Clamp away floating-point overshoot at the antipodes before asin().
    return 2.0 * EARTH_RADIUS_NM * math.asin(min(1.0, math.sqrt(a)))
