"""Find a picture on the web that is fine to show young children, for the e-ink screen.

Brave Image Search (strict SafeSearch) finds candidates. Only Brave's own thumbnails are downloaded,
never the source sites, so a search result cannot point this computer at the local network. Jev
(TypeSafe) then answers narrow yes/no questions about each thumbnail: does it show what was asked,
is there nudity outside fine art, anything sexual, violent or meant for adults, is it a logo or
clutter rather than a picture, and, looking at it as the four-gray screen will show it, is the
subject still easy to recognise. Fierce animals with open jaws are fine. Of the candidates that
clear every question, the clearest on screen is used.
"""

import io
import uuid
import base64
import asyncio
import logging
from typing import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from PIL import Image, UnidentifiedImageError


logger = logging.getLogger(__name__)

BRAVE_IMAGES_URL = "https://api.search.brave.com/res/v1/images/search"
THUMBNAIL_HOST = "imgs.search.brave.com"
TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
CANDIDATES = 6
REQUEST_TIMEOUT_S = 8.0
MAX_THUMBNAIL_BYTES = 3_000_000
MIN_SHORT_SIDE = 200
# Jev rejects images with a short side under ~280 px or a long side over ~1000 px (probed 2026-10-25).
CHECK_SIDES = (300, 1000)
# Show a picture only when it very likely shows the subject and every risk is unlikely.
MIN_SHOWS_SUBJECT = 0.7
# Calibrated on real searches (2026-10-25): smoking, beer, horror and zombies score mature 0.84-0.98
# and Botticelli's Venus 0.17-0.27; boxing, battles, zombies and a bloody T. rex score violent 0.45-0.97.
MAX_RISKS = {"sexual": 0.2, "violent": 0.3, "mature": 0.4, "cluttered": 0.3}
# Nudity is allowed only in classical and fine-art paintings and sculptures, whose nudes still score
# sexual 0.24-0.29 when Jev is sure they are fine art.
MAX_NUDITY, MIN_FINE_ART, FINE_ART_MAX_SEXUAL = 0.2, 0.8, 0.4
# Realistic blood or gore is never shown. Fine art may depict violence (Guernica) as long as it is not graphic.
MAX_GRAPHIC = 0.3
# Judged on the four-gray screen version: too dark or tiny subjects fall below this.
MIN_CLEAR_ON_SCREEN = 0.5

_AUDIENCE = "children aged 2 to 5, at home, on a small black-and-white screen"
_QUESTIONS: dict[str, dict[str, object]] = {
    "shows_subject": {
        "type": "noul",
        "instructions": "Does the `image` clearly show the `subject` as its main content?",
    },
    "nudity": {
        "type": "noul",
        "instructions": "Does the `image` show a naked or partly naked person?",
        "criteria": {
            "true": "Bare breasts, buttocks or genitals, or a person shown in underwear",
            "false": "Clothed people, or only bare faces, hands, arms or legs",
        },
    },
    "fine_art": {
        "type": "noul",
        "instructions": "Is the `image` a classical or fine-art painting or sculpture, or a photo of one?",
    },
    "sexual": {
        "type": "noul",
        "instructions": "Is the `image` sexual or suggestive?",
        "criteria": {
            "true": "Erotic content, sexualised poses, a focus on the body, or underwear or swimwear modelling",
            "false": "Nothing sexual, including the nude figures of classical paintings and sculptures",
        },
    },
    "violent": {
        "type": "noul",
        "instructions": "Does the `image` show violence or harm?",
        "criteria": {
            "true": (
                "Fighting, an attack on a person or an animal, prey being caught, blood, injury, weapons used "
                "against someone, or dead or suffering animals"
            ),
            "false": "No one being harmed, including fierce animals with open jaws and roaring dinosaurs",
        },
    },
    "graphic": {
        "type": "noul",
        "instructions": "Does the `image` show blood, gore or injuries realistically?",
        "criteria": {
            "true": "Realistic blood, open wounds, gore or dead bodies shown in detail",
            "false": "No blood or wounds, or violence shown in a stylised or abstract way, as in a cubist painting",
        },
    },
    "mature": {
        "type": "noul",
        "instructions": "Is the `image` meant for adults rather than children?",
        "criteria": {
            "true": "Horror, gore, drugs, alcohol, smoking, gambling, crude gestures or other adult themes",
            "false": (
                "Fine for children, including wild or fierce animals such as sharks, tigers and dinosaurs with "
                "open jaws, natural events like volcanoes, and famous works of art"
            ),
        },
    },
    "cluttered": {
        "type": "noul",
        "instructions": (
            "Is the `image` a logo, icon, clip art, text, a screenshot, a meme, a collage or a diagram full of "
            "labels, rather than one clear photo, painting or illustration?"
        ),
    },
    "clear_on_screen": {
        "type": "noul",
        "instructions": (
            "The `screen` image is how the picture will look on a small black-and-white e-ink screen. On it, "
            "can a young child easily recognise the `subject`?"
        ),
        "criteria": {
            "true": "The subject is large, well lit and stands out clearly from the background",
            "false": "Too dark, washed out, tiny, blurry or lost in a busy background",
        },
    },
}


class PictureError(RuntimeError):
    """No suitable picture could be found."""


@dataclass(frozen=True)
class WebPicture:
    """An approved picture and the site it comes from."""

    image: Image.Image
    source: str


class WebPictures:
    """Search, download and check pictures."""

    def __init__(
        self,
        *,
        brave_api_key: str,
        typesafe_api_key: str,
        check_model: str,
        screen_preview: Callable[[Image.Image], bytes],
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Keep the API keys; ``screen_preview`` renders a picture as the e-ink screen will show it."""
        self._brave_api_key = brave_api_key
        self._typesafe_api_key = typesafe_api_key
        self._check_model = check_model
        self._screen_preview = screen_preview
        self._transport = transport

    async def find(self, subject: str) -> WebPicture:
        """Return the picture of ``subject`` that passes every check and is clearest on screen."""
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S, transport=self._transport) as client:
            candidates = await self._search(client, subject)
            pictures = await asyncio.gather(*(self._download(client, url) for url, _ in candidates))
            downloaded = [(picture, source) for picture, (_, source) in zip(pictures, candidates) if picture]
            if not downloaded:
                raise PictureError("no picture could be downloaded")
            clarity = await asyncio.gather(*(self._clarity(client, subject, picture) for picture, _ in downloaded))
        approved = [
            (score, -rank, picture, source)
            for rank, ((picture, source), score) in enumerate(zip(downloaded, clarity))
            if score is not None
        ]
        if not approved:
            raise PictureError("no picture passed the check")
        _, _, picture, source = max(approved, key=lambda candidate: candidate[:2])
        return WebPicture(picture, source)

    async def _search(self, client: httpx.AsyncClient, subject: str) -> list[tuple[str, str]]:
        """Return (thumbnail URL, source site) for the top results."""
        try:
            response = await client.get(
                BRAVE_IMAGES_URL,
                params={"q": subject, "safesearch": "strict", "count": 20},
                headers={"X-Subscription-Token": self._brave_api_key, "Accept": "application/json"},
            )
            response.raise_for_status()
            results = response.json().get("results", [])
        except (httpx.HTTPError, ValueError) as error:
            raise PictureError(f"image search failed ({type(error).__name__})") from error
        candidates: list[tuple[str, str]] = []
        for result in results if isinstance(results, list) else []:
            thumbnail = (result.get("thumbnail") or {}).get("src") if isinstance(result, dict) else None
            if isinstance(thumbnail, str) and urlparse(thumbnail).hostname == THUMBNAIL_HOST:
                source = urlparse(str(result.get("url") or "")).hostname or ""
                candidates.append((thumbnail, source.removeprefix("www.")))
            if len(candidates) == CANDIDATES:
                break
        if not candidates:
            raise PictureError("the search found no pictures")
        return candidates

    async def _download(self, client: httpx.AsyncClient, url: str) -> Image.Image | None:
        """Return the thumbnail as a grayscale image, or None if it is unusable."""
        try:
            response = await client.get(url, headers={"Accept": "image/jpeg,image/png"}, follow_redirects=False)
            response.raise_for_status()
            if len(response.content) > MAX_THUMBNAIL_BYTES:
                return None
            with Image.open(io.BytesIO(response.content)) as opened:
                picture = opened.convert("RGB")
        except (httpx.HTTPError, UnidentifiedImageError, OSError) as error:
            logger.info("Picture candidate skipped: %s", type(error).__name__)
            return None
        return picture if min(picture.size) >= MIN_SHORT_SIDE else None

    async def _clarity(self, client: httpx.AsyncClient, subject: str, picture: Image.Image) -> float | None:
        """Ask Jev about ``picture``; its clarity on screen if it passes every check, else None."""
        short_side, long_side = CHECK_SIDES
        scale = max(short_side / min(picture.size), 1.0)
        scale = min(scale, long_side / max(picture.size))
        sized = picture.resize((round(picture.width * scale), round(picture.height * scale)))
        jpeg = io.BytesIO()
        sized.save(jpeg, format="JPEG", quality=85)
        with Image.open(io.BytesIO(self._screen_preview(picture))) as rendered:
            screen = io.BytesIO()
            rendered.convert("L").save(screen, format="JPEG", quality=90)
        placeholder, screen_placeholder = str(uuid.uuid4()), str(uuid.uuid4())
        try:
            response = await client.post(
                TYPESAFE_URL,
                headers={
                    "Authorization": f"Bearer {self._typesafe_api_key}",
                    "TypeSafe-Danger-Zone-Images": "true",
                },
                json={
                    "model": self._check_model,
                    "state": {
                        "subject": subject,
                        "audience": _AUDIENCE,
                        "image": placeholder,
                        "screen": screen_placeholder,
                    },
                    "questions": _QUESTIONS,
                    "assets": {
                        placeholder: {
                            "type": "image",
                            "format": "jpg",
                            "data_base64": base64.b64encode(jpeg.getvalue()).decode(),
                        },
                        screen_placeholder: {
                            "type": "image",
                            "format": "jpg",
                            "data_base64": base64.b64encode(screen.getvalue()).decode(),
                        },
                    },
                },
            )
            response.raise_for_status()
            answers = {name: float(answer["noul"]) for name, answer in response.json()["answers"].items()}
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            logger.warning("Picture check failed: %s", type(error).__name__)
            return None
        fine_art = answers.get("fine_art", 0.0) >= MIN_FINE_ART
        limits = {**MAX_RISKS, "sexual": FINE_ART_MAX_SEXUAL, "violent": 1.0} if fine_art else MAX_RISKS
        approved = (
            answers.get("shows_subject", 0.0) >= MIN_SHOWS_SUBJECT
            and all(answers.get(name, 1.0) <= limit for name, limit in limits.items())
            and (answers.get("nudity", 1.0) <= MAX_NUDITY or fine_art)
            and answers.get("graphic", 1.0) <= MAX_GRAPHIC
            and answers.get("clear_on_screen", 0.0) >= MIN_CLEAR_ON_SCREEN
        )
        scores = ", ".join(f"{name}={value:.2f}" for name, value in sorted(answers.items()))
        logger.info("Picture check: %s (%s)", "approved" if approved else "rejected", scores)
        return answers["clear_on_screen"] if approved else None
