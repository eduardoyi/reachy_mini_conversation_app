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


SAFE = {
    "shows_subject": 0.95,
    "nudity": 0.02,
    "fine_art": 0.03,
    "sexual": 0.02,
    "violent": 0.01,
    "mature": 0.03,
    "graphic": 0.02,
    "clear_on_screen": 0.8,
    "cluttered": 0.05,
}
# Measured on Botticelli's Venus (2026-10-25): sure it is fine art, yet "sexual" around a quarter.
CLASSICAL_NUDE = {**SAFE, "nudity": 0.98, "fine_art": 0.98, "sexual": 0.25}


def _jpeg(size: tuple[int, int] = (500, 375), shade: int = 120) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (shade, shade, shade)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _screen(picture: Image.Image) -> bytes:
    """Stand in for the e-ink renderer: a small PNG Jev sees as the screen version."""
    buffer = io.BytesIO()
    Image.new("L", (800, 480), 200).save(buffer, format="PNG")
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
            screen_preview=_screen,
            transport=httpx.MockTransport(self.handle),
        )


@pytest.mark.asyncio
async def test_the_first_ranked_picture_that_passes_every_check_is_chosen() -> None:
    """Top results are checked together; the best-ranked approved one wins, with its source site."""
    web = _FakeWeb([_result(1), _result(2), _result(3)], [{**SAFE, "mature": 0.6}, SAFE, SAFE])

    found = await web.pictures().find("Mona Lisa painting")

    assert found.source == "site2.org"
    assert found.image.size == (500, 375)
    search = web.requests[0]
    assert search.url.params["safesearch"] == "strict" and search.headers["X-Subscription-Token"] == "brave-key"


@pytest.mark.asyncio
async def test_jev_gets_the_image_and_one_narrow_question_per_risk() -> None:
    """Each check sends the thumbnail as an image asset with the danger-zone header and one yes/no question per risk."""
    web = _FakeWeb([_result(1)], [SAFE])
    await web.pictures().find("Falcon 9 rocket")

    check = next(request for request in web.requests if str(request.url) == TYPESAFE_URL)
    body = json.loads(check.content)
    assert check.headers["Authorization"] == "Bearer ts-key"
    assert check.headers["TypeSafe-Danger-Zone-Images"] == "true"
    assert body["model"] == "jev-latest"
    assert set(body["questions"]) == set(SAFE)
    assert all(question["type"] == "noul" for question in body["questions"].values())
    (placeholder, asset), (screen_placeholder, screen) = body["assets"].items()
    assert body["state"]["image"] == placeholder and body["state"]["subject"] == "Falcon 9 rocket"
    assert body["state"]["screen"] == screen_placeholder
    assert asset["type"] == screen["type"] == "image" and asset["format"] == screen["format"] == "jpg"
    assert Image.open(io.BytesIO(base64.b64decode(screen["data_base64"]))).size == (800, 480)
    assert "`screen`" in body["questions"]["clear_on_screen"]["instructions"]
    assert "classical paintings and sculptures" in body["questions"]["sexual"]["criteria"]["false"]
    assert "open jaws" in body["questions"]["violent"]["criteria"]["false"]
    assert "open jaws" in body["questions"]["mature"]["criteria"]["false"]
    assert "cubist" in body["questions"]["graphic"]["criteria"]["false"]


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
        pytest.param({**SAFE, "shows_subject": 0.6}, id="weak-match"),
        pytest.param({**SAFE, "sexual": 0.3}, id="sexual"),
        pytest.param({**SAFE, "violent": 0.35}, id="violent"),
        pytest.param({**SAFE, "mature": 0.6}, id="mature"),
        pytest.param({**SAFE, "cluttered": 0.9}, id="logo-or-collage"),
        pytest.param({**SAFE, "nudity": 0.9}, id="nudity-outside-fine-art"),
        pytest.param({**SAFE, "clear_on_screen": 0.3}, id="too-dark-or-small-on-screen"),
        # Measured on Goya's The Third of May 1808: fine art, but realistic blood.
        pytest.param({**SAFE, "fine_art": 0.97, "violent": 0.97, "graphic": 0.85}, id="graphic-even-as-art"),
        pytest.param({**SAFE, "fine_art": 0.5, "violent": 0.94}, id="violent-and-unsure-it-is-art"),
        pytest.param({**CLASSICAL_NUDE, "sexual": 0.5}, id="suggestive-even-as-art"),
        pytest.param({**CLASSICAL_NUDE, "fine_art": 0.6}, id="unsure-it-is-art"),
        pytest.param({name: value for name, value in SAFE.items() if name != "mature"}, id="missing-answer"),
    ],
)
async def test_any_doubt_rejects_the_picture(verdict: dict[str, float]) -> None:
    """A weak match, any likely risk, nudity outside fine art, or a missing answer means the picture is not shown."""
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "verdict",
    [
        pytest.param(CLASSICAL_NUDE, id="classical-nude"),
        # Measured on roaring T. rex and open-jawed shark pictures (2026-10-25).
        pytest.param({**SAFE, "violent": 0.18, "mature": 0.15}, id="fierce-animal"),
        # Measured on Guardi's Piazzetta San Marco: a busy painting is shown rather than drawn.
        pytest.param({**SAFE, "fine_art": 0.97, "clear_on_screen": 0.17}, id="busy-painting"),
        # Measured on Picasso's Guernica: violent, but stylised rather than graphic.
        pytest.param(
            {**SAFE, "fine_art": 0.95, "violent": 0.90, "graphic": 0.06, "nudity": 0.67, "mature": 0.20},
            id="non-graphic-violent-art",
        ),
    ],
)
async def test_classical_nudes_fierce_animals_and_non_graphic_art_are_shown(verdict: dict[str, float]) -> None:
    """Fine-art nudity and non-graphic violence in fine art are allowed; open-jawed animals are not violence."""
    assert (await _FakeWeb([_result(1)], [verdict]).pictures().find("The Birth of Venus")).source == "site1.org"


@pytest.mark.asyncio
async def test_pictures_are_resized_to_sizes_jev_accepts() -> None:
    """Jev refuses a short side under ~280 px or a long side over ~1000 px, so each check is resized first."""
    web = _FakeWeb(
        [_result(1), _result(2)],
        [SAFE, SAFE],
        images={"1.jpg": _jpeg((500, 266), shade=40), "2.jpg": _jpeg((2400, 1200), shade=80)},
    )
    await web.pictures().find("shark")

    for request in web.requests:
        if str(request.url) == TYPESAFE_URL:
            asset = next(iter(json.loads(request.content)["assets"].values()))
            size = Image.open(io.BytesIO(base64.b64decode(asset["data_base64"]))).size
            assert min(size) >= 300 and max(size) <= 1000


@pytest.mark.asyncio
async def test_the_clearest_approved_picture_wins_over_a_higher_ranked_one() -> None:
    """Among pictures that pass, the one clearest on the e-ink screen is shown, ties going to search rank."""
    web = _FakeWeb(
        [_result(1), _result(2), _result(3)],
        [{**SAFE, "clear_on_screen": 0.55}, {**SAFE, "clear_on_screen": 0.9}, {**SAFE, "clear_on_screen": 0.9}],
    )
    assert (await web.pictures().find("crocodile")).source == "site2.org"
