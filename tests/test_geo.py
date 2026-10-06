"""Great-circle helpers: antimeridian wrapping and haversine distances."""
import math

import pytest

from app.geo import great_circle_distance_nm, wrap_longitude_delta


def test_wrap_longitude_delta():
    assert wrap_longitude_delta(0.0) == 0.0
    assert wrap_longitude_delta(179.0) == 179.0
    assert wrap_longitude_delta(-179.0) == -179.0
    assert wrap_longitude_delta(180.0) == -180.0
    assert wrap_longitude_delta(181.0) == pytest.approx(-179.0)
    assert wrap_longitude_delta(359.5) == pytest.approx(-0.5)
    assert wrap_longitude_delta(-359.5) == pytest.approx(0.5)


def test_known_distances():
    # One degree of longitude at the equator is 60 NM by definition.
    d = great_circle_distance_nm(0.0, 0.0, 0.0, 1.0)
    assert 59.95 < d < 60.05
    # Antimeridian straddle: 0.04 degree gap at the equator ~= 2.4 NM via
    # the shortest path, never ~21,599 NM the long way round.
    d = great_circle_distance_nm(0.0, 179.98, 0.0, -179.98)
    assert 2.0 < d < 3.0
    # Antipodal points: about half an Earth circumference (~10,800 NM).
    d = great_circle_distance_nm(0.0, 0.0, 0.0, 180.0)
    assert 10_700.0 < d < 10_900.0
    # One degree along a meridian is ~60 NM at any latitude.
    d = great_circle_distance_nm(60.0, 10.0, 61.0, 10.0)
    assert 59.9 < d < 60.1


def test_antimeridian_wrapping_matches_direct_spherical_path():
    # Equivalent points expressed on each side of the line give one distance.
    across = great_circle_distance_nm(45.0, 179.99, 45.0, -179.99)
    adjacent = great_circle_distance_nm(45.0, 179.99, 45.0, 179.97)
    assert across == pytest.approx(adjacent, rel=1e-9)
    assert across < 2.0


def test_zero_distance():
    assert great_circle_distance_nm(-33.86, 151.21, -33.86, 151.21) == pytest.approx(0.0, abs=1e-9)
