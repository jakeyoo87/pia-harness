"""Stock name to code lookup on pia-broker, shared by the order and broker tools."""

from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True, slots=True)
class InstrumentLookup:
    """A found stock, or the sentence that tells the model what to ask instead."""

    code: str | None = None
    name: str | None = None
    problem: str | None = None


async def find_instrument(client: httpx.AsyncClient, name: str) -> InstrumentLookup:
    """Raises on an HTTP or response error; the caller decides what that means."""

    response = await client.get("/internal/instruments", params={"query": name})
    response.raise_for_status()
    found = response.json()
    if not isinstance(found, dict):
        raise TypeError("Broker response must be an object")
    status = found.get("status")
    if status == "AMBIGUOUS":
        candidates = ", ".join(
            f"{item['name']}({item['code']})" for item in found["candidates"]
        )
        return InstrumentLookup(
            problem=f"'{name}' matches several stocks: {candidates}. "
            "Ask the user which one, listing these names in the answer."
        )
    if status != "FOUND":
        return InstrumentLookup(
            problem=f"No listed stock matches '{name}'. Ask the user for the "
            "official listed name."
        )
    return InstrumentLookup(code=str(found["code"]), name=str(found["name"]))
