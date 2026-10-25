"""Tests for finding and checking web pictures for the e-ink screen."""

import io
import json
import base64
from typing import Any

import httpx
import pytest
from PIL import Image

from reachy_mini_conversation_app.web_pictures import (
    TYPESAFE_URL,
    BRAVE_IMAGES_URL,
    WebPictures,
    PictureError,
)


SAFE = {"shows_subject": 0.95, "sexual": 0.02, "violent": 0.01, "frightening": 0.03, "cluttered": 0.05}


def _jpeg(size: tuple[int, int] = (500, 375), shade: int = 120) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (shade, shade, shade)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _result(index: int, host: str = "imgs.search.brave.com") -> dict[str, Any]:
    return {"url": f"https://www.site{index}.org/page", "thumbnail": {"src": f"https://{host}/thumb/{index}.jpg"}}


class _FakeWeb:
    """Answers Brave search, Brave thumbnails and Jev; ``verdicts[n - 1]`` is Jev's answer for result n."""

    def __init__(
        self,
        results: list[dict[str, Any]],
        verdicts: list[dict[str, float]],
        images: dict[str, bytes] | None = None,
        search_status: int = 200,
        check_status: int = 200,
    ) -> None:
        self.results = results
        self.verdicts = verdicts
        self.images = images or {}
        self.search_status = search_status
        self.check_status = check_status
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url.startswith(BRAVE_IMAGES_URL):
            return httpx.Response(self.search_status, json={"results": self.results})
        if url == TYPESAFE_URL:
            body = json.loads(request.content)
            asset = next(iter(body["assets"].values()))
            # Thumbnail n is a flat gray of n*40, so each check maps back to its search result.
            shade = Image.open(io.BytesIO(base64.b64decode(asset["data_base64"]))).convert("L").getpixel((10, 10))
            verdict = self.verdicts[round(float(shade) / 40) - 1]
            answers = {name: {"type": "noul", "noul": value} for name, value in verdict.items()}
            return httpx.Response(self.check_status, json={"model": "jev-1.14.0", "answers": answers})
        name = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, content=self.images.get(name, _jpeg(shade=int(name.split(".")[0]) * 40)))

    def pictures(self) -> WebPictures:
        return WebPictures(
            brave_api_key="brave-key",
            typesafe_api_key="ts-key",
            check_model="jev-latest",
            transport=httpx.MockTransport(self.handle),
        )


@pytest.mark.asyncio
async def test_the_first_ranked_picture_that_passes_every_check_is_chosen() -> None:
    """Top results are checked together; the best-ranked approved one wins, with its source site."""
    web = _FakeWeb([_result(1), _result(2), _result(3)], [{**SAFE, "frightening": 0.6}, SAFE, SAFE])

    found = await web.pictures().find("Mona Lisa painting")

    assert found.source == "site2.org"
    assert found.image.size == (500, 375)
    search = web.requests[0]
    assert search.url.params["safesearch"] == "strict" and search.headers["X-Subscription-Token"] == "brave-key"


@pytest.mark.asyncio
async def test_jev_gets_the_image_and_one_narrow_question_per_risk() -> None:
    """Each check sends the thumbnail as an image asset with the danger-zone header and five yes/no questions."""
    web = _FakeWeb([_result(1)], [SAFE])
    await web.pictures().find("Falcon 9 rocket")

    check = next(request for request in web.requests if str(request.url) == TYPESAFE_URL)
    body = json.loads(check.content)
    assert check.headers["Authorization"] == "Bearer ts-key"
    assert check.headers["TypeSafe-Danger-Zone-Images"] == "true"
    assert body["model"] == "jev-latest"
    assert set(body["questions"]) == set(SAFE)
    assert all(question["type"] == "noul" for question in body["questions"].values())
    placeholder, asset = next(iter(body["assets"].items()))
    assert body["state"]["image"] == placeholder and body["state"]["subject"] == "Falcon 9 rocket"
    assert asset["type"] == "image" and asset["format"] == "jpg"
    assert "classical or fine-art" in body["questions"]["sexual"]["criteria"]["false"]


@pytest.mark.asyncio
async def test_only_brave_thumbnails_are_ever_downloaded() -> None:
    """A result pointing anywhere but Brave's thumbnail server, like the local network, is never fetched."""
    web = _FakeWeb([_result(1, host="192.168.1.1"), _result(2, host="example.com"), _result(3)], [SAFE] * 3)

    found = await web.pictures().find("cat")

    assert found.source == "site3.org"
    hosts = {request.url.host for request in web.requests}
    assert hosts == {"api.search.brave.com", "imgs.search.brave.com", "api.typesafe.ai"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "verdict",
    [
        {**SAFE, "shows_subject": 0.6},
        {**SAFE, "sexual": 0.3},
        {**SAFE, "violent": 0.25},
        {**SAFE, "cluttered": 0.9},
        {name: value for name, value in SAFE.items() if name != "frightening"},
    ],
)
async def test_any_doubt_rejects_the_picture(verdict: dict[str, float]) -> None:
    """A weak subject match, any likely risk, or a missing answer means the picture is not shown."""
    with pytest.raises(PictureError, match="passed the check"):
        await _FakeWeb([_result(1)], [verdict]).pictures().find("cat")


@pytest.mark.asyncio
async def test_failures_raise_picture_errors() -> None:
    """A failed search, no results, a failed check or unusable thumbnails all end in a PictureError."""
    with pytest.raises(PictureError, match="search failed"):
        await _FakeWeb([], [], search_status=500).pictures().find("cat")
    with pytest.raises(PictureError, match="no pictures"):
        await _FakeWeb([], []).pictures().find("cat")
    with pytest.raises(PictureError, match="passed the check"):
        await _FakeWeb([_result(1)], [SAFE], check_status=429).pictures().find("cat")
    tiny = _FakeWeb([_result(1)], [SAFE], images={"1.jpg": _jpeg((120, 90))})
    with pytest.raises(PictureError, match="downloaded"):
        await tiny.pictures().find("cat")
    broken = _FakeWeb([_result(1)], [SAFE], images={"1.jpg": b"not an image"})
    with pytest.raises(PictureError, match="downloaded"):
        await broken.pictures().find("cat")
