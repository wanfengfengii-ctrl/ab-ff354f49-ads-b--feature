"""Request/response schemas for the position decode endpoint."""
from __future__ import annotations

import math
from typing import Annotated, Literal, Optional

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)

MIN_PAIRS = 1
MAX_PAIRS = 200

#: Allowed reference radius (nautical miles); timing limits live in decoder.
MIN_RADIUS_NM = 1
MAX_RADIUS_NM = 500

#: Pair ids arrive as "编号"; accept numbers as well as strings.
PairId = Annotated[str, BeforeValidator(lambda v: v if isinstance(v, str) else str(v))]


class FrameIn(BaseModel):
    """One received frame: receive time in epoch milliseconds + 28 hex chars."""

    model_config = ConfigDict(extra="forbid")

    time_ms: int = Field(description="receive timestamp, epoch milliseconds")
    raw: str = Field(description="28 hexadecimal characters (112-bit Mode S frame)")


class ReferenceIn(BaseModel):
    """Trusted contemporaneous fix used for single-frame local CPR decode."""

    model_config = ConfigDict(extra="forbid")

    lat: float = Field(ge=-90.0, le=90.0, description="reference latitude, decimal degrees")
    lon: float = Field(ge=-180.0, le=180.0, description="reference longitude, decimal degrees")
    time_ms: int = Field(description="reference timestamp, epoch milliseconds")
    maxDistanceNm: float = Field(
        ge=MIN_RADIUS_NM,
        le=MAX_RADIUS_NM,
        description="maximum acceptable great-circle radius around the reference, nautical miles",
    )

    @model_validator(mode="after")
    def _finite(self) -> "ReferenceIn":
        for name in ("lat", "lon", "maxDistanceNm"):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"{name} must be finite")
        return self


class PairIn(BaseModel):
    """One or two ADS-B airborne-position frames plus an id unique in the batch.

    Two frames are globally decoded as an even/odd pair; a single frame must
    carry a ``reference`` fix for local CPR decoding.  A missing reference on
    a single-frame group is *not* a request-level rejection: it is reported as
    a per-group stable error so the rest of the batch still adjudicates.
    """

    model_config = ConfigDict(extra="forbid")

    id: PairId = Field(min_length=1, max_length=64)
    frames: list[FrameIn] = Field(min_length=1, max_length=2)
    reference: Optional[ReferenceIn] = None

    @model_validator(mode="after")
    def _reject_reference_on_pair(self) -> "PairIn":
        if len(self.frames) == 2 and self.reference is not None:
            raise ValueError("reference is only valid for single-frame groups")
        return self


class DecodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pairs: list[PairIn] = Field(min_length=MIN_PAIRS, max_length=MAX_PAIRS)

    @model_validator(mode="after")
    def _unique_ids(self) -> "DecodeRequest":
        seen: set[str] = set()
        for pair in self.pairs:
            if pair.id in seen:
                raise ValueError(f"duplicate pair id: {pair.id!r}")
            seen.add(pair.id)
        return self


class Position(BaseModel):
    """Decoded position of the decoded frame of the group."""

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
