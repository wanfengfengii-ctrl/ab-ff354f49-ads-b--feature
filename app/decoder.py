"""Per-group adjudication: validate frames, then global or local CPR decode."""
from __future__ import annotations

from .adsb import PositionFrame, parse_position_frame
from .cpr import global_decode, local_decode
from .errors import DecodeError
from .geo import great_circle_distance_nm
from .schemas import ErrorInfo, PairIn, PairResult, Position

#: Even/odd pairs must be received no more than 10 seconds apart.
MAX_GAP_MS = 10_000

#: A single-frame reference must not post-date the frame and must be no
#: older than 30 seconds at receive time.
MAX_REFERENCE_AGE_MS = 30_000


def process_pair(pair: PairIn) -> PairResult:
    """Adjudicate one group; a failure here never affects other groups."""
    try:
        frames = [
            parse_position_frame(frame.raw, frame.time_ms, index=i)
            for i, frame in enumerate(pair.frames)
        ]

        if len(frames) == 1:
            lat, lon, frame = _decode_single(frames[0], pair)
        else:
            lat, lon, frame = _decode_pair(frames[0], frames[1])

        return PairResult(
            id=pair.id,
            status="ok",
            position=Position(
                lat=round(lat, 6),
                lon=round(lon, 6),
                time_ms=frame.time_ms,
                icao=frame.icao,
                frame="odd" if frame.odd else "even",
            ),
        )
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


def _decode_pair(first: PositionFrame, second: PositionFrame):
    """Globally decode one even/odd pair; returns (lat, lon, newer frame)."""
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
    return lat, lon, _newer(even, odd)


def _decode_single(frame: PositionFrame, pair: PairIn):
    """Locally decode one frame against the trusted reference fix."""
    ref = pair.reference
    if ref is None:
        raise DecodeError(
            "MISSING_REFERENCE",
            "single-frame groups require a reference fix for local CPR decoding",
        )

    age_ms = frame.time_ms - ref.time_ms
    if age_ms < 0:
        raise DecodeError(
            "REFERENCE_IN_FUTURE",
            f"reference time {ref.time_ms} must not be later than the frame "
            f"time {frame.time_ms}",
        )
    if age_ms > MAX_REFERENCE_AGE_MS:
        raise DecodeError(
            "REFERENCE_TOO_OLD",
            f"reference is {age_ms} ms older than the frame; the limit is "
            f"{MAX_REFERENCE_AGE_MS} ms",
        )

    lat, lon = local_decode(frame, ref.lat, ref.lon)

    distance_nm = great_circle_distance_nm(lat, lon, ref.lat, ref.lon)
    if distance_nm > ref.maxDistanceNm:
        raise DecodeError(
            "POSITION_OUT_OF_RANGE",
            f"decoded position is {distance_nm:.2f} NM from the reference; "
            f"the allowed radius is {ref.maxDistanceNm:g} NM",
        )
    return lat, lon, frame


def _newer(even: PositionFrame, odd: PositionFrame) -> PositionFrame:
    """The frame the reported position belongs to (ties resolve to even)."""
    return odd if odd.time_ms > even.time_ms else even
