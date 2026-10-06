"""HTTP API: health check plus the ADS-B position decode endpoint."""
from __future__ import annotations

from fastapi import FastAPI

from .decoder import process_pair
from .schemas import DecodeRequest, DecodeResponse

app = FastAPI(
    title="ADS-B Position Decoder",
    version="1.1.0",
    summary="Adjudicates DF17 airborne-position frames: even/odd pairs are "
            "globally decoded; lone frames are locally decoded against a "
            "trusted contemporaneous reference and radius-checked.",
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/adsb/positions/decode", response_model=DecodeResponse)
def decode_positions(request: DecodeRequest) -> DecodeResponse:
    """Adjudicate 1-200 uniquely-numbered frame groups.

    A group carries one or two frames.  Two frames are globally decoded as
    an even/odd pair; a single frame is locally decoded against its
    reference.  Every group is processed independently: a bad group yields a
    stable error code under its own id and never shadows the healthy groups
    in the batch.
    """
    return DecodeResponse(results=[process_pair(pair) for pair in request.pairs])
