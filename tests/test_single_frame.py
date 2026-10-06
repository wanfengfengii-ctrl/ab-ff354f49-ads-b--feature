"""Endpoint behaviour for single-frame groups decoded against a reference."""
import pytest
from fastapi.testclient import TestClient

from app.adsb import build_position_message
from app.main import app

URL = "/api/adsb/positions/decode"
EVEN = "8D40621D58C382D690C8AC2863A7"
ODD = "8D40621D58C386435CC412692AD6"
BAD_CRC = EVEN[:-1] + "0"

client = TestClient(app)


def frame(raw, time_ms):
    return {"time_ms": time_ms, "raw": raw}


def reference(lat, lon, time_ms, radius=50):
    return {"lat": lat, "lon": lon, "time_ms": time_ms, "maxDistanceNm": radius}


def post_single(group_id, frame_obj, ref=None):
    group = {"id": group_id, "frames": [frame_obj]}
    if ref is not None:
        group["reference"] = ref
    return client.post(URL, json={"pairs": [group]})


def result_of(resp):
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


# --------------------------------------------------------------------------
# Successful local decoding.
# --------------------------------------------------------------------------

def test_single_even_frame_decodes_against_reference():
    resp = post_single("s1", frame(EVEN, 1_000_000), reference(52.25, 3.90, 999_000))
    r = result_of(resp)
    assert r["status"] == "ok"
    pos = r["position"]
    assert pos["lat"] == 52.257202
    assert pos["lon"] == 3.919373
    assert pos["frame"] == "even"
    assert pos["time_ms"] == 1_000_000
    assert pos["icao"] == "40621D"
    assert r["error"] is None


def test_single_odd_frame_decodes_against_reference():
    resp = post_single("s2", frame(ODD, 1_000_000), reference(52.27, 3.95, 995_000))
    r = result_of(resp)
    assert r["status"] == "ok"
    pos = r["position"]
    assert pos["lat"] == 52.265780
    assert pos["lon"] == 3.938913
    assert pos["frame"] == "odd"


def test_single_frame_synthetic_roundtrips():
    for lat, lon in [(52.25, 3.92), (-33.86, 151.2), (1.35, 103.82), (64.1, -21.9)]:
        for odd in (False, True):
            raw = build_position_message("ABCDEF", lat, lon, odd=odd)
            resp = post_single(
                f"rt-{lat}-{lon}-{odd}",
                frame(raw, 50_000),
                reference(lat - 0.001, lon - 0.001, 49_000, radius=10),
            )
            r = result_of(resp)
            assert r["status"] == "ok", r
            assert r["position"]["lat"] == pytest.approx(lat, abs=1e-3)
            assert r["position"]["lon"] == pytest.approx(lon, abs=1e-3)


# --------------------------------------------------------------------------
# Reference timing.
# --------------------------------------------------------------------------

def test_reference_same_timestamp_is_valid():
    resp = post_single("t0", frame(EVEN, 10_000), reference(52.25, 3.90, 10_000))
    assert result_of(resp)["status"] == "ok"


def test_reference_exactly_30s_old_is_valid():
    resp = post_single("t30", frame(EVEN, 40_000), reference(52.25, 3.90, 10_000))
    assert result_of(resp)["status"] == "ok"


def test_reference_30s_and_1ms_old_rejected():
    resp = post_single("t31", frame(EVEN, 40_001), reference(52.25, 3.90, 10_000))
    r = result_of(resp)
    assert r["status"] == "error"
    assert r["error"]["code"] == "REFERENCE_TOO_OLD"


def test_reference_in_future_rejected():
    resp = post_single("tf", frame(EVEN, 10_000), reference(52.25, 3.90, 10_001))
    r = result_of(resp)
    assert r["status"] == "error"
    assert r["error"]["code"] == "REFERENCE_IN_FUTURE"


# --------------------------------------------------------------------------
# Radius gate and CPR ambiguity.
# --------------------------------------------------------------------------

def test_position_outside_radius_rejected():
    # Credible fix seconds earlier, but the allowed radius is tighter than
    # the reference-to-fix distance.
    resp = post_single(
        "far", frame(EVEN, 10_000), reference(51.0, 3.0, 9_000, radius=10)
    )
    r = result_of(resp)
    assert r["status"] == "error"
    assert r["error"]["code"] == "POSITION_OUT_OF_RANGE"
    assert r["position"] is None


def test_radius_boundary_inclusive():
    # Reference ~0.014 degrees of longitude (~0.55 NM at 52 deg latitude)
    # away: a 1 NM radius still admits the decoded fix.
    resp = post_single(
        "r-edge", frame(EVEN, 10_000), reference(52.2572, 3.905, 9_000, radius=1)
    )
    assert result_of(resp)["status"] == "ok"


def test_cpr_grid_ambiguity_cannot_throw_target_far_away():
    # Frame genuinely broadcast over western Europe; an untrustworthy
    # reference placed on another continent must not relocate the target --
    # local reconstruction lands far from that reference and the radius
    # gate rejects it instead of emitting a distant-but-confident fix.
    resp = post_single(
        "ambig", frame(EVEN, 10_000), reference(0.0, 0.0, 9_000, radius=50)
    )
    r = result_of(resp)
    assert r["status"] == "error"
    assert r["error"]["code"] == "POSITION_OUT_OF_RANGE"


# --------------------------------------------------------------------------
# Missing / malformed references.
# --------------------------------------------------------------------------

def test_missing_reference_is_group_error_not_422():
    resp = client.post(URL, json={"pairs": [{"id": "m", "frames": [frame(EVEN, 0)]}]})
    r = result_of(resp)
    assert r["id"] == "m"
    assert r["error"]["code"] == "MISSING_REFERENCE"


def test_missing_reference_does_not_shadow_other_groups():
    resp = client.post(URL, json={"pairs": [
        {"id": "no-ref", "frames": [frame(EVEN, 0)]},
        {
            "id": "single-ok",
            "frames": [frame(EVEN, 10_000)],
            "reference": reference(52.25, 3.90, 9_000),
        },
        {
            "id": "pair-ok",
            "frames": [frame(ODD, 1_000), frame(EVEN, 6_000)],
        },
    ]})
    assert resp.status_code == 200
    results = {r["id"]: r for r in resp.json()["results"]}
    assert results["no-ref"]["error"]["code"] == "MISSING_REFERENCE"
    assert results["single-ok"]["status"] == "ok"
    assert results["pair-ok"]["status"] == "ok"


@pytest.mark.parametrize("patch", [
    {"lat": 90.001},
    {"lat": -90.01},
    {"lon": 180.001},
    {"lon": -180.01},
    {"maxDistanceNm": 0},
    {"maxDistanceNm": 500.01},
    {"maxDistanceNm": 1000},
])
def test_invalid_reference_fields_rejected_with_422(patch):
    ref = reference(52.25, 3.90, 0)
    ref.update(patch)
    resp = post_single("bad-ref", frame(EVEN, 10_000), ref)
    assert resp.status_code == 422


def test_non_finite_reference_rejected():
    ref = reference(52.25, 3.90, 0)
    ref["lat"] = "NaN"
    resp = post_single("nan", frame(EVEN, 10_000), ref)
    assert resp.status_code == 422


def test_reference_on_two_frame_group_rejected():
    resp = client.post(URL, json={"pairs": [{
        "id": "p",
        "frames": [frame(EVEN, 0), frame(ODD, 5_000)],
        "reference": reference(52.25, 3.90, 0),
    }]})
    assert resp.status_code == 422


def test_extra_reference_field_rejected():
    ref = reference(52.25, 3.90, 0)
    ref["source"] = "radar"
    resp = post_single("extra", frame(EVEN, 10_000), ref)
    assert resp.status_code == 422


# --------------------------------------------------------------------------
# Frame validation still applies to single frames.
# --------------------------------------------------------------------------

def test_single_frame_bad_crc():
    r = result_of(post_single("crc", frame(BAD_CRC, 10_000), reference(52.25, 3.90, 9_000)))
    assert r["error"]["code"] == "CRC_MISMATCH"


def test_single_frame_not_df17():
    from app.adsb import build_message
    df4 = build_message(df=4, icao=0x40621D, me=0x58C382D690C8AC)
    r = result_of(post_single("df4", frame(df4, 10_000), reference(52.25, 3.90, 9_000)))
    assert r["error"]["code"] == "NOT_DF17"


def test_single_frame_not_airborne_position():
    velocity = build_position_message("40621D", 52.0, 4.0, odd=True, type_code=19)
    r = result_of(post_single("vel", frame(velocity, 10_000), reference(52.0, 4.0, 9_000)))
    assert r["error"]["code"] == "NOT_AIRBORNE_POSITION"


# --------------------------------------------------------------------------
# Antimeridian and polar legality.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("odd", [False, True])
def test_antimeridian_single_frame_with_reference_across_line(odd):
    # Real fix at +179.98; the trusted reference is on the -179 side a few
    # NM away across the antimeridian -- shortest-path distance, legal.
    raw = build_position_message("ABCDEF", 10.0, 179.98, odd=odd)
    resp = post_single(
        "am-east", frame(raw, 10_000), reference(10.0, -179.95, 9_000, radius=10)
    )
    r = result_of(resp)
    assert r["status"] == "ok", r
    assert r["position"]["lon"] == pytest.approx(179.98, abs=1e-3)

    raw = build_position_message("ABCDEF", 10.0, -179.98, odd=odd)
    resp = post_single(
        "am-west", frame(raw, 10_000), reference(10.0, 179.95, 9_000, radius=10)
    )
    r = result_of(resp)
    assert r["status"] == "ok", r
    assert r["position"]["lon"] == pytest.approx(-179.98, abs=1e-3)


@pytest.mark.parametrize("lat,lon", [
    (83.0, -130.0),
    (-83.0, 120.0),
    (89.9, 45.0),
    (-89.9, -45.0),
])
@pytest.mark.parametrize("odd", [False, True])
def test_polar_single_frame_stays_near_reference(lat, lon, odd):
    raw = build_position_message("ABCDEF", lat, lon, odd=odd)
    resp = post_single(
        f"polar-{lat}-{odd}",
        frame(raw, 10_000),
        reference(lat + 0.002, lon + 0.002, 9_000, radius=5),
    )
    r = result_of(resp)
    assert r["status"] == "ok", r
    assert r["position"]["lat"] == pytest.approx(lat, abs=1e-3)
    assert r["position"]["lon"] == pytest.approx(lon, abs=1e-3)
