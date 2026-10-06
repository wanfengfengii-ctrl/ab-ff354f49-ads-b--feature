"""Global/local CPR decoding: known vectors, round-trips, boundaries."""
import pytest

from app.adsb import build_position_message, parse_position_frame
from app.cpr import cpr_nl, global_decode, great_circle_distance_nm, local_decode
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


# ---------------------------------------------------------------------------
# Local (single-frame) CPR decoding against a trusted reference.
# ---------------------------------------------------------------------------

LOCAL_CASES = [
    (52.2572, 3.9194, False),       # western Europe, even
    (52.2658, 3.9389, True),        # western Europe, odd
    (-33.8688, 151.2093, False),    # Sydney, southern hemisphere
    (1.3521, 103.8198, True),       # Singapore, near the equator
    (-54.8, -68.3, False),          # Ushuaia
    (71.0, 25.0, True),             # high northern latitude
    (83.0, -130.0, False),          # NL down to single digits
    (-75.0, 45.0, True),            # Antarctica coast
    (10.0, 179.97, False),          # just west of the antimeridian
    (10.0, -179.97, True),          # just east of the antimeridian
]


@pytest.mark.parametrize("lat,lon,odd", LOCAL_CASES)
def test_local_decode_lands_in_reference_cell(lat, lon, odd):
    frame = parse_position_frame(build_position_message("ABCDEF", lat, lon, odd=odd), 6_000)
    # Reference a short distance (~1 NM) away: the same parity cell wins.
    ref_lat, ref_lon = lat + 0.015, lon + 0.015
    dec_lat, dec_lon = local_decode(frame, ref_lat, ref_lon)
    lat_step = 360.0 / (59 if odd else 60) / 2**17
    lon_step = 360.0 / max(cpr_nl(lat) - (1 if odd else 0), 1) / 2**17
    assert dec_lat == pytest.approx(lat, abs=max(lat_step, 1e-6))
    assert dec_lon == pytest.approx(lon, abs=max(lon_step, 1e-6))
    assert -180.0 <= dec_lon < 180.0


def test_local_decode_known_even_frame():
    frame = parse_position_frame(EVEN, 6_000)
    lat, lon = local_decode(frame, 52.26, 3.92)
    assert (round(lat, 6), round(lon, 6)) == (52.257202, 3.919373)


def test_local_decode_known_odd_frame():
    frame = parse_position_frame(ODD, 6_000)
    lat, lon = local_decode(frame, 52.27, 3.94)
    assert (round(lat, 6), round(lon, 6)) == (52.265780, 3.938913)


def test_local_decode_antimeridian_wraps_to_nearest_cell():
    # Target cell sits at -179.98 while the trusted reference is on the
    # +179.99 side: local decode must wrap, not pick a cell ~360 degrees away.
    frame = parse_position_frame(
        build_position_message("ABCDEF", 10.0, -179.98, odd=False), 6_000
    )
    lat, lon = local_decode(frame, 10.0, 179.99)
    assert lat == pytest.approx(10.0, abs=1e-4)
    assert lon == pytest.approx(-179.98, abs=1e-4)
    assert great_circle_distance_nm(10.0, 179.99, lat, lon) < 5.0


def test_local_decode_antimeridian_east_to_west():
    frame = parse_position_frame(
        build_position_message("ABCDEF", -20.0, 179.98, odd=True), 6_000
    )
    lat, lon = local_decode(frame, -20.0, -179.99)
    assert lon == pytest.approx(179.98, abs=1e-4)
    assert great_circle_distance_nm(-20.0, -179.99, lat, lon) < 5.0


def test_local_decode_polar_even_frame():
    # Even frames keep longitude zones to the pole; a legal high-latitude
    # single frame must land in the reference's cell.
    frame = parse_position_frame(
        build_position_message("ABCDEF", 85.0, 100.0, odd=False), 6_000
    )
    lat, lon = local_decode(frame, 85.01, 100.02)
    assert lat == pytest.approx(85.0, abs=1e-3)
    assert lon == pytest.approx(100.0, abs=1e-2)


def test_local_decode_odd_frame_undefined_at_pole():
    # An odd frame decoded to |lat| >= 87 (NL collapses to 1) has no
    # longitude zone structure and must be refused, not silently guessed.
    frame = parse_position_frame(
        build_position_message("ABCDEF", 88.0, 100.0, odd=True), 6_000
    )
    with pytest.raises(DecodeError) as exc:
        local_decode(frame, 88.0, 100.0)
    assert exc.value.code == "POLAR_CPR_AMBIGUITY"


def test_local_decode_distant_reference_picks_ambiguous_cell():
    # The same CPR encoding repeats every few degrees: a reference on the
    # far side of the ambiguity resolves to a *different* cell.  This is the
    # case the caller's radius gate exists to reject.
    frame = parse_position_frame(
        build_position_message("ABCDEF", 52.2572, 3.9194, odd=False), 6_000
    )
    lat, lon = local_decode(frame, -33.8688, 151.2093)
    assert great_circle_distance_nm(-33.8688, 151.2093, lat, lon) > 50.0


@pytest.mark.parametrize(
    "lat1,lon1,lat2,lon2,expected",
    [
        (0.0, 0.0, 0.0, 0.0, 0.0),
        (0.0, 0.0, 0.0, 1.0, 60.0),          # one degree longitude at equator ~ 60 NM
        (10.0, 179.99, 10.0, -179.99, 1.2),  # short way across the antimeridian
        (10.0, -179.99, 10.0, 179.99, 1.2),
        (90.0, 0.0, 89.0, 0.0, 60.0),        # one degree latitude ~ 60 NM near pole
    ],
)
def test_great_circle_distance_wraps_antimeridian(lat1, lon1, lat2, lon2, expected):
    distance = great_circle_distance_nm(lat1, lon1, lat2, lon2)
    assert distance == pytest.approx(expected, abs=0.1)
