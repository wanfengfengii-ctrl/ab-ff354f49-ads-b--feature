#!/usr/bin/env python3
"""Interface smoke test for the ADS-B position decoder.

Covers:
  * health check;
  * a valid even/odd pair (double-frame regression);
  * a bad-CRC pair (stable error code + id echo);
  * a mixed batch proving a bad group never shadows a good one;
  * single-frame local decode against a reference (success);
  * single-frame out-of-radius rejection;
  * a legal single frame straddling the antimeridian;
  * single-frame missing-reference group error.

Exits 0 on success, 1 on any failure.

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

# Synthetic even frame encoding (lat 10.0, lon 179.98), ICAO ABCDEF.
ANTIMERIDIAN_EVEN = "8DABCDEF580002AAAAFE52255378"

failures = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f":: {detail}" if detail else ""))
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


def ref(lat, lon, time_ms, radius=50):
    return {"lat": lat, "lon": lon, "time_ms": time_ms, "maxDistanceNm": radius}


def single(group_id, raw, t_ms, reference):
    return {"id": group_id, "frames": [{"time_ms": t_ms, "raw": raw}],
            "reference": reference}


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
    check("valid pair decodes to known position (double-frame regression)", ok, json.dumps(result))

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

    # 3. Mixed batch: the bad group must not shadow the good one.
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
    check("bad group does not shadow good group", ok, json.dumps(body))

    # 4. Single frame decoded locally against a credible reference.
    status, body = post({"pairs": [single(
        "smoke-single", EVEN, 1_000_000, ref(52.25, 3.90, 999_000),
    )]})
    result = body["results"][0]
    pos = result.get("position") or {}
    ok = (
        status == 200
        and result["status"] == "ok"
        and pos.get("frame") == "even"
        and pos.get("time_ms") == 1_000_000
        and abs(pos.get("lat", 0) - 52.257202) < 1e-6
        and abs(pos.get("lon", 0) - 3.919373) < 1e-6
    )
    check("single frame locally decodes against reference", ok, json.dumps(result))

    # 5. Single frame outside the allowed radius: stable error, no position.
    status, body = post({"pairs": [single(
        "smoke-out-of-range", EVEN, 10_000, ref(51.0, 3.0, 9_000, radius=10),
    )]})
    result = body["results"][0]
    ok = (
        result["status"] == "error"
        and result.get("position") is None
        and result["error"]["code"] == "POSITION_OUT_OF_RANGE"
    )
    check("single frame out of radius rejected", ok, json.dumps(result))

    # 6. Legal single frame across the antimeridian: the fix is at +179.98,
    #    the trusted reference sits a few NM away at -179.95; shortest-path
    #    spherical distance keeps it inside the 10 NM radius.
    status, body = post({"pairs": [single(
        "smoke-antimeridian",
        ANTIMERIDIAN_EVEN,
        10_000,
        ref(10.0, -179.95, 9_000, radius=10),
    )]})
    result = body["results"][0]
    pos = result.get("position") or {}
    ok = (
        result["status"] == "ok"
        and abs(pos.get("lat", 0) - 10.0) < 1e-3
        and abs(pos.get("lon", 0) - 179.98) < 1e-3
    )
    check("antimeridian single frame lands on correct grid", ok, json.dumps(result))

    # 7. Missing reference: per-group stable error, still batch-isolated.
    status, body = post({"pairs": [
        {"id": "smoke-no-ref", "frames": [{"time_ms": 10_000, "raw": EVEN}]},
        {"id": "mix-good", "frames": [
            {"time_ms": 1000, "raw": ODD},
            {"time_ms": 6000, "raw": EVEN},
        ]},
    ]})
    by_id = {r["id"]: r for r in body["results"]}
    ok = (
        by_id["smoke-no-ref"]["status"] == "error"
        and by_id["smoke-no-ref"]["error"]["code"] == "MISSING_REFERENCE"
        and by_id["mix-good"]["status"] == "ok"
    )
    check("missing reference reported per group without shadowing", ok, json.dumps(body))

    if failures:
        print(f"SMOKE FAILED: {len(failures)} check(s): {', '.join(failures)}")
        return 1
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
