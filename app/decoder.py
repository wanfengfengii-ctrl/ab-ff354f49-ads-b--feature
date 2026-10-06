"""Per-group adjudication: validate frame(s), then global or local CPR decode."""
from __future__ import annotations

from .adsb import PositionFrame, parse_position_frame
from .cpr import global_decode, great_circle_distance_nm, local_decode
from .errors import DecodeError
from .schemas import ErrorInfo, PairIn, PairResult, Position

#: Even/odd pairs must be received no more than 10 seconds apart.
MAX_GAP_MS = 10_000

#: A single-frame reference fix must be contemporaneous: no later than the
#: message and at most this much older.
MAX_REFERENCE_AGE_MS = 30_000


def process_pair(pair: PairIn) -> PairResult:
    """Adjudicate one group; a failure here never affects other groups."""
    try:
        if len(pair.frames) == 1:
            position = _decode_single(pair)
        else:
            position = _decode_pair(pair)
        return PairResult(id=pair.id, status="ok", position=position)
    except DecodeError as exc:
        return PairResult(
            id=pair.id,
            status="error",
            error=ErrorInfo(code=exc.code, message=exc.message),
        )
    except Exception as exc:  # defensive: one group must never sink the batch
        return PairResult(
            id=pair.id,
            status="error",
            error=ErrorInfo(code="INTERNAL_ERROR", message=f"unexpected failure: {exc}"),
        )


def _decode_pair(pair: PairIn) -> Position:
    """Validate two opposite-flag frames and globally decode the newer one."""
    frames = [
        parse_position_frame(frame.raw, frame.time_ms, index=i)
        for i, frame in enumerate(pair.frames)
    ]
    first, second = frames

    if first.icao != second.icao:
        raise DecodeError(
            "ICAO_MISMATCH",
            f"ICAO addresses differ: {first.icao} vs {second.icao}",
        )
    if first.odd == second.odd:
        flag = "odd" if first.odd else "even"
        raise DecodeError(
            "SAME_CPR_FLAG",
            f"both frames carry the {flag} CPR flag; one even and one odd are required",
        )
    gap_ms = abs(first.time_ms - second.time_ms)
    if gap_ms > MAX_GAP_MS:
        raise DecodeError(
            "TIME_GAP_EXCEEDED",
            f"frames are {gap_ms} ms apart; the limit is {MAX_GAP_MS} ms",
        )

    even, odd = (second, first) if first.odd else (first, second)
    lat, lon = global_decode(even, odd)
    newer = _newer(even, odd)
    return Position(
        lat=round(lat, 6),
        lon=round(lon, 6),
        time_ms=newer.time_ms,
        icao=newer.icao,
        frame="odd" if newer.odd else "even",
    )


def _decode_single(pair: PairIn) -> Position:
    """Validate one frame against its reference and locally decode CPR."""
    if pair.reference is None:
        raise DecodeError(
            "MISSING_REFERENCE",
            "single-frame groups require a trusted reference point",
        )
    ref = pair.reference

    frame = parse_position_frame(pair.frames[0].raw, pair.frames[0].time_ms, index=0)

    if ref.time_ms > frame.time_ms:
        raise DecodeError(
            "REFERENCE_IN_FUTURE",
            f"reference time {ref.time_ms} is later than the message time "
            f"{frame.time_ms}",
        )
    age_ms = frame.time_ms - ref.time_ms
    if age_ms > MAX_REFERENCE_AGE_MS:
        raise DecodeError(
            "REFERENCE_TOO_OLD",
            f"reference is {age_ms} ms older than the message; the limit is "
            f"{MAX_REFERENCE_AGE_MS} ms",
        )

    # Local CPR picks the grid cell of the frame's parity nearest the
    # reference; the radius check below is what stops a grid ambiguity from
    # placing the target in a distant cell.
    lat, lon = local_decode(frame, ref.lat, ref.lon)
    distance_nm = great_circle_distance_nm(ref.lat, ref.lon, lat, lon)
    if distance_nm > ref.maxDistanceNm:
        raise DecodeError(
            "POSITION_OUT_OF_RANGE",
            f"decoded position is {distance_nm:.1f} NM from the reference; "
            f"outside the allowed radius of {ref.maxDistanceNm:g} NM",
        )

    return Position(
        lat=round(lat, 6),
        lon=round(lon, 6),
        time_ms=frame.time_ms,
        icao=frame.icao,
        frame="odd" if frame.odd else "even",
    )


def _newer(even: PositionFrame, odd: PositionFrame) -> PositionFrame:
    """The frame the reported position belongs to (ties resolve to even)."""
    return odd if odd.time_ms > even.time_ms else even
