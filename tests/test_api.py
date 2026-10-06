"""Endpoint behaviour: per-pair verdicts, stable error codes, batch limits."""
import pytest
from fastapi.testclient import TestClient

from app.adsb import build_position_message
from app.main import app

URL = "/api/adsb/positions/decode"
EVEN = "8D40621D58C382D690C8AC2863A7"
ODD = "8D40621D58C386435CC412692AD6"
BAD_CRC = EVEN[:-1] + "0"  # EVEN ends in "7"; flipping it breaks parity

client = TestClient(app)


def frame(raw, time_ms):
    return {"time_ms": time_ms, "raw": raw}


def post(pairs):
    return client.post(URL, json={"pairs": pairs})


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_valid_pair_decodes_newer_frame():
    resp = post([{"id": "p1", "frames": [frame(ODD, 1_000), frame(EVEN, 6_000)]}])
    assert resp.status_code == 200
    (result,) = resp.json()["results"]
    assert result["id"] == "p1"
    assert result["status"] == "ok"
    pos = result["position"]
    assert pos["lat"] == 52.257202
    assert pos["lon"] == 3.919373
    assert pos["frame"] == "even"
    assert pos["time_ms"] == 6_000
    assert pos["icao"] == "40621D"
    assert result["error"] is None


def test_bad_crc_reports_stable_code_and_id():
    resp = post([{"id": "broken-1", "frames": [frame(BAD_CRC, 0), frame(ODD, 5_000)]}])
    assert resp.status_code == 200
    (result,) = resp.json()["results"]
    assert result["id"] == "broken-1"
    assert result["status"] == "error"
    assert result["error"]["code"] == "CRC_MISMATCH"
    assert result["position"] is None


def test_bad_pair_does_not_shadow_good_pair():
    pairs = [
        {"id": "bad-crc", "frames": [frame(BAD_CRC, 0), frame(ODD, 5_000)]},
        {"id": "good", "frames": [frame(EVEN, 6_000), frame(ODD, 1_000)]},
        {"id": "bad-gap", "frames": [frame(EVEN, 0), frame(ODD, 30_000)]},
    ]
    resp = post(pairs)
    assert resp.status_code == 200
    results = {r["id"]: r for r in resp.json()["results"]}
    assert results["bad-crc"]["error"]["code"] == "CRC_MISMATCH"
    assert results["bad-gap"]["error"]["code"] == "TIME_GAP_EXCEEDED"
    assert results["good"]["status"] == "ok"
    assert results["good"]["position"]["lat"] == 52.257202


def test_icao_mismatch():
    other = build_position_message("ABCDEF", 52.26, 3.92, odd=True)
    resp = post([{"id": "x", "frames": [frame(EVEN, 0), frame(other, 5_000)]}])
    (result,) = resp.json()["results"]
    assert result["error"]["code"] == "ICAO_MISMATCH"


def test_same_cpr_flag_rejected():
    resp = post([{"id": "x", "frames": [frame(EVEN, 0), frame(EVEN, 5_000)]}])
    (result,) = resp.json()["results"]
    assert result["error"]["code"] == "SAME_CPR_FLAG"


def test_time_gap_boundary():
    # Exactly 10 s apart is still acceptable...
    resp = post([{"id": "edge", "frames": [frame(EVEN, 0), frame(ODD, 10_000)]}])
    (result,) = resp.json()["results"]
    assert result["status"] == "ok"
    # ...10 s + 1 ms is not.
    resp = post([{"id": "over", "frames": [frame(EVEN, 0), frame(ODD, 10_001)]}])
    (result,) = resp.json()["results"]
    assert result["error"]["code"] == "TIME_GAP_EXCEEDED"


def test_not_df17_and_not_position_codes():
    from app.adsb import build_message
    df4 = build_message(df=4, icao=0x40621D, me=0x58C382D690C8AC)
    velocity = build_position_message("40621D", 52.0, 4.0, odd=True, type_code=19)
    resp = post([
        {"id": "df4", "frames": [frame(df4, 0), frame(ODD, 5_000)]},
        {"id": "vel", "frames": [frame(EVEN, 0), frame(velocity, 5_000)]},
    ])
    results = {r["id"]: r for r in resp.json()["results"]}
    assert results["df4"]["error"]["code"] == "NOT_DF17"
    assert results["vel"]["error"]["code"] == "NOT_AIRBORNE_POSITION"


def test_latitude_zone_mismatch_surfaces_as_pair_error():
    even = build_position_message("40621D", 36.84, 10.0, odd=False)
    odd = build_position_message("40621D", 36.86, 10.0, odd=True)
    resp = post([{"id": "zones", "frames": [frame(even, 0), frame(odd, 5_000)]}])
    (result,) = resp.json()["results"]
    assert result["error"]["code"] == "LATITUDE_ZONE_MISMATCH"


def test_batch_size_limits():
    assert post([]).status_code == 422
    pairs = [{"id": f"p{i}", "frames": [frame(EVEN, 0), frame(ODD, 5_000)]} for i in range(201)]
    assert post(pairs).status_code == 422
    pairs = pairs[:200]
    assert post(pairs).status_code == 200


def test_duplicate_ids_rejected():
    pair = {"id": "dup", "frames": [frame(EVEN, 0), frame(ODD, 5_000)]}
    assert post([pair, pair]).status_code == 422


def test_wrong_frame_count_rejected():
    # Zero or three frames remain request-level errors...
    assert post([{"id": "x", "frames": []}]).status_code == 422
    three = [frame(EVEN, 0), frame(ODD, 5_000), frame(EVEN, 6_000)]
    assert post([{"id": "x", "frames": three}]).status_code == 422
    # ...one frame is valid as a shape; without a reference it gets the
    # per-group MISSING_REFERENCE code instead of a 422.
    resp = post([{"id": "x", "frames": [frame(EVEN, 0)]}])
    assert resp.status_code == 200
    assert resp.json()["results"][0]["error"]["code"] == "MISSING_REFERENCE"


def test_numeric_ids_accepted_and_echoed():
    resp = post([{"id": 7, "frames": [frame(EVEN, 6_000), frame(ODD, 1_000)]}])
    assert resp.status_code == 200
    assert resp.json()["results"][0]["id"] == "7"


# ---------------------------------------------------------------------------
# Single-frame groups: local CPR against a trusted reference.
# ---------------------------------------------------------------------------

def reference(lat, lon, time_ms, max_distance_nm=50.0):
    return {"lat": lat, "lon": lon, "time_ms": time_ms, "maxDistanceNm": max_distance_nm}


def single(raw, time_ms, ref):
    return {"id": "single", "frames": [frame(raw, time_ms)], "reference": ref}


def test_single_frame_decodes_against_reference():
    resp = post([single(EVEN, 6_000, reference(52.26, 3.92, 3_000, max_distance_nm=25.0))])
    assert resp.status_code == 200
    (result,) = resp.json()["results"]
    assert result["status"] == "ok"
    pos = result["position"]
    assert pos["lat"] == 52.257202
    assert pos["lon"] == 3.919373
    assert pos["time_ms"] == 6_000
    assert pos["icao"] == "40621D"
    assert pos["frame"] == "even"
    assert result["error"] is None


def test_single_odd_frame_decodes_against_reference():
    odd = build_position_message("ABCDEF", -33.86, 151.21, odd=True)
    resp = post([single(odd, 20_000, reference(-33.85, 151.20, 10_000, max_distance_nm=25.0))])
    (result,) = resp.json()["results"]
    assert result["status"] == "ok"
    pos = result["position"]
    assert abs(pos["lat"] - (-33.86)) < 1e-4
    assert abs(pos["lon"] - 151.21) < 1e-4
    assert pos["frame"] == "odd"
    assert pos["time_ms"] == 20_000


def test_single_frame_without_reference_is_stable_group_error():
    # Missing reference is reported per group with a stable code (HTTP 200),
    # not as a request-level 422, so it cannot invalidate the rest of the
    # batch.
    resp = post([
        {"id": "no-ref", "frames": [frame(EVEN, 6_000)]},
        {"id": "good", "frames": [frame(ODD, 1_000), frame(EVEN, 6_000)]},
    ])
    assert resp.status_code == 200
    results = {r["id"]: r for r in resp.json()["results"]}
    assert results["no-ref"]["status"] == "error"
    assert results["no-ref"]["error"]["code"] == "MISSING_REFERENCE"
    assert results["no-ref"]["position"] is None
    assert results["good"]["status"] == "ok"


def test_single_frame_reference_in_future():
    resp = post([single(EVEN, 6_000, reference(52.26, 3.92, 6_001, max_distance_nm=25.0))])
    (result,) = resp.json()["results"]
    assert result["status"] == "error"
    assert result["error"]["code"] == "REFERENCE_IN_FUTURE"


def test_single_frame_reference_age_boundary():
    # Exactly 30 s older is acceptable...
    resp = post([single(EVEN, 30_000, reference(52.26, 3.92, 0, max_distance_nm=25.0))])
    (result,) = resp.json()["results"]
    assert result["status"] == "ok"
    # ...30 s + 1 ms is not.
    resp = post([single(EVEN, 30_001, reference(52.26, 3.92, 0, max_distance_nm=25.0))])
    (result,) = resp.json()["results"]
    assert result["error"]["code"] == "REFERENCE_TOO_OLD"


def test_single_frame_position_outside_radius():
    # The CPR encoding is shared by cells world-wide: against a reference
    # near (0, 0) this frame resolves to a cell ~178 NM away, which the
    # 100 NM acceptance radius must reject.
    resp = post([single(EVEN, 6_000, reference(0.0, 0.0, 3_000, max_distance_nm=100.0))])
    (result,) = resp.json()["results"]
    assert result["status"] == "error"
    assert result["error"]["code"] == "POSITION_OUT_OF_RANGE"
    assert result["position"] is None


def test_single_frame_radius_boundary():
    # Reference ~15.7 NM from the decoded cell: 16 NM passes, 10 NM fails.
    resp = post([single(EVEN, 6_000, reference(52.0, 4.0, 3_000, max_distance_nm=16.0))])
    assert resp.json()["results"][0]["status"] == "ok"
    resp = post([single(EVEN, 6_000, reference(52.0, 4.0, 3_000, max_distance_nm=10.0))])
    assert resp.json()["results"][0]["error"]["code"] == "POSITION_OUT_OF_RANGE"


def test_single_frame_bad_crc_still_rejected():
    resp = post([single(BAD_CRC, 6_000, reference(52.26, 3.92, 3_000))])
    (result,) = resp.json()["results"]
    assert result["error"]["code"] == "CRC_MISMATCH"


def test_single_frame_df_and_type_code_still_validated():
    from app.adsb import build_message
    df4 = build_message(df=4, icao=0x40621D, me=0x58C382D690C8AC)
    velocity = build_position_message("40621D", 52.0, 4.0, odd=True, type_code=19)
    resp = post([
        {"id": "df4", "frames": [frame(df4, 6_000)], "reference": reference(52.26, 3.92, 3_000)},
        {"id": "vel", "frames": [frame(velocity, 6_000)], "reference": reference(52.0, 4.0, 3_000)},
    ])
    results = {r["id"]: r for r in resp.json()["results"]}
    assert results["df4"]["error"]["code"] == "NOT_DF17"
    assert results["vel"]["error"]["code"] == "NOT_AIRBORNE_POSITION"


def test_single_frame_failures_do_not_shadow_batch():
    out_of_range = single(EVEN, 6_000, reference(0.0, 0.0, 3_000, max_distance_nm=50.0))
    out_of_range["id"] = "single-bad"
    good_single = single(EVEN, 6_000, reference(52.26, 3.92, 3_000, max_distance_nm=25.0))
    good_single["id"] = "single-good"
    good_pair = {"id": "pair-good", "frames": [frame(ODD, 1_000), frame(EVEN, 6_000)]}
    resp = post([out_of_range, good_single, good_pair])
    results = {r["id"]: r for r in resp.json()["results"]}
    assert results["single-bad"]["error"]["code"] == "POSITION_OUT_OF_RANGE"
    assert results["single-good"]["status"] == "ok"
    assert results["pair-good"]["status"] == "ok"


def test_single_frame_antimeridian_crossing():
    raw = build_position_message("ABCDEF", 10.0, -179.98, odd=False)
    resp = post([single(raw, 6_000, reference(10.0, 179.99, 3_000, max_distance_nm=10.0))])
    (result,) = resp.json()["results"]
    assert result["status"] == "ok"
    assert abs(result["position"]["lon"] - (-179.98)) < 1e-4


def test_single_frame_polar_latitude():
    raw = build_position_message("ABCDEF", 85.0, 100.0, odd=False)
    resp = post([single(raw, 6_000, reference(85.01, 100.02, 3_000, max_distance_nm=10.0))])
    (result,) = resp.json()["results"]
    assert result["status"] == "ok"
    assert abs(result["position"]["lat"] - 85.0) < 1e-3


def test_single_frame_polar_odd_ambiguity_rejected():
    raw = build_position_message("ABCDEF", 88.0, 100.0, odd=True)
    resp = post([single(raw, 6_000, reference(88.0, 100.0, 3_000, max_distance_nm=100.0))])
    (result,) = resp.json()["results"]
    assert result["status"] == "error"
    assert result["error"]["code"] == "POLAR_CPR_AMBIGUITY"


def test_reference_on_pair_is_ignored():
    # Two-frame requests stay compatible: an incidental reference is allowed
    # but has no effect on global decoding.
    resp = post([{
        "id": "p1",
        "frames": [frame(ODD, 1_000), frame(EVEN, 6_000)],
        "reference": reference(0.0, 0.0, 0),
    }])
    (result,) = resp.json()["results"]
    assert result["status"] == "ok"
    assert result["position"]["lat"] == 52.257202


def test_reference_field_ranges_rejected():
    bad_lat = single(EVEN, 6_000, reference(90.5, 3.92, 3_000))
    bad_lon = single(EVEN, 6_000, reference(52.26, 181.0, 3_000))
    too_small = single(EVEN, 6_000, reference(52.26, 3.92, 3_000, max_distance_nm=0.5))
    too_large = single(EVEN, 6_000, reference(52.26, 3.92, 3_000, max_distance_nm=501.0))
    for payload in (bad_lat, bad_lon, too_small, too_large):
        assert post([payload]).status_code == 422
