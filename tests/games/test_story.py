"""Tests for story mode: story files, asking for a story, and telling one with the children joining in."""

import io
import random
import asyncio
from pathlib import Path
from collections.abc import Callable

import numpy as np
import pytest

from .fakes import FakeReachy
from reachy_mini_conversation_app.games import story as story_mode
from reachy_mini_conversation_app.games.host import GameHost, host_phrases
from reachy_mini_conversation_app.games.story import (
    OFFER,
    StoryGame,
    StoryVoice,
    picked,
    again_answer,
    offer_answer,
)
from reachy_mini_conversation_app.games.catalog import STORIES, GameInfo, parse_game_request
from reachy_mini_conversation_app.games.kids_audio import KidsAudio
from reachy_mini_conversation_app.games.story_file import (
    StoryError,
    clip_id,
    load_cast,
    load_story,
    load_common,
    load_stories,
)
from reachy_mini_conversation_app.games.big_brain_audio import write_wav


CAST = """
narrator: {voice: "narr"}
wolf: {voice: "wolf", look: "a goofy wolf with a striped scarf"}
pig: {voice: "", look: "a little pig with a straw hat"}
"""
SOUNDS = """
sound:
  knock: {seconds: 1, prompt: a knock}
  whoosh: {seconds: 1, prompt: a whoosh}
music:
  bed: {seconds: 5, prompt: music}
"""
COMMON = """
again: {es: "¿Otro cuento?", it: "Un'altra?"}
goodbye: {es: "¡Chao!", it: "Ciao!"}
screen_nap: {es: "¡Se durmió la pantalla!", it: "Lo schermo dorme!"}
offer_story_a: {es: "¿El cuento A...", it: "La storia A..."}
or_story_a: {es: "...¿o el cuento A?", it: "...o la storia A?"}
offer_story_b: {es: "¿El cuento B...", it: "La storia B..."}
or_story_b: {es: "...¿o el cuento B?", it: "...o la storia B?"}
"""
STORY = """
key: test_story
title: {es: El cuento de prueba, it: La storia di prova}
names: {es: [cuento de prueba], it: [storia di prova]}
pitch: {es: "Uno de prueba.", it: "Una di prova."}
cover: {id: cover, picture: a test cover, with: [wolf, pig]}
steps:
  - scene: {id: forest, picture: a forest, with: [pig], move: happy}
  - say: narrator
    es: "[warm] Había una vez."
    it: "[warm] C'era una volta."
  - sound: whoosh
  - noise:
      ask: {es: "¡Soplen!", it: "Soffiate!"}
      louder: {es: "¡Más fuerte!", it: "Più forte!"}
      done: {es: "¡Qué fuerte!", it: "Fortissimo!"}
      without: {who: wolf, es: "¡Fuuu!", it: "Fuuu!"}
      sound: whoosh
  - chorus:
      ask: {es: "¡No, no y no!", it: "No, no e poi no!"}
      done: {who: pig, es: "¡Eso!", it: "Esatto!"}
      without: {who: pig, es: "¡No te abro!", it: "Non ti apro!"}
  - pat:
      ask: {es: "¡Toquen!", it: "Bussate!"}
      done: {es: "¡Toc toc, bien!", it: "Toc toc, bravi!"}
      without: {es: "Tocó el lobo.", it: "Bussò il lupo."}
      sound: knock
  - copy:
      ask: {es: "¡Tiemblen!", it: "Tremate!"}
      done: {es: "¡Qué temblorosos!", it: "Che tremarella!"}
      move: scared
      music: bed
      seconds: 0.1
  - choice:
      id: color
      ask: {es: "¿Roja o azul?", it: "Rossa o blu?"}
      options:
        red:
          words: {es: [roja, rojo], it: [rossa]}
          picked: {es: "Yo digo roja.", it: "Dico rossa."}
          then: {es: "¡Roja!", it: "Rossa!"}
        blue:
          words: {es: [azul], it: [blu]}
          picked: {es: "Yo digo azul.", it: "Dico blu."}
          then: {es: "¡Azul!", it: "Blu!"}
  - scene:
      id: house
      about: color
      with: [pig]
      options: {red: a red house, blue: a blue house}
  - say: narrator
    about: color
    options:
      red: {es: "La casa roja.", it: "La casa rossa."}
      blue: {es: "La casa azul.", it: "La casa blu."}
  - music: {name: bed, seconds: 0.1}
  - say: narrator
    es: "Colorín colorado."
    it: "Fine."
"""


@pytest.fixture(autouse=True)
def quick_story(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the waits short."""
    monkeypatch.setattr(story_mode, "NOISE_LISTEN_S", 0.1)
    monkeypatch.setattr(story_mode, "CHOICE_LISTEN_S", 0.1)
    monkeypatch.setattr(story_mode, "AGAIN_LISTEN_S", 0.1)
    monkeypatch.setattr(story_mode, "ANSWER_MARGIN_S", 0.3)
    monkeypatch.setattr(story_mode, "PAT_WAIT_S", 0.3)
    monkeypatch.setattr(story_mode, "MIN_SCENE_GAP_S", 0.0)


def _folder(tmp_path: Path, story: str = STORY, cast: str = CAST) -> Path:
    folder = tmp_path / "stories"
    (folder / "test_story").mkdir(parents=True)
    (folder / "cast.yaml").write_text(cast, encoding="utf-8")
    (folder / "sounds.yaml").write_text(SOUNDS, encoding="utf-8")
    (folder / "common.yaml").write_text(COMMON, encoding="utf-8")
    (folder / "test_story" / "story.yaml").write_text(story, encoding="utf-8")
    return folder


def _info() -> GameInfo:
    return GameInfo(
        key="test_story",
        names=frozenset({"cuento de prueba", "storia di prova"}),
        title={"es": "El cuento de prueba", "it": "La storia di prova", "en": "El cuento de prueba"},
        pitch={"es": "Uno de prueba.", "it": "Una di prova.", "en": "Uno de prueba."},
        rules="A test story.",
        kind="story",
        languages=("es", "it"),
    )


def _game(folder: Path, data: Path, *, record: bool) -> Callable[[], StoryGame]:
    """Return a factory for the test story; ``record`` writes a clip for every line first."""
    cast, common = load_cast(folder), load_common(folder)
    story = load_story(folder / "test_story" / "story.yaml", folder)
    voice, common_voice = StoryVoice(data / "test_story", cast), StoryVoice(data / "common", cast)
    if record:
        tone = (np.sin(np.arange(2400) / 5) * 8000).astype(np.int16)
        for language in ("es", "it"):
            for line in story.lines():
                write_wav(voice.path(line, language), tone)
            for line in common.lines.values():
                write_wav(common_voice.path(line, language), tone)
        for picture in story.pictures():
            screen = data / "test_story" / "screens" / f"{picture.id}.png"
            screen.parent.mkdir(parents=True, exist_ok=True)
            screen.write_bytes(f"screen {picture.id}".encode())
    sounds = KidsAudio(data, {})
    return lambda: StoryGame(story, _info(), voice, common, common_voice, sounds)


async def _until(predicate: Callable[[], bool], timeout_s: float = 6.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "condition did not become true"
        await asyncio.sleep(0.01)


async def _answer_clips(reachy: FakeReachy, answers: dict[str, str]) -> None:
    """Answer each recorded question whose text is in ``answers``; leave the rest unanswered (silence)."""
    done = 0
    while True:
        while done < len(reachy.clip_answers):
            answer = reachy.clip_answers[done]
            done += 1
            # The game waits for each answer, so the question is the last clip played.
            await answer(answers.get(reachy.clips[-1][0], ""))
        await asyncio.sleep(0.005)


# Story files ---------------------------------------------------------------------------------------


SHIPPED = (
    "tres_cerditos",
    "nabo_gigante",
    "ricitos_de_oro",
    "musicos_de_bremen",
    "la_gran_carrera",
    "donde_esta_bigotes",
    "la_befana",
    "el_cascanueces",
    "la_monedita_de_plata",
)


@pytest.mark.parametrize("key", SHIPPED)
def test_the_shipped_stories_read(key: str) -> None:
    """Each shipped story reads, with every line in both languages, every sound known and every kind of moment."""
    stories = load_stories()
    assert key in stories
    story = stories[key]
    lines = list(story.lines())
    assert len(lines) > 50
    assert all(line.tagged("es") and line.tagged("it") for line in lines)
    kinds = {step.kind for step in story.steps}
    assert len({"noise", "chorus", "pat", "copy", "choice"} & kinds) >= 4
    assert story.cover is not None and len(list(story.pictures())) >= 10
    assert set(load_common().lines) >= {"again", "goodbye"}
    assert key in STORIES


def test_a_story_file_says_what_is_wrong(tmp_path: Path) -> None:
    """Missing text, an unknown role or sound, and a choice line before its choice are refused by name."""
    cases = {
        "no it": STORY.replace('it: "[warm] C\'era una volta."', ""),
        "not in cast.yaml": STORY.replace("who: wolf", "who: dragon"),
        "not in sounds.yaml": STORY.replace("sound: whoosh\n  - noise", "sound: thunder\n  - noise"),
        "has no look": STORY.replace("with: [pig], move: happy", "with: [narrator], move: happy"),
        "options must be": STORY.replace(
            "options: {red: a red house, blue: a blue house}", "options: {red: a red house}"
        ),
        "share an id": STORY.replace("id: forest", "id: cover"),
        "needs exactly one": STORY.replace(
            "  - music: {name: bed, seconds: 0.1}", "  - music: {name: bed}\n    move: happy"
        ),
    }
    for expected, text in cases.items():
        folder = _folder(tmp_path / expected.replace(" ", "_"), text)
        with pytest.raises(StoryError, match=expected):
            load_story(folder / "test_story" / "story.yaml", folder)
    early = STORY.replace("  - scene: {id: forest, picture: a forest, with: [pig], move: happy}", "  - say: narrator\n    about: color\n    options:\n      red: {es: a, it: b}")  # fmt: skip
    folder = _folder(tmp_path / "early", early)
    with pytest.raises(StoryError, match="not asked before"):
        load_story(folder / "test_story" / "story.yaml", folder)


def test_a_clip_is_named_by_its_voice_language_and_text() -> None:
    """A new voice or a changed line gets a new recording; the same line keeps its own."""
    same = clip_id("v1", "es", "[warm] Hola")
    assert same == clip_id("v1", "es", "[warm] Hola ")
    assert len({same, clip_id("v2", "es", "[warm] Hola"), clip_id("v1", "it", "[warm] Hola"), clip_id("v1", "es", "Hola")}) == 4  # fmt: skip


# Asking for a story --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("said", "key", "language", "which"),
    [
        ("Cuéntanos los tres cerditos", "tres_cerditos", "es", False),
        ("cuéntanos el cuento de los tres cerditos", "tres_cerditos", "es", False),
        ("Cuéntame un cuento", None, "es", True),
        ("Raccontaci i tre porcellini", "tres_cerditos", "it", False),
        ("vogliamo la storia dei tre porcellini", "tres_cerditos", "it", False),
        ("raccontami una storia", None, "it", True),
        ("i tre porcellini", "tres_cerditos", "it", False),
        ("los tres cerditos", "tres_cerditos", "es", False),
        ("cuéntanos el nabo gigante", "nabo_gigante", "es", False),
        ("raccontaci la rapa gigante", "nabo_gigante", "it", False),
        ("cuéntanos ricitos de oro", "ricitos_de_oro", "es", False),
        ("cuéntanos el cuento de los tres osos", "ricitos_de_oro", "es", False),
        ("raccontaci riccioli d'oro", "ricitos_de_oro", "it", False),
        ("cuéntanos los músicos de Bremen", "musicos_de_bremen", "es", False),
        ("raccontaci i musicanti di Brema", "musicos_de_bremen", "it", False),
        ("cuéntanos la gran carrera", "la_gran_carrera", "es", False),
        ("cuéntanos el cuento de los carros", "la_gran_carrera", "es", False),
        ("raccontaci la grande corsa", "la_gran_carrera", "it", False),
        ("cuéntanos dónde está bigotes", "donde_esta_bigotes", "es", False),
        ("cuéntanos el cuento del gato perdido", "donde_esta_bigotes", "es", False),
        ("raccontaci dov'è baffo", "donde_esta_bigotes", "it", False),
        ("cuéntanos la monedita de plata", "la_monedita_de_plata", "es", False),
        ("raccontaci la monetina d'argento", "la_monedita_de_plata", "it", False),
        ("cuéntanos la befana", "la_befana", "es", False),
        ("raccontaci la storia della befana", "la_befana", "it", False),
        ("cuéntanos el cascanueces", "el_cascanueces", "es", False),
        ("raccontaci lo schiaccianoci", "el_cascanueces", "it", False),
        # By what it is about, the way Enzo asks (2026-11-08/09).
        ("la historia de los carros", "la_gran_carrera", "es", False),
        ("El de los carros, por favor. El de los carros", "la_gran_carrera", "es", False),
        ("puedes contar la historia de los carros", "la_gran_carrera", "es", False),
        ("cuéntanos el de los ratones", "el_cascanueces", "es", False),
        ("raccontaci la storia delle macchinine", "la_gran_carrera", "it", False),
    ],
)
def test_asking_for_a_story(said: str, key: str | None, language: str, which: bool) -> None:
    """A story's name in either language tells it in that language; "un cuento" alone asks which."""
    request = parse_game_request(said)
    assert request is not None
    assert (request.game.key if request.game else None, request.language, request.story) == (key, language, which)


@pytest.mark.parametrize("said", ["quiero agua", "cuéntame un chiste", "voglio un caffè", "juguemos a los colores"])
def test_other_requests_are_not_stories(said: str) -> None:
    """Everyday requests that look a little like it are not stories."""
    request = parse_game_request(said)
    assert request is None or not request.story and (request.game is None or request.game.kind != "story")


def test_the_answer_to_another_story() -> None:
    """A yes offers two others, "otra vez" tells it again, a story's name or keyword tells that one, else stop."""
    assert again_answer("¡sí, otro!", "tres_cerditos") == OFFER
    assert again_answer("¡sí, otra vez!", "tres_cerditos") == "tres_cerditos"
    assert again_answer("ancora!", "tres_cerditos") == "tres_cerditos"
    assert again_answer("los tres cerditos", "other") == "tres_cerditos"
    # 2026-11-10: this told Los tres cerditos again.
    assert again_answer("Sí, otro cuento, el de carros", "tres_cerditos") == "la_gran_carrera"
    assert again_answer("no, ya no", "tres_cerditos") is None
    assert again_answer("", "tres_cerditos") is None


def test_the_answer_to_the_two_offered() -> None:
    """A name, the first or second, "otra vez" or a no; anything else takes the first."""
    offered = ["ricitos_de_oro", "la_gran_carrera"]
    assert offer_answer("¡el de los carros!", offered, "tres_cerditos") == "la_gran_carrera"
    assert offer_answer("el segundo", offered, "tres_cerditos") == "la_gran_carrera"
    assert offer_answer("il primo", offered, "tres_cerditos") == "ricitos_de_oro"
    assert offer_answer("¡sí!", offered, "tres_cerditos") == "ricitos_de_oro"
    assert offer_answer("", offered, "tres_cerditos") == "ricitos_de_oro"
    assert offer_answer("no, otra vez el mismo", offered, "tres_cerditos") == "tres_cerditos"
    assert offer_answer("no, ya no", offered, "tres_cerditos") is None


def test_the_stories_offered_are_the_ones_heard_least_lately(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never the one just told; untold ones before those told today."""
    monkeypatch.setattr(story_mode, "_TOLD", {"a": 1.0, "b": 2.0, "c": 3.0})
    assert story_mode.stories_to_offer("c", random.Random(0), ("a", "b", "c", "d")) == ["d", "a"]


def test_every_story_can_be_offered() -> None:
    """Each shipped story has its two offer lines in common.yaml."""
    lines = load_common().lines
    assert all(f"offer_{key}" in lines and f"or_{key}" in lines for key in load_stories())


def test_story_keywords_point_to_one_story_each() -> None:
    """A keyword shared by two stories would pick neither."""
    stories = load_stories().values()
    for story in stories:
        assert story.keywords, f"{story.key} has no keywords"
        for other in stories:
            if other.key != story.key:
                assert not story.keywords & other.keywords, (story.key, other.key, story.keywords & other.keywords)


def test_a_choice_needs_one_option_named(tmp_path: Path) -> None:
    """One option named anywhere picks it; none, both or silence pick nothing."""
    folder = _folder(tmp_path)
    story = load_story(folder / "test_story" / "story.yaml", folder)
    options = next(step for step in story.steps if step.kind == "choice").options
    assert picked("¡La roja!", options, "es").key == "red"  # type: ignore[union-attr]
    assert picked("blu", options, "it").key == "blue"  # type: ignore[union-attr]
    assert picked("roja y azul", options, "es") is None
    assert picked("el lobo", options, "es") is None
    assert picked(None, options, "es") is None


def test_the_host_asks_about_stories_in_italian() -> None:
    """The host's lines exist in Italian and stories are asked about as stories."""
    lines = host_phrases()
    assert "Pausa. Continuiamo o finiamo?" in lines
    assert any(line.startswith("Vi racconto I tre porcellini?") for line in lines)
    assert any(line.startswith("¿Les cuento Los tres cerditos?") for line in lines)


# Telling a story -----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_story_with_the_children_joining_in(tmp_path: Path) -> None:
    """They blow, shout the line, knock and pick: each gets its "done" line, and the choice is remembered."""
    reachy = FakeReachy(real_clock=True, recorded=True)
    factory = _game(_folder(tmp_path), tmp_path / "data", record=True)
    host = GameHost(reachy.services, {"test_story": factory})
    answers = {"¡Soplen!": "fuuuu", "¡No, no y no!": "no no y no", "¿Roja o azul?": "¡la roja!", "¿Otro cuento?": "no"}
    answering = asyncio.create_task(_answer_clips(reachy, answers))

    async def knock() -> None:
        await _until(lambda: any(text == "¡Toquen!" for text, _ in reachy.clips))
        reachy.pats += 1

    knocking = asyncio.create_task(knock())
    assert host.start("test_story", "es") is None
    await _until(lambda: host.state.value == "idle", 8.0)
    answering.cancel()
    knocking.cancel()
    said = [text for text, _ in reachy.clips]
    for line in ("Había una vez.", "¡Qué fuerte!", "¡Eso!", "¡Toc toc, bien!", "¡Qué temblorosos!", "¡Roja!", "La casa roja.", "¡Chao!"):  # fmt: skip
        assert line in said, line
    for line in ("¡Más fuerte!", "¡Fuuu!", "¡No te abro!", "Tocó el lobo.", "Yo digo roja.", "Yo digo azul."):
        assert line not in said, line
    # Noise and chorus stay on the local speech model; the choice and "¿Otro cuento?" may use the cloud one.
    assert reachy.clip_cloud[:2] == [False, False]
    assert reachy.clip_cloud[2:] == [True, True]
    assert ["roja", "rojo", "azul"] in reachy.hints
    assert "scared" in reachy.emotions
    # Every picture shows (no gap here), the house as the children picked it; nothing is drawn while playing.
    assert reachy.screens[:3] == [b"screen cover", b"screen forest", b"screen house-red"]
    assert reachy.drawn == []
    assert reachy.restored == 1


@pytest.mark.asyncio
async def test_a_story_nobody_joins_in_still_ends(tmp_path: Path) -> None:
    """Silence everywhere: one "¡Más fuerte!", the story does it itself, Richie picks, and it says goodbye."""
    reachy = FakeReachy(real_clock=True, recorded=True)
    factory = _game(_folder(tmp_path), tmp_path / "data", record=True)
    host = GameHost(reachy.services, {"test_story": factory})
    answering = asyncio.create_task(_answer_clips(reachy, {}))
    assert host.start("test_story", "it") is None
    await _until(lambda: host.state.value == "idle", 8.0)
    answering.cancel()
    said = [text for text, _ in reachy.clips]
    assert said.count("Più forte!") == 1
    for line in ("Fuuu!", "Non ti apro!", "Bussò il lupo.", "Ciao!"):
        assert line in said, line
    chosen = "rossa" if "Dico rossa." in said else "blu"
    assert ("Dico rossa." in said) != ("Dico blu." in said)
    assert f"La casa {chosen}." in said


@pytest.mark.asyncio
async def test_a_story_without_recordings_is_said_in_richies_voice(tmp_path: Path) -> None:
    """No clips yet: every line is said through the reply voice and questions are asked aloud."""
    reachy = FakeReachy(real_clock=True)
    factory = _game(_folder(tmp_path), tmp_path / "data", record=False)
    host = GameHost(reachy.services, {"test_story": factory})

    async def silence() -> None:
        answered = 0
        while True:
            while answered < len(reachy.asked):
                await reachy.asked[answered][1]("")
                answered += 1
            await asyncio.sleep(0.005)

    answering = asyncio.create_task(silence())
    assert host.start("test_story", "es") is None
    await _until(lambda: host.state.value == "idle", 8.0)
    answering.cancel()
    assert "Había una vez." in reachy.said  # tags are not said
    assert "¡Soplen!" in reachy.said and "¡Chao!" in reachy.said


@pytest.mark.asyncio
async def test_yes_to_another_story_offers_two_and_tells_the_one_picked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plain yes offers two other stories by name; "el segundo" tells the second."""
    monkeypatch.setattr(story_mode, "STORIES", ("test_story", "story_a", "story_b"))
    monkeypatch.setattr(story_mode, "_TOLD", {"story_a": 1.0})  # A was told lately, so B is offered first
    reachy = FakeReachy(real_clock=True, recorded=True)
    factory = _game(_folder(tmp_path), tmp_path / "data", record=True)
    host = GameHost(reachy.services, {"test_story": factory, "story_a": factory, "story_b": factory})
    answering = asyncio.create_task(
        _answer_clips(reachy, {"¿Otro cuento?": "¡sí!", "...¿o el cuento A?": "el segundo, ese"})
    )
    assert host.start("test_story", "es") is None
    await _until(lambda: [text for text, _ in reachy.clips].count("Había una vez.") == 2, 8.0)
    host.end("test", say=False)
    answering.cancel()
    played = [text for text, _ in reachy.clips]
    assert played.index("¿El cuento B...") < played.index("...¿o el cuento A?")


@pytest.mark.asyncio
async def test_a_napping_screen_gets_one_request_for_its_button(tmp_path: Path) -> None:
    """The screen was awake, then stopped checking in: Richie asks once for its back button, not every step."""
    reachy = FakeReachy(real_clock=True, recorded=True)
    asleep = iter([0.0, 0.0])
    reachy.services.screen_asleep_s = lambda: next(asleep, 120.0)
    factory = _game(_folder(tmp_path), tmp_path / "data", record=True)
    host = GameHost(reachy.services, {"test_story": factory})
    answering = asyncio.create_task(_answer_clips(reachy, {"¿Otro cuento?": "no"}))
    assert host.start("test_story", "es") is None
    await _until(lambda: host.state.value == "idle", 8.0)
    answering.cancel()
    assert [text for text, _ in reachy.clips].count("¡Se durmió la pantalla!") == 1


@pytest.mark.asyncio
async def test_a_screen_that_never_woke_is_not_asked_about(tmp_path: Path) -> None:
    """No screen in use (never checked in during the story): nothing is said about it."""
    reachy = FakeReachy(real_clock=True, recorded=True)
    reachy.services.screen_asleep_s = lambda: float("inf")
    factory = _game(_folder(tmp_path), tmp_path / "data", record=True)
    host = GameHost(reachy.services, {"test_story": factory})
    answering = asyncio.create_task(_answer_clips(reachy, {"¿Otro cuento?": "no"}))
    assert host.start("test_story", "es") is None
    await _until(lambda: host.state.value == "idle", 8.0)
    answering.cancel()
    assert "¡Se durmió la pantalla!" not in [text for text, _ in reachy.clips]


@pytest.mark.asyncio
async def test_yes_to_another_story_tells_it_again(tmp_path: Path) -> None:
    """Start the same story again when "¿Otro cuento?" is answered "otra vez"."""
    reachy = FakeReachy(real_clock=True, recorded=True)
    factory = _game(_folder(tmp_path), tmp_path / "data", record=True)
    host = GameHost(reachy.services, {"test_story": factory})
    answering = asyncio.create_task(_answer_clips(reachy, {"¿Otro cuento?": "¡sí, otra vez!"}))
    assert host.start("test_story", "es") is None
    await _until(lambda: [text for text, _ in reachy.clips].count("Había una vez.") == 2, 8.0)
    host.end("test", say=False)
    answering.cancel()


@pytest.mark.asyncio
async def test_a_choices_picture_is_up_before_richie_says_what_happened(tmp_path: Path) -> None:
    """The red house shows as soon as red is picked, before "¡Roja!", and is not put up a second time."""
    reachy = FakeReachy(real_clock=True, recorded=True)
    factory = _game(_folder(tmp_path), tmp_path / "data", record=True)
    host = GameHost(reachy.services, {"test_story": factory})
    screens_by_line: dict[str, list[bytes]] = {}

    class Clips(list):  # what was on screen as each line played
        def append(self, item: tuple[str, int]) -> None:
            screens_by_line[item[0]] = list(reachy.screens)
            super().append(item)

    reachy.clips = Clips()
    answering = asyncio.create_task(_answer_clips(reachy, {"¿Roja o azul?": "roja", "¿Otro cuento?": "no"}))
    assert host.start("test_story", "es") is None
    await _until(lambda: host.state.value == "idle", 8.0)
    answering.cancel()
    assert screens_by_line["¡Roja!"][-1] == b"screen house-red"
    assert reachy.screens.count(b"screen house-red") == 1


@pytest.mark.asyncio
async def test_pictures_wait_for_the_gap_so_the_screen_does_not_flash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A picture that comes too soon after the last one waits; a newer one replaces it; the cover shows at once."""
    monkeypatch.setattr(story_mode, "MIN_SCENE_GAP_S", 60.0)
    reachy = FakeReachy(real_clock=True, recorded=True)
    factory = _game(_folder(tmp_path), tmp_path / "data", record=True)
    host = GameHost(reachy.services, {"test_story": factory})
    answering = asyncio.create_task(_answer_clips(reachy, {}))
    assert host.start("test_story", "es") is None
    await _until(lambda: host.state.value == "idle", 8.0)
    answering.cancel()
    assert [screen for screen in reachy.screens if screen.startswith(b"screen")] == [b"screen cover"]


def test_scene_prompts_restate_each_look_and_the_screen_is_full() -> None:
    """Each character in a scene is named with its look and kept as on its sheet; the screen is 800x480 in 2 bits."""
    from PIL import Image

    from reachy_mini_conversation_app.games.story_pictures import screen_png, scene_prompt, sheet_prompt

    looks = {"wolf": "a goofy wolf with a striped scarf", "pig": "a little pig with a straw hat"}
    prompt = scene_prompt("the wolf knocks", ["wolf", "pig"], looks)
    assert "Image 1" in prompt and "Image 2" in prompt and "striped scarf" in prompt and "straw hat" in prompt
    assert "Do not redesign" in prompt and "No text" in prompt
    assert "pure white background" in sheet_prompt("pig", looks["pig"])
    png = screen_png(Image.new("RGB", (1536, 1024), (200, 100, 50)))
    with Image.open(io.BytesIO(png)) as screen:
        assert screen.size == (800, 480)


def test_turnaround_scenes_keep_who_a_character_is_but_not_its_pose() -> None:
    """From a turnaround sheet a scene acts the moment out instead of copying one pose (2026-11-11)."""
    from reachy_mini_conversation_app.games.story_pictures import scene_prompt

    looks = {"clara": "Clara, a little girl"}
    free = scene_prompt("Clara looks up at the tree", ["clara"], looks, turnaround=True)
    assert "turnaround sheet" in free and "never copy a pose" in free
    assert "exactly as on their sheets" in scene_prompt("Clara looks up", ["clara"], looks)
    assert all(story.turnaround for story in load_stories().values())  # every story since 2026-11-11
