"""HTTP API: health check plus the paired ADS-B position decode endpoint."""
from __future__ import annotations

from fastapi import FastAPI

from .decoder import process_pair
from .schemas import DecodeRequest, DecodeResponse

app = FastAPI(
    title="ADS-B Position Decoder",
    version="1.1.0",
    summary="Decodes DF17 airborne-position frames: globally from even/odd "
            "pairs, or locally from a single frame against a trusted reference.",
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/adsb/positions/decode", response_model=DecodeResponse)
def decode_positions(request: DecodeRequest) -> DecodeResponse:
    """Adjudicate 1-200 uniquely-numbered groups of one or two frames.

    Two-frame groups are globally decoded as an even/odd pair; single-frame
    groups are locally decoded against their trusted reference and range
    checked.  Every group is processed independently: a bad group yields a
    stable error code under its own id and never shadows the healthy groups
    in the batch.
    """
    return DecodeResponse(results=[process_pair(pair) for pair in request.pairs])
