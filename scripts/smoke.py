#!/usr/bin/env python3
"""Interface smoke test for the ADS-B position decoder.

Covers: health check, a valid even/odd pair, a bad-CRC pair (stable error
code + id echo), a mixed batch proving a bad group never shadows a good
one, single-frame local CPR against a trusted reference (success, out of
radius, and reference too new/old), plus an antimeridian-crossing single
frame.  Exits 0 on success, 1 on any failure.

Usage: python scripts/smoke.py [base-url]   (default http://localhost:8000)
"""
import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://localhost:8000"

# Real captured DF17 airborne-position pair, ICAO 40621D.
EVEN = "8D40621D58C382D690C8AC2863A7"
ODD = "8D40621D58C386435CC412692AD6"
BAD_CRC = EVEN[:-1] + "0"  # EVEN ends in "7"; flipping the parity nibble breaks CRC

failures = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def post(payload):
    req = urllib.request.Request(
        f"{BASE}/api/adsb/positions/decode",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, json.loads(resp.read())


def main():
    try:
        with urllib.request.urlopen(f"{BASE}/health", timeout=10) as resp:
            health = json.loads(resp.read())
        check("health check", health.get("status") == "ok", json.dumps(health))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        check("health check", False, f"API unreachable at {BASE}: {exc}")
        return 1

    # 1. A valid pair decodes; the newer (even, t=6000) frame wins.
    status, body = post({"pairs": [{"id": "smoke-valid", "frames": [
        {"time_ms": 1000, "raw": ODD},
        {"time_ms": 6000, "raw": EVEN},
    ]}]})
    result = body["results"][0]
    pos = result.get("position") or {}
    ok = (
        status == 200
        and result["status"] == "ok"
        and abs(pos.get("lat", 0) - 52.257202) < 1e-6
        and abs(pos.get("lon", 0) - 3.919373) < 1e-6
        and -180.0 <= pos.get("lon", 180.0) < 180.0
    )
    check("valid pair decodes to known position", ok, json.dumps(result))

    # 2. A corrupted frame is rejected with the stable CRC_MISMATCH code.
    status, body = post({"pairs": [{"id": "smoke-bad-crc", "frames": [
        {"time_ms": 1000, "raw": BAD_CRC},
        {"time_ms": 6000, "raw": ODD},
    ]}]})
    result = body["results"][0]
    ok = (
        result["id"] == "smoke-bad-crc"
        and result["status"] == "error"
        and result["error"]["code"] == "CRC_MISMATCH"
    )
    check("bad CRC rejected with stable code and id", ok, json.dumps(result))

    # 3. Mixed batch: the bad pair must not shadow the good one.
    status, body = post({"pairs": [
        {"id": "mix-bad", "frames": [
            {"time_ms": 0, "raw": BAD_CRC},
            {"time_ms": 5000, "raw": ODD},
        ]},
        {"id": "mix-good", "frames": [
            {"time_ms": 1000, "raw": ODD},
            {"time_ms": 6000, "raw": EVEN},
        ]},
    ]})
    by_id = {r["id"]: r for r in body["results"]}
    ok = (
        by_id.get("mix-bad", {}).get("status") == "error"
        and by_id["mix-bad"]["error"]["code"] == "CRC_MISMATCH"
        and by_id.get("mix-good", {}).get("status") == "ok"
    )
    check("bad pair does not shadow good pair", ok, json.dumps(body))

    # Synthetic frames for the single-frame checks (CPR encoding is
    # deterministic, so a frame built here is decodable by the remote API).
    try:
        from app.adsb import build_position_message
    except ImportError:
        check("single-frame checks available", False, "app package not importable")
        build_position_message = None

    def single(group_id, raw, time_ms, ref):
        return {"id": group_id, "frames": [{"time_ms": time_ms, "raw": raw}],
                "reference": ref}

    if build_position_message is not None:
        # 4. Single-frame local CPR against a contemporaneous reference.
        status, body = post({"pairs": [single(
            "single-ok", EVEN, 6000,
            {"lat": 52.26, "lon": 3.92, "time_ms": 3000, "maxDistanceNm": 25},
        )]})
        result = body["results"][0]
        pos = result.get("position") or {}
        ok = (
            status == 200
            and result["status"] == "ok"
            and abs(pos.get("lat", 0) - 52.257202) < 1e-6
            and abs(pos.get("lon", 0) - 3.919373) < 1e-6
            and pos.get("frame") == "even"
        )
        check("single frame decodes against reference", ok, json.dumps(result))

        # 5. The same CPR frame resolves to a distant cell against a far
        # reference; the acceptance radius must reject the grid ambiguity.
        status, body = post({"pairs": [single(
            "single-far", EVEN, 6000,
            {"lat": 0.0, "lon": 0.0, "time_ms": 3000, "maxDistanceNm": 100},
        )]})
        result = body["results"][0]
        ok = (
            result["status"] == "error"
            and result["error"]["code"] == "POSITION_OUT_OF_RANGE"
        )
        check("single frame outside radius rejected", ok, json.dumps(result))

        # 6. Reference timing: not later than the message, at most 30 s older.
        future = single("single-future", EVEN, 6000,
                        {"lat": 52.26, "lon": 3.92, "time_ms": 6001, "maxDistanceNm": 25})
        stale = single("single-stale", EVEN, 31000,
                       {"lat": 52.26, "lon": 3.92, "time_ms": 0, "maxDistanceNm": 25})
        fresh = single("single-fresh", EVEN, 30000,
                       {"lat": 52.26, "lon": 3.92, "time_ms": 0, "maxDistanceNm": 25})
        status, body = post({"pairs": [future, stale, fresh]})
        by_id = {r["id"]: r for r in body["results"]}
        ok = (
            by_id["single-future"]["error"]["code"] == "REFERENCE_IN_FUTURE"
            and by_id["single-stale"]["error"]["code"] == "REFERENCE_TOO_OLD"
            and by_id["single-fresh"]["status"] == "ok"
        )
        check("single frame reference timing enforced", ok, json.dumps(body))

        # 7. Antimeridian: reference at +179.99, target cell at -179.98.
        cross = build_position_message("ABCDEF", 10.0, -179.98, odd=False)
        status, body = post({"pairs": [single(
            "single-anti", cross, 6000,
            {"lat": 10.0, "lon": 179.99, "time_ms": 3000, "maxDistanceNm": 10},
        )]})
        result = body["results"][0]
        pos = result.get("position") or {}
        ok = (
            result["status"] == "ok"
            and abs(pos.get("lon", 0) - (-179.98)) < 1e-3
        )
        check("single frame crosses antimeridian to nearest cell", ok, json.dumps(result))

        # 8. High latitude: a legal even frame stays in the reference's cell.
        polar = build_position_message("ABCDEF", 85.0, 100.0, odd=False)
        status, body = post({"pairs": [single(
            "single-polar", polar, 6000,
            {"lat": 85.01, "lon": 100.02, "time_ms": 3000, "maxDistanceNm": 10},
        )]})
        result = body["results"][0]
        pos = result.get("position") or {}
        ok = result["status"] == "ok" and abs(pos.get("lat", 0) - 85.0) < 1e-3
        check("single frame decodes near the pole", ok, json.dumps(result))

        # 9. Mixed batch across both group shapes: a missing reference and an
        # out-of-range single frame must not shadow a good single frame or a
        # good pair.
        status, body = post({"pairs": [
            {"id": "mix-single-noref", "frames": [{"time_ms": 6000, "raw": EVEN}]},
            single("mix-single-bad", EVEN, 6000,
                   {"lat": 0.0, "lon": 0.0, "time_ms": 3000, "maxDistanceNm": 50}),
            single("mix-single-good", EVEN, 6000,
                   {"lat": 52.26, "lon": 3.92, "time_ms": 3000, "maxDistanceNm": 25}),
            {"id": "mix-pair-good", "frames": [
                {"time_ms": 1000, "raw": ODD},
                {"time_ms": 6000, "raw": EVEN},
            ]},
        ]})
        by_id = {r["id"]: r for r in body["results"]}
        ok = (
            by_id["mix-single-noref"]["error"]["code"] == "MISSING_REFERENCE"
            and by_id["mix-single-bad"]["error"]["code"] == "POSITION_OUT_OF_RANGE"
            and by_id["mix-single-good"]["status"] == "ok"
            and by_id["mix-pair-good"]["status"] == "ok"
        )
        check("single-frame failure does not shadow batch", ok, json.dumps(body))

    if failures:
        print(f"SMOKE FAILED: {len(failures)} check(s): {', '.join(failures)}")
        return 1
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
