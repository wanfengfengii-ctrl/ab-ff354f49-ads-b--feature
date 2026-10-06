"""Global CPR decoding: known vectors, round-trips, and boundary behaviour."""
import pytest

from app.adsb import build_position_message, parse_position_frame
from app.cpr import cpr_nl, global_decode, local_decode
from app.errors import DecodeError

EVEN = "8D40621D58C382D690C8AC2863A7"
ODD = "8D40621D58C386435CC412692AD6"


def decode_positions(lat0, lon0, lat1, lon1, t_even=0, t_odd=5_000, icao="ABCDEF"):
    """Build a synthetic even/odd pair for two nearby fixes and decode it."""
    even = parse_position_frame(build_position_message(icao, lat0, lon0, odd=False), t_even)
    odd = parse_position_frame(build_position_message(icao, lat1, lon1, odd=True), t_odd)
    return global_decode(even, odd)


def test_known_pair_even_newer():
    even = parse_position_frame(EVEN, 6_000)
    odd = parse_position_frame(ODD, 1_000)
    lat, lon = global_decode(even, odd)
    assert (round(lat, 6), round(lon, 6)) == (52.257202, 3.919373)


def test_known_pair_odd_newer():
    even = parse_position_frame(EVEN, 1_000)
    odd = parse_position_frame(ODD, 6_000)
    lat, lon = global_decode(even, odd)
    assert (round(lat, 6), round(lon, 6)) == (52.265780, 3.938913)


# Positions are decoded from the newer (odd) frame; the aircraft moves
# slightly between the two fixes, as a real target would.
ROUNDTRIP_CASES = [
    (52.2572, 3.9194),        # western Europe
    (-33.8688, 151.2093),     # Sydney, southern hemisphere
    (1.3521, 103.8198),       # Singapore, near the equator
    (0.0, 0.0),               # origin
    (64.1466, -21.9426),      # Reykjavik
    (-54.8, -68.3),           # Ushuaia, far south
    (71.0, 25.0),             # high northern latitude
    (83.0, -130.0),           # NL down to single digits
    (36.80, 10.0),            # just below the NL boundary at 36.85025108
    (36.86, 10.0),            # just above it (NL 47 zone)
    (10.0, 179.95),           # just west of the antimeridian
    (10.0, -179.95),          # just east of the antimeridian
    (10.0, -0.05),            # just west of the prime meridian
]


@pytest.mark.parametrize("lat,lon", ROUNDTRIP_CASES)
def test_roundtrip_lands_on_newer_frame(lat, lon):
    moved_lat = lat + 0.0005
    moved_lon = lon + 0.0005
    dec_lat, dec_lon = decode_positions(lat, lon, moved_lat, moved_lon)
    # CPR quantises to one 2^17-th of a zone; longitude zones widen with
    # latitude, so the tolerance follows the newer (odd) frame's zone width.
    lon_step = 360.0 / max(cpr_nl(lat) - 1, 1) / 2**17
    assert dec_lat == pytest.approx(moved_lat, abs=1e-4)
    assert dec_lon == pytest.approx(moved_lon, abs=lon_step)
    assert -180.0 <= dec_lon < 180.0


def test_antimeridian_crossing_stays_on_correct_side():
    # Aircraft crosses 180° between the even and odd frames: the decoded
    # longitude must wrap to -179.98, not jump to +180.02 or mirror east.
    lat, lon = decode_positions(10.0, 179.98, 10.0, -179.98)
    assert lat == pytest.approx(10.0, abs=1e-4)
    assert lon == pytest.approx(-179.98, abs=1e-4)


def test_antimeridian_crossing_westbound():
    lat, lon = decode_positions(-20.0, -179.98, -20.0, 179.98)
    assert lon == pytest.approx(179.98, abs=1e-4)


def test_longitude_normalisation_range():
    for lon0 in (-179.99, -90.0, 0.0, 90.0, 179.99):
        _, lon = decode_positions(45.0, lon0, 45.0, lon0 + 0.0005)
        assert -180.0 <= lon < 180.0


def test_latitude_zone_mismatch_rejected():
    # Frames straddling the NL boundary at 36.85025108° (NL 48 below, 47
    # above) cannot be combined into a single global position.
    even = parse_position_frame(build_position_message("ABCDEF", 36.84, 10.0, odd=False), 0)
    odd = parse_position_frame(build_position_message("ABCDEF", 36.86, 10.0, odd=True), 5_000)
    with pytest.raises(DecodeError) as exc:
        global_decode(even, odd)
    assert exc.value.code == "LATITUDE_ZONE_MISMATCH"


def test_same_zone_near_boundary_decodes_fine():
    # Both frames inside the NL=48 band, hugging the boundary: stable decode.
    lat, lon = decode_positions(36.80, 10.0, 36.84, 10.0005)
    assert lat == pytest.approx(36.84, abs=1e-4)
    assert lon == pytest.approx(10.0005, abs=1e-4)


def test_cpr_nl_symmetry_and_extremes():
    assert cpr_nl(0.0) == 59
    assert cpr_nl(52.2572) == 36
    assert cpr_nl(36.84) == 48
    assert cpr_nl(36.86) == 47
    assert cpr_nl(-52.2572) == cpr_nl(52.2572)
    assert cpr_nl(89.9) == 1


# --------------------------------------------------------------------------
# Local (single-frame) CPR decoding against a trusted reference.
# --------------------------------------------------------------------------

def local_roundtrip(lat, lon, odd, ref_lat=None, ref_lon=None):
    raw = build_position_message("ABCDEF", lat, lon, odd=odd)
    frame = parse_position_frame(raw, 1_000)
    if ref_lat is None:
        ref_lat, ref_lon = lat - 0.001, lon - 0.001
    return local_decode(frame, ref_lat, ref_lon)


def test_local_known_even_frame():
    frame = parse_position_frame(EVEN, 6_000)
    lat, lon = local_decode(frame, 52.25, 3.90)
    assert (round(lat, 6), round(lon, 6)) == (52.257202, 3.919373)


def test_local_known_odd_frame():
    frame = parse_position_frame(ODD, 6_000)
    lat, lon = local_decode(frame, 52.27, 3.95)
    assert (round(lat, 6), round(lon, 6)) == (52.265780, 3.938913)


LOCAL_CASES = [
    (52.2572, 3.9194, False),
    (52.2572, 3.9194, True),
    (-33.8688, 151.2093, False),
    (-33.8688, 151.2093, True),
    (1.3521, 103.8198, False),
    (0.0, 0.0, False),
    (64.1466, -21.9426, True),
    (-54.8, -68.3, False),
    (71.0, 25.0, True),
    (83.0, -130.0, False),
    (83.0, -130.0, True),
    (-83.0, 120.0, False),
    (-83.0, 120.0, True),
    (89.9, 10.0, False),  # polar cap, few longitude zones
    (89.9, 10.0, True),
    (-89.9, -10.0, False),
    (-89.9, -10.0, True),
]


@pytest.mark.parametrize("lat,lon,odd", LOCAL_CASES)
def test_local_roundtrip(lat, lon, odd):
    dec_lat, dec_lon = local_roundtrip(lat, lon, odd)
    lon_step = 360.0 / max(cpr_nl(lat) - (1 if odd else 0), 1) / 2**17
    assert dec_lat == pytest.approx(lat, abs=1e-4)
    assert dec_lon == pytest.approx(lon, abs=max(lon_step, 1e-4))
    assert -180.0 <= dec_lon < 180.0


@pytest.mark.parametrize("odd", [False, True])
def test_local_antimeridian_east_and_west(odd):
    # The encoded fix sits on one side; references on either side must each
    # resolve the grid to that same 179.98 fix, wrapped to [-180, 180).
    raw = build_position_message("ABCDEF", 10.0, 179.98, odd=odd)
    frame = parse_position_frame(raw, 1_000)
    for ref_lon in (179.95, -179.95):
        lat, lon = local_decode(frame, 10.001, ref_lon)
        assert lat == pytest.approx(10.0, abs=1e-4)
        assert lon == pytest.approx(179.98, abs=1e-4)

    raw = build_position_message("ABCDEF", 10.0, -179.98, odd=odd)
    frame = parse_position_frame(raw, 1_000)
    for ref_lon in (179.95, -179.95):
        lat, lon = local_decode(frame, 10.001, ref_lon)
        assert lon == pytest.approx(-179.98, abs=1e-4)


@pytest.mark.parametrize("odd", [False, True])
def test_local_decode_stays_within_half_zone_of_reference(odd):
    # Aviation CPR local decoding resolves the ambiguity by reconstructing
    # inside the reference's own grid cell, snapping at most half a zone:
    # the fix can never be thrown a whole world away.  At mid-latitudes
    # half a longitude zone is 5 degrees (even) or ~5.08 (odd).
    raw = build_position_message("ABCDEF", 10.0, 179.98, odd=odd)
    frame = parse_position_frame(raw, 1_000)
    lat, lon = local_decode(frame, 10.0, 8.0)
    dlon = 360.0 / max(cpr_nl(lat) - (1 if odd else 0), 1)
    assert abs((lon - 8.0 + 180.0) % 360.0 - 180.0) <= dlon / 2.0 + 1e-9
