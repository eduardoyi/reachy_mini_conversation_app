"""Ask Jev (TypeSafe's System One model) narrow questions about text and images.

One request carries every question about the same state; images are sent as assets that the state and the
questions name in backticks. Answers come back typed: a Noul is a probability of yes, a Choice the most likely
option with every option's probability. Each request is added to the usage ledger under its feature.
"""

import io
import uuid
import base64
import logging
from typing import Any

import httpx
from PIL import Image

from reachy_mini_conversation_app.usage_costs import get_ledger


logger = logging.getLogger(__name__)

TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
# Jev rejects images with a short side under ~280 px or a long side over ~1000 px (probed 2026-10-25).
CHECK_SIDES = (300, 1000)


class JevError(RuntimeError):
    """Jev could not be reached or answered something unreadable."""


def sized_jpeg(picture: Image.Image, *, quality: int = 85, grayscale: bool = False) -> bytes:
    """Return ``picture`` as a JPEG Jev accepts: scaled so its sides fall within CHECK_SIDES."""
    short_side, long_side = CHECK_SIDES
    scale = max(short_side / min(picture.size), 1.0)
    scale = min(scale, long_side / max(picture.size))
    sized = picture.resize((round(picture.width * scale), round(picture.height * scale)))
    data = io.BytesIO()
    sized.convert("L" if grayscale else "RGB").save(data, format="JPEG", quality=quality)
    return data.getvalue()


async def ask(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    model: str,
    state: dict[str, Any],
    questions: dict[str, dict[str, Any]],
    images: dict[str, bytes],
    feature: str,
) -> dict[str, dict[str, Any]]:
    """Ask ``questions`` about ``state`` and return the answers by question id.

    ``images`` maps state keys to JPEG bytes; each becomes an asset the questions refer to by that key.
    Raises JevError when the request fails or the answer cannot be read.
    """
    state = dict(state)
    assets: dict[str, dict[str, str]] = {}
    for key, jpeg in images.items():
        placeholder = str(uuid.uuid4())
        state[key] = placeholder
        assets[placeholder] = {"type": "image", "format": "jpg", "data_base64": base64.b64encode(jpeg).decode()}
    try:
        response = await client.post(
            TYPESAFE_URL,
            headers={"Authorization": f"Bearer {api_key}", "TypeSafe-Danger-Zone-Images": "true"},
            json={"model": model, "state": state, "questions": questions, "assets": assets},
        )
        response.raise_for_status()
        body = response.json()
        answers = body["answers"]
        if not isinstance(answers, dict):
            raise TypeError("answers")
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
        raise JevError(type(error).__name__) from error
    usage = body.get("usage") or {}
    _record_usage(
        feature,
        str(body.get("model") or model),
        {"input_text": int(usage.get("input_tokens") or 0), "output_text": int(usage.get("output_tokens") or 0)},
    )
    return answers


def _record_usage(feature: str, model: str, tokens: dict[str, int]) -> None:
    """Add one request to the usage ledger; never raises."""
    try:
        get_ledger().record(feature, model, {kind: count for kind, count in tokens.items() if count})
    except Exception as error:  # usage tracking must never break a feature
        logger.debug("Usage tracking failed: %s", type(error).__name__)
