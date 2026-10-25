"""Find a picture on the web that is fine to show young children, for the e-ink screen.

Brave Image Search (strict SafeSearch) finds candidates. Only Brave's own thumbnails are downloaded,
never the source sites, so a search result cannot point this computer at the local network. Jev
(TypeSafe) then answers narrow yes/no questions about each thumbnail: does it show what was asked,
and is there anything sexual, violent, frightening or text-heavy. The first candidate that clears
every question is used.
"""

import io
import uuid
import base64
import asyncio
import logging
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from PIL import Image, UnidentifiedImageError


logger = logging.getLogger(__name__)

BRAVE_IMAGES_URL = "https://api.search.brave.com/res/v1/images/search"
THUMBNAIL_HOST = "imgs.search.brave.com"
TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
CANDIDATES = 4
REQUEST_TIMEOUT_S = 8.0
MAX_THUMBNAIL_BYTES = 3_000_000
MIN_SHORT_SIDE = 200
# Show a picture only when it very likely shows the subject and every risk is unlikely.
MIN_SHOWS_SUBJECT = 0.7
MAX_RISK = 0.2

_AUDIENCE = "children aged 2 to 5, at home, on a small black-and-white screen"
_QUESTIONS: dict[str, dict[str, object]] = {
    "shows_subject": {
        "type": "noul",
        "instructions": "Does the `image` clearly show the `subject` as its main content?",
    },
    "sexual": {
        "type": "noul",
        "instructions": "Does the `image` contain sexual content or nudity?",
        "criteria": {
            "true": "Nudity, sexualised poses, underwear or suggestive content in a photo, illustration or modern image",
            "false": "Nothing of the kind, or only nudity in classical or fine-art paintings and sculptures",
        },
    },
    "violent": {
        "type": "noul",
        "instructions": "Does the `image` show violence, blood, injury, weapons used against someone, or dead or suffering animals?",
    },
    "frightening": {
        "type": "noul",
        "instructions": "Would the `image` frighten or upset a young child in the `audience`?",
        "criteria": {
            "true": "Horror, monsters, menacing faces or teeth, disasters, distress, or anything a toddler would find scary",
            "false": "Friendly, neutral or merely impressive, like a rocket launch, a tall tower or a calm animal",
        },
    },
    "cluttered": {
        "type": "noul",
        "instructions": "Is the `image` mostly text, a screenshot, a meme, a collage or a diagram full of labels rather than one clear picture?",
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
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Keep the API keys; each find() opens its own HTTP client."""
        self._brave_api_key = brave_api_key
        self._typesafe_api_key = typesafe_api_key
        self._check_model = check_model
        self._transport = transport

    async def find(self, subject: str) -> WebPicture:
        """Return the best-ranked picture of ``subject`` that passes every check."""
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S, transport=self._transport) as client:
            candidates = await self._search(client, subject)
            pictures = await asyncio.gather(*(self._download(client, url) for url, _ in candidates))
            downloaded = [(picture, source) for picture, (_, source) in zip(pictures, candidates) if picture]
            if not downloaded:
                raise PictureError("no picture could be downloaded")
            verdicts = await asyncio.gather(*(self._approved(client, subject, picture) for picture, _ in downloaded))
        for (picture, source), approved in zip(downloaded, verdicts):
            if approved:
                return WebPicture(picture, source)
        raise PictureError("no picture passed the check")

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

    async def _approved(self, client: httpx.AsyncClient, subject: str, picture: Image.Image) -> bool:
        """Ask Jev about ``picture``; True only if it shows the subject and every risk is unlikely."""
        jpeg = io.BytesIO()
        picture.save(jpeg, format="JPEG", quality=85)
        placeholder = str(uuid.uuid4())
        try:
            response = await client.post(
                TYPESAFE_URL,
                headers={
                    "Authorization": f"Bearer {self._typesafe_api_key}",
                    "TypeSafe-Danger-Zone-Images": "true",
                },
                json={
                    "model": self._check_model,
                    "state": {"subject": subject, "audience": _AUDIENCE, "image": placeholder},
                    "questions": _QUESTIONS,
                    "assets": {
                        placeholder: {
                            "type": "image",
                            "format": "jpg",
                            "data_base64": base64.b64encode(jpeg.getvalue()).decode(),
                        }
                    },
                },
            )
            response.raise_for_status()
            answers = {name: float(answer["noul"]) for name, answer in response.json()["answers"].items()}
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            logger.warning("Picture check failed: %s", type(error).__name__)
            return False
        risks = [answers.get(name, 1.0) for name in _QUESTIONS if name != "shows_subject"]
        approved = answers.get("shows_subject", 0.0) >= MIN_SHOWS_SUBJECT and max(risks) <= MAX_RISK
        scores = ", ".join(f"{name}={value:.2f}" for name, value in sorted(answers.items()))
        logger.info("Picture check: %s (%s)", "approved" if approved else "rejected", scores)
        return approved
