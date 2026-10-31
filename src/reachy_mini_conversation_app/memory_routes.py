"""Household memory routes for the admin app, served by the running app's control API (it owns the memory file).

Reachy writes memories all the time; going through his process keeps the admin app's changes under the same lock.
A change reaches him at his next conversation, whose instructions are built from the memory file.
"""

import json
import asyncio
import logging
from typing import Any, Final
from pathlib import Path
from collections.abc import Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.household_memory import (
    MemoryEntry,
    MemoryStoreError,
    HouseholdMemoryStore,
    MemoryValidationError,
    MigrationRequiredError,
)


logger = logging.getLogger(__name__)

_NO_STORE: Final[dict[str, str]] = {"Cache-Control": "no-store"}
MAX_BODY_BYTES: Final[int] = 4096


def memory_json(entry: MemoryEntry) -> dict[str, Any]:
    """Return what the admin app shows of one memory."""
    return {
        "id": entry.id,
        "text": entry.text,
        "subject": entry.subject_id,
        "kind": entry.kind,
        "certainty": entry.certainty,
        "status": entry.status,
        "learned_at": entry.learned_at.isoformat(),
        "updated_at": entry.updated_at.isoformat(),
        "event_start": entry.event_start,
        "event_end": entry.event_end,
        "active_until": entry.active_until.isoformat() if entry.active_until else None,
    }


def register_memory_routes(
    app: FastAPI, instance_path: str | Path | None, access_denied: Callable[..., Response | None]
) -> None:
    """Add ``GET /memory``, ``POST /memory/update`` and ``POST /memory/forget`` to ``app``."""

    def refused(request: Request, mutation: bool = False) -> Response | None:
        denied = access_denied(request, mutation=mutation)
        if denied is not None:
            return denied
        if not config.HOUSEHOLD_MEMORY_ENABLED:
            return JSONResponse({"error": "Memory is turned off."}, status_code=404, headers=_NO_STORE)
        return None

    async def body(request: Request) -> dict[str, Any] | Response:
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            return Response(status_code=415, headers=_NO_STORE)
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            return Response(status_code=413, headers=_NO_STORE)
        try:
            payload = json.loads(raw)
        except ValueError:
            return Response(status_code=400, headers=_NO_STORE)
        if not isinstance(payload, dict) or not isinstance(payload.get("id"), str) or not payload["id"].strip():
            return Response(status_code=400, headers=_NO_STORE)
        return payload

    def failure(error: MemoryStoreError) -> Response:
        if isinstance(error, MigrationRequiredError):
            return JSONResponse({"error": "Memory needs migrating first."}, status_code=409, headers=_NO_STORE)
        if isinstance(error, MemoryValidationError):
            return JSONResponse({"error": str(error).capitalize() + "."}, status_code=422, headers=_NO_STORE)
        logger.warning("Memory unavailable: %s", type(error).__name__)
        return JSONResponse({"error": "Memory is unavailable."}, status_code=503, headers=_NO_STORE)

    @app.get("/memory")
    async def _memory(request: Request) -> Response:
        denied = refused(request)
        if denied is not None:
            return denied
        try:
            entries = await asyncio.to_thread(HouseholdMemoryStore(instance_path).list_entries, True)
        except MemoryStoreError as error:
            return failure(error)
        return JSONResponse({"entries": [memory_json(entry) for entry in entries]}, headers=_NO_STORE)

    @app.post("/memory/update")
    async def _update(request: Request) -> Response:
        denied = refused(request, mutation=True)
        if denied is not None:
            return denied
        payload = await body(request)
        if isinstance(payload, Response):
            return payload
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            return JSONResponse({"error": "A memory needs some text."}, status_code=422, headers=_NO_STORE)
        store = HouseholdMemoryStore(instance_path)
        try:
            entry = await asyncio.to_thread(lambda: store.update(payload["id"].strip(), text=text.strip()))
        except MemoryStoreError as error:
            return failure(error)
        return JSONResponse({"entry": memory_json(entry)}, headers=_NO_STORE)

    @app.post("/memory/forget")
    async def _forget(request: Request) -> Response:
        denied = refused(request, mutation=True)
        if denied is not None:
            return denied
        payload = await body(request)
        if isinstance(payload, Response):
            return payload
        store = HouseholdMemoryStore(instance_path)
        try:
            result = await asyncio.to_thread(lambda: store.forget(memory_id=payload["id"].strip()))
        except MemoryStoreError as error:
            return failure(error)
        removed = result.removed
        return JSONResponse(
            {"removed": removed is not None, "subject": removed.subject_id if removed else None}, headers=_NO_STORE
        )
