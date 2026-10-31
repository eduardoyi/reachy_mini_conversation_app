"""Face routes shared by the settings page and the app's control API: list, photos, enroll and forget."""

import json
import base64
import asyncio
import logging
from typing import Any, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse

from reachy_mini_conversation_app.face_identity import EnrolledPerson, FaceRecognitionService


logger = logging.getLogger(__name__)


def register_face_routes(
    app: FastAPI, service: FaceRecognitionService | None, access_denied: Callable[..., Response | None]
) -> None:
    """Faces: list, view photos, enroll from the live camera or photos, and delete, behind ``access_denied``.

    Without face recognition no route is added, so every Faces route is a 404.
    """
    if service is None:
        return
    no_store = {"Cache-Control": "no-store"}

    async def _small_json(
        request: Request, keys: set[str], *, max_bytes: int = 1_024, string_keys: set[str] | None = None
    ) -> dict[str, Any] | Response:
        """Read a JSON object with exactly ``keys``; ``string_keys`` (default: all) must be strings."""
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            return Response(status_code=415, headers=no_store)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > max_bytes:
                return Response(status_code=413, headers=no_store)
        try:
            payload: object = json.loads(body)
        except (ValueError, UnicodeError):
            return Response(status_code=400, headers=no_store)
        if (
            not isinstance(payload, dict)
            or set(payload) != keys
            or not all(isinstance(payload[k], str) for k in (keys if string_keys is None else string_keys))
        ):
            return Response(status_code=400, headers=no_store)
        return payload

    def _person_json(person: EnrolledPerson) -> dict[str, Any]:
        return {
            "id": person.person_id,
            "name": person.name,
            "samples": person.sample_count,
            "photos": list(person.photos),
            "updated_at": person.updated_at,
        }

    @app.get("/api/faces")
    async def _faces_list(request: Request) -> Response:
        denied = access_denied(request)
        if denied is not None:
            return denied
        try:
            people = await asyncio.to_thread(service.registry.people)
        except (OSError, ValueError):
            logger.warning("Face list unavailable")
            return JSONResponse({"error": "Saved faces are unavailable."}, status_code=503, headers=no_store)
        return JSONResponse({"people": [_person_json(p) for p in people]}, headers=no_store)

    @app.get("/api/faces/photo")
    async def _faces_photo(request: Request, person_id: str, photo: str) -> Response:
        denied = access_denied(request)
        if denied is not None:
            return denied
        path = service.registry.photo_path(person_id, photo)
        if path is None:
            return Response(status_code=404, headers=no_store)
        return FileResponse(str(path), media_type="image/jpeg", headers=no_store)

    @app.post("/api/faces/enroll")
    async def _faces_enroll(request: Request) -> Response:
        denied = access_denied(request, mutation=True)
        if denied is not None:
            return denied
        payload = await _small_json(request, {"name"})
        if isinstance(payload, Response):
            return payload
        try:
            result = await asyncio.to_thread(service.enroll_from_camera, payload["name"], trust_name=True)
        except (OSError, ValueError, RuntimeError):
            logger.warning("Face enrollment unavailable")
            return JSONResponse({"error": "The camera is unavailable."}, status_code=503, headers=no_store)
        if result.person is None:
            return JSONResponse({"error": result.error}, status_code=422, headers=no_store)
        return JSONResponse({"person": _person_json(result.person)}, headers=no_store)

    @app.post("/api/faces/upload")
    async def _faces_upload(request: Request) -> Response:
        """Enroll from photos: ``{"name": str, "photos": [base64 JPEG/PNG, ...]}``, at most 12 MB."""
        denied = access_denied(request, mutation=True)
        if denied is not None:
            return denied
        payload = await _small_json(request, {"name", "photos"}, max_bytes=12 * 1024 * 1024, string_keys={"name"})
        if isinstance(payload, Response):
            return payload
        photos = payload["photos"]
        if not isinstance(photos, list) or not all(isinstance(photo, str) for photo in photos):
            return Response(status_code=400, headers=no_store)
        try:
            decoded = [base64.b64decode(photo, validate=True) for photo in photos]
        except ValueError:
            return Response(status_code=400, headers=no_store)
        try:
            result = await asyncio.to_thread(service.enroll_from_photos, payload["name"], decoded, trust_name=True)
        except (OSError, ValueError, RuntimeError):
            logger.warning("Face photo enrollment unavailable")
            return JSONResponse({"error": "Photos could not be processed."}, status_code=503, headers=no_store)
        if result.person is None:
            return JSONResponse(
                {"error": result.error, "skipped": list(result.skipped)}, status_code=422, headers=no_store
            )
        return JSONResponse({"person": _person_json(result.person), "skipped": list(result.skipped)}, headers=no_store)

    @app.post("/api/faces/check")
    async def _faces_check(request: Request) -> Response:
        """Say whether one photo (``{"name": str, "photo": base64}``) would enroll ``name``, without enrolling."""
        denied = access_denied(request, mutation=True)
        if denied is not None:
            return denied
        payload = await _small_json(request, {"name", "photo"}, max_bytes=4 * 1024 * 1024)
        if isinstance(payload, Response):
            return payload
        try:
            photo = base64.b64decode(payload["photo"], validate=True)
        except ValueError:
            return Response(status_code=400, headers=no_store)
        try:
            problem, detail = await asyncio.to_thread(service.check_photo, payload["name"], photo)
        except (OSError, ValueError, RuntimeError):
            logger.warning("Face photo check unavailable")
            return JSONResponse({"error": "Photos could not be checked."}, status_code=503, headers=no_store)
        return JSONResponse({"ok": not problem, "problem": problem, "detail": detail}, headers=no_store)

    @app.post("/api/faces/forget-photo")
    async def _faces_forget_photo(request: Request) -> Response:
        """Delete one photo and its fingerprint; the person goes too if it was their last."""
        denied = access_denied(request, mutation=True)
        if denied is not None:
            return denied
        payload = await _small_json(request, {"person_id", "photo"})
        if isinstance(payload, Response):
            return payload
        found, person = await asyncio.to_thread(service.registry.forget_photo, payload["person_id"], payload["photo"])
        if not found:
            return Response(status_code=404, headers=no_store)
        return JSONResponse({"person": _person_json(person) if person is not None else None}, headers=no_store)

    @app.post("/api/faces/forget")
    async def _faces_forget(request: Request) -> Response:
        denied = access_denied(request, mutation=True)
        if denied is not None:
            return denied
        payload = await _small_json(request, {"person_id"})
        if isinstance(payload, Response):
            return payload
        try:
            removed = await asyncio.to_thread(service.registry.forget, payload["person_id"])
        except (OSError, ValueError):
            return Response(status_code=400, headers=no_store)
        return JSONResponse({"removed": removed is not None}, headers=no_store)
