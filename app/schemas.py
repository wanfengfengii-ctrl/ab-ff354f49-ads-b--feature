"""Request/response schemas for the position decode endpoint."""
from __future__ import annotations

from typing import Annotated, Literal, Optional

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

MIN_PAIRS = 1
MAX_PAIRS = 200

MIN_MAX_DISTANCE_NM = 1
MAX_MAX_DISTANCE_NM = 500

#: Group ids arrive as "编号"; accept numbers as well as strings.
PairId = Annotated[str, BeforeValidator(lambda v: v if isinstance(v, str) else str(v))]


class FrameIn(BaseModel):
    """One received frame: receive time in epoch milliseconds + 28 hex chars."""

    model_config = ConfigDict(extra="forbid")

    time_ms: int = Field(description="receive timestamp, epoch milliseconds")
    raw: str = Field(description="28 hexadecimal characters (112-bit Mode S frame)")


class ReferenceIn(BaseModel):
    """Trusted contemporaneous reference fix for single-frame local CPR.

    Carries the fix (``lat``/``lon``/``time_ms``) plus the acceptance
    radius ``maxDistanceNm`` (1-500 nautical miles) used to reject CPR
    grid ambiguities that resolve far from the reference.
    """

    model_config = ConfigDict(extra="forbid")

    lat: float = Field(ge=-90.0, le=90.0, description="reference latitude in decimal degrees")
    lon: float = Field(ge=-180.0, le=180.0, description="reference longitude in decimal degrees")
    time_ms: int = Field(description="reference fix timestamp, epoch milliseconds")
    maxDistanceNm: float = Field(
        ge=MIN_MAX_DISTANCE_NM,
        le=MAX_MAX_DISTANCE_NM,
        description="acceptance radius around the reference, in nautical miles",
    )


class PairIn(BaseModel):
    """A numbered decode group: an even/odd pair, or one frame + reference."""

    model_config = ConfigDict(extra="forbid")

    id: PairId = Field(min_length=1, max_length=64)
    frames: list[FrameIn] = Field(min_length=1, max_length=2)
    # Required for single-frame groups; a missing reference is reported per
    # group with the stable MISSING_REFERENCE code (HTTP 200) so it never
    # invalidates the rest of the batch.
    reference: Optional[ReferenceIn] = Field(
        default=None,
        description="required when exactly one frame is supplied; ignored for pairs",
    )


class DecodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pairs: list[PairIn] = Field(min_length=MIN_PAIRS, max_length=MAX_PAIRS)

    @field_validator("pairs")
    @classmethod
    def _unique_ids(cls, pairs: list[PairIn]) -> list[PairIn]:
        seen: set[str] = set()
        for pair in pairs:
            if pair.id in seen:
                raise ValueError(f"duplicate pair id: {pair.id!r}")
            seen.add(pair.id)
        return pairs


class Position(BaseModel):
    """Decoded position of the reported frame."""

    lat: float = Field(description="latitude in decimal degrees, 6 decimal places")
    lon: float = Field(description="longitude in decimal degrees, normalised to [-180, 180)")
    time_ms: int = Field(description="receive time of the frame the position was decoded from")
    icao: str = Field(description="24-bit ICAO address, uppercase hex")
    frame: Literal["even", "odd"] = Field(description="CPR flag of the decoded frame")


class ErrorInfo(BaseModel):
    code: str = Field(description="stable machine-readable error code")
    message: str = Field(description="human-readable detail")


class PairResult(BaseModel):
    """Per-group verdict; exactly one of ``position`` / ``error`` is set."""

    id: str
    status: Literal["ok", "error"]
    position: Optional[Position] = None
    error: Optional[ErrorInfo] = None


class DecodeResponse(BaseModel):
    results: list[PairResult]
