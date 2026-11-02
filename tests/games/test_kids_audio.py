"""Tests for the kids' games' recorded audio: lines with sounds, the fallback voice, the music bed, every line."""

import re
import json
import asyncio
from pathlib import Path
from collections.abc import Callable, AsyncIterator

import numpy as np
import pytest
import pytest_asyncio

from .fakes import FakeReachy, seen, frame
from reachy_mini_conversation_app.games import hunt, kids_audio
from reachy_mini_conversation_app.games.base import GameContext
from reachy_mini_conversation_app.games.host import GameHost, GameState
from reachy_mini_conversation_app.games.catalog import COLOR_HUNT
from reachy_mini_conversation_app.games.color_hunt import TARGETS, ColorHunt
from reachy_mini_conversation_app.games.kids_audio import RATE, KidsAudio, loopable
from reachy_mini_conversation_app.games.kids_lines import TAGS, LINES
from reachy_mini_conversation_app.games.simon_says import POSES
from reachy_mini_conversation_app.games.treasure_hunt import TREASURES
from reachy_mini_conversation_app.games.big_brain_audio import write_wav


def _tone(seconds: float, level: int = 1000) -> np.ndarray:
    return np.full(int(RATE * seconds), level, dtype=np.int16)


def _recorded(folder: Path, moments: dict[str, float], sounds: dict[str, float]) -> Path:
    """Write one Spanish clip per moment and one take per sound (their lengths in seconds)."""
    for moment, seconds in moments.items():
        write_wav(folder / "voice" / moment / "es-01.wav", _tone(seconds))
    for name, seconds in sounds.items():
        write_wav(folder / "sounds" / name / "take-1.wav", _tone(seconds))
    (folder / "sounds").mkdir(parents=True, exist_ok=True)
    (folder / "sounds" / "chosen.json").write_text(json.dumps(dict.fromkeys(sounds, "take-1.wav")))
    return folder


def _context(reachy: FakeReachy) -> GameContext:
    context = GameContext(info=COLOR_HUNT, language="es", services=reachy.services)
    context.running.set()
    return context


async def _until(predicate: Callable[[], bool], timeout_s: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "condition did not become true"
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_a_line_plays_with_its_sounds_and_without_tags(tmp_path: Path) -> None:
    """Sounds, the recorded take and the after-sounds play as one line; the text has no audio tags."""
    folder = _recorded(tmp_path, {"colors_found_red": 1.0}, {"found": 0.5, "star": 0.25})
    reachy = FakeReachy(recorded=True)
    audio = KidsAudio(folder, LINES)
    await audio.say(_context(reachy), "colors_found_red", sounds=("found",), after=("star",))
    assert reachy.clips == [
        ("¡Sí! ¡Rojo! ¡Qué buen rojo!", int(RATE * 0.5) + int(RATE * 1.0) + int(RATE * 0.25) + 2 * int(RATE * 0.15))
    ]
    assert reachy.said == []


@pytest.mark.asyncio
async def test_without_a_clip_richie_says_it_in_his_own_voice(tmp_path: Path) -> None:
    """A moment not recorded yet (or no recorded-line player) is said in his everyday voice, tags stripped."""
    reachy = FakeReachy(recorded=True)
    audio = KidsAudio(tmp_path, LINES)
    await audio.say(_context(reachy), "round_first", "colors_find_blue", sounds=("round",))
    assert reachy.said == ["¡Primera ronda!", "¿Quién me trae algo... ¡AZUL!"] and reachy.clips == []


@pytest.mark.asyncio
async def test_play_again_is_recorded_and_a_yes_plays_on(tmp_path: Path) -> None:
    """Ask "¿Jugamos otra vez?" as a recorded line that listens; "¡sí!" is a yes, silence a no."""
    folder = _recorded(tmp_path, {"again": 1.0}, {})
    reachy = FakeReachy(recorded=True)
    audio = KidsAudio(folder, LINES)
    context = _context(reachy)
    asking = asyncio.create_task(audio.ask_yes(context, "again", 1.0))
    await _until(lambda: bool(reachy.clip_answers))
    await reachy.clip_answers[-1]("¡Sí!")
    assert await asking is True
    assert await audio.ask_yes(context, "again", 0.05) is False


def test_sounds_are_levelled_by_loudness_without_clipping() -> None:
    """A quiet take is raised to its level and a loud one lowered; a sharp peak holds the gain back."""
    quiet, loud = kids_audio.leveled(_tone(1.0, 300), -24.0), kids_audio.leveled(_tone(1.0, 20000), -24.0)
    for samples in (quiet, loud):
        assert abs(20 * np.log10(np.sqrt(np.mean(samples.astype(np.float32) ** 2)) / 32768) + 24.0) < 0.1
    spiky = np.concatenate([_tone(1.0, 100), np.array([30000], dtype=np.int16)])
    assert int(np.abs(kids_audio.leveled(spiky, -6.0)).max()) <= int(32768 * 10 ** (kids_audio.PEAK_DB / 20)) + 1


def test_music_loops_without_a_jump() -> None:
    """The last LOOP_FADE_S blends into the first, so the loop is that much shorter and starts mid-blend."""
    samples = np.concatenate([_tone(1.0, 1000), _tone(1.0, -1000)])
    looped = loopable(samples)
    fade = int(RATE * kids_audio.LOOP_FADE_S)
    assert len(looped) == len(samples) - fade
    assert looped[0] == -1000 and looped[fade - 1] > 900


@pytest.mark.asyncio
async def test_music_plays_a_little_ahead_and_waits_while_he_speaks(tmp_path: Path) -> None:
    """Pieces go out at most AHEAD_S ahead; refused pieces (he is speaking) are not counted as played."""
    folder = _recorded(tmp_path, {}, {"hunt": 3.0})
    reachy = FakeReachy(recorded=True)
    audio = KidsAudio(folder, LINES)
    context = _context(reachy)
    stop = asyncio.Event()
    playing = asyncio.create_task(audio.play_music(context, lambda _: "hunt", stop.is_set))
    await asyncio.sleep(0.3)
    ahead = len(reachy.audio)
    assert 1 <= ahead <= int(kids_audio.AHEAD_S / kids_audio.CHUNK_S) + 1  # played_s does not move here
    assert all(len(piece) == int(RATE * kids_audio.CHUNK_S) for piece in reachy.audio)
    level = 20 * np.log10(np.sqrt(np.mean(reachy.audio[0].astype(np.float32) ** 2)) / 32768)
    assert abs(level - kids_audio.MUSIC_DB) < 0.1
    stop.set()
    await asyncio.wait_for(playing, 1.0)


@pytest.mark.asyncio
async def test_missing_music_plays_nothing(tmp_path: Path) -> None:
    """Without the music, the bed returns at once."""
    reachy = FakeReachy(recorded=True)
    await asyncio.wait_for(KidsAudio(tmp_path, LINES).play_music(_context(reachy), lambda _: "hunt", lambda: False), 1)
    assert reachy.audio == []


@pytest_asyncio.fixture
async def recorded_hunt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[FakeReachy, GameHost]]:
    """Run a one-round color hunt for red with every moment it uses recorded, the music and the sounds."""
    monkeypatch.setattr(hunt, "ROUNDS", 1)
    monkeypatch.setattr(ColorHunt, "round_s", 0.8)
    monkeypatch.setattr(hunt, "CHECK_EVERY_S", 0.02)
    monkeypatch.setattr(hunt, "RECHECK_STILL_S", 0.1)
    monkeypatch.setattr(hunt, "OTHER_ROUND_ANSWER_S", 0.3)
    monkeypatch.setattr(ColorHunt, "targets", lambda self: ["red"])
    moments = ["colors_start", "round_first", "colors_find_red", "colors_found_red", "stars_1", "again", "thanks"]
    sounds = ["intro_colors", "round", "found", "star", "celebrate", "hunt", "hunt_hurry"]
    folder = _recorded(tmp_path, dict.fromkeys(moments, 0.2), dict.fromkeys(sounds, 0.3))
    reachy = FakeReachy(real_clock=True, recorded=True)
    for seed in range(400):
        reachy.frames.append(frame(seed))
    audio = KidsAudio(folder, LINES)
    host = GameHost(reachy.services, {"colors": lambda: ColorHunt(audio=audio)})
    yield reachy, host
    host.end("test over", say=False)
    await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_a_recorded_color_hunt(recorded_hunt) -> None:
    """The jingle and opening, the round's intro with the color, music while looking, the find, the stars."""
    reachy, host = recorded_hunt
    reachy.jev_answer = lambda: seen("red") if reachy.jev_calls > 1 else seen("none", held=0.05)
    host.start("colors", "es")
    await _until(lambda: bool(reachy.clip_answers))
    texts = [text for text, _ in reachy.clips]
    assert texts[0] == "¡Vamos a jugar a los colores! Yo digo un color... y tú me traes algo de ese color."
    assert texts[1] == "¡Primera ronda! ¿Quién me trae algo... ¡ROJO!"
    assert texts[2] == "¡Sí! ¡Rojo! ¡Qué buen rojo!" and texts[3] == "¡Ganaste una estrella!"
    assert texts[4] == "¿Jugamos otra vez?" and reachy.said == []
    assert reachy.audio and all(len(piece) == int(RATE * kids_audio.CHUNK_S) for piece in reachy.audio)
    await reachy.clip_answers[-1]("no")
    await _until(lambda: host.state is GameState.IDLE)
    assert reachy.clips[-1][0] == "¡Gracias por jugar conmigo!"


def test_every_line_the_games_say_is_written() -> None:
    """Each color, thing and pose has its moments, in both languages, and the stars go up to eight."""
    wanted = ["colors_start", "treasure_start", "simon_start", "simon_praise", "simon_like_this", "again", "thanks"]
    wanted += ["round_first", "round_next", "round_last", "nudge", "hurry", "time_up", "wrong"]
    wanted += ["simon_step_back", "simon_come_closer", *(f"stars_{count}" for count in range(9))]
    for color in TARGETS:
        wanted += [f"colors_{kind}_{color}" for kind in ("find", "again", "found", "seen")]
    for item in TREASURES:
        wanted += [f"treasure_{kind}_{item}" for kind in ("find", "again", "found")]
    wanted += [f"simon_{pose}" for pose in POSES]
    for moment in wanted:
        assert set(LINES[moment]) == {"es", "en"} and all(LINES[moment].values()), moment


def test_lines_use_square_bracket_tags_from_the_list() -> None:
    """Tags are in square brackets and from TAGS; no parentheses or braces (ElevenLabs reads those aloud)."""
    for moment, takes in LINES.items():
        for line in (line for language in takes.values() for line in language):
            assert not re.search(r"[(){}]", line), (moment, line)
            assert set(re.findall(r"\[([^\]]+)\]", line)) <= TAGS, (moment, line)
