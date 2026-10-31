"""Tests for the game host: a yes before every start, pause and quit, and putting things back afterwards."""

import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from .fakes import FakeReachy, ticks
from reachy_mini_conversation_app.games import host as host_module
from reachy_mini_conversation_app.games.base import Game, GameContext
from reachy_mini_conversation_app.games.host import GameHost, GameState, host_phrases
from reachy_mini_conversation_app.games.catalog import COLOR_HUNT, GameRequest


class _Waiting(Game):
    """A game that shows its screen and plays until it is ended."""

    info = COLOR_HUNT

    def __init__(self) -> None:
        self.paused_count = 0
        self.heard_texts: list[str] = []

    async def play(self, context: GameContext) -> None:
        context.show(b"game screen")
        await asyncio.Event().wait()

    def heard(self, context: GameContext, text: str) -> bool:
        self.heard_texts.append(text)
        return text == "aqui"

    def paused(self, context: GameContext) -> None:
        self.paused_count += 1


@pytest_asyncio.fixture
async def room() -> AsyncIterator[tuple[FakeReachy, GameHost, list[_Waiting]]]:
    """Run a host over a pretend Reachy with one game that waits; return both and the games it made."""
    reachy = FakeReachy()
    made: list[_Waiting] = []

    def make() -> Game:
        made.append(_Waiting())
        return made[-1]

    host = GameHost(reachy.services, {"colors": make})
    yield reachy, host, made
    host.end("test over", say=False)
    await ticks(0.1)


async def _playing(reachy: FakeReachy, host: GameHost) -> None:
    host.start("colors", "es")
    await ticks(0.05)
    assert host.state is GameState.PLAYING


@pytest.mark.asyncio
async def test_a_game_asks_first_and_a_yes_starts_it(room) -> None:
    """Saying "Juguemos a los colores" asks with a one-line pitch; "sí" starts it, pauses music, takes the screen."""
    reachy, host, made = room
    reply = host.offer(GameRequest(COLOR_HUNT, "es"))
    assert reply.text == "¿Jugamos a los colores? Yo digo un color y tú me traes algo de ese color."
    assert host.state is GameState.ASKING and made == []
    assert reply.after is not None
    await reply.after()
    answer = reachy.expected[-1]
    await answer("¡Sí!")
    await ticks(0.05)
    assert host.state is GameState.PLAYING and len(made) == 1
    assert reachy.music_paused == 1
    assert reachy.screens[-1] == b"game screen"


@pytest.mark.asyncio
async def test_no_or_another_answer_starts_nothing(room) -> None:
    """Saying "No" gets "otro día"; an unrelated answer just drops the question."""
    reachy, host, made = room
    host.offer(GameRequest(COLOR_HUNT, "es"))
    reply = await host._start_answer("no")
    assert reply.text == "Vale, otro día." and host.state is GameState.IDLE
    host.offer(GameRequest(COLOR_HUNT, "en"))
    reply = await host._start_answer("what time is it")
    assert reply.text is None and host.state is GameState.IDLE and made == []


@pytest.mark.asyncio
async def test_which_game_is_asked_when_none_is_named_and_the_name_starts_it(room) -> None:
    """Saying "Juguemos" alone lists the games; answering with one's name starts it."""
    reachy, host, made = room
    reply = host.offer(GameRequest(None, "es"))
    assert reply.text == "¿A qué jugamos? Puedo jugar a los colores."
    await host._start_answer("a los colores")
    await ticks(0.05)
    assert host.state is GameState.PLAYING


@pytest.mark.asyncio
async def test_a_pat_says_yes_and_silence_drops_the_question(room) -> None:
    """The head is held still while he asks, so a pat counts; with no answer the question lapses."""
    reachy, host, made = room
    host.offer(GameRequest(COLOR_HUNT, "es"))
    await ticks(0.05)
    assert reachy.quiet == [True]
    reachy.pats += 1
    await ticks()
    assert host.state is GameState.PLAYING and reachy.quiet == [True, False]
    host.end("test", say=False)
    host.offer(GameRequest(COLOR_HUNT, "es"))
    reachy.now += host_module.ASK_TIMEOUT_S
    await ticks()
    assert host.state is GameState.IDLE


@pytest.mark.asyncio
async def test_pause_and_resume_words_heard_without_the_name(room) -> None:
    """Saying "Pausa" pauses (screen says so, the game stops its sound); "seguimos" plays on with its screen back."""
    reachy, host, made = room
    await _playing(reachy, host)
    assert host.heard("¡Pausa!")
    assert host.state is GameState.PAUSED and made[0].paused_count == 1
    assert reachy.screens[-1] != b"game screen"  # the PAUSA screen
    await ticks(0.05)
    assert "Pausa." in reachy.said
    assert host.heard("seguimos")
    assert host.state is GameState.PLAYING and reachy.screens[-1] == b"game screen"


@pytest.mark.asyncio
async def test_a_bare_para_is_left_alone_and_quit_words_end_the_game(room) -> None:
    """A shouted "¡para!" is not for the host; "se acabó el juego" ends it and puts the screen back."""
    reachy, host, made = room
    await _playing(reachy, host)
    assert not host.heard("para")
    assert host.state is GameState.PLAYING
    assert host.heard("aqui") and made[0].heard_texts[-1] == "aqui"  # the game's own word
    assert host.heard("se acabó el juego")
    await ticks(0.05)
    assert host.state is GameState.IDLE and reachy.restored == 1
    assert reachy.said[-1] == "Vale, se acabó el juego."


@pytest.mark.asyncio
async def test_his_name_alone_pauses_and_asks_whether_to_go_on(room) -> None:
    """Saying "Richie" (or an antenna) pauses: "¿Seguimos o terminamos?"; "seguimos" resumes, "terminamos" ends."""
    reachy, host, made = room
    await _playing(reachy, host)
    reply = await host.addressed("")
    assert reply is not None and reply.text == "Pausa. ¿Seguimos o terminamos?"
    assert host.state is GameState.PAUSED
    await reply.after()
    resumed = await reachy.expected[-1]("seguimos")
    assert resumed.text == "¡Seguimos!" and host.state is GameState.PLAYING
    reply = await host.addressed("")
    await reply.after()
    ended = await reachy.expected[-1]("terminamos")
    assert ended.text == "Vale, se acabó el juego." and host.state is GameState.IDLE


@pytest.mark.asyncio
async def test_another_request_offers_to_end_the_game_then_carries_it_out(room) -> None:
    """Saying "Richie, enciende la luz" mid-game: "¿Lo terminamos?"; yes ends it and hands the request on."""
    reachy, host, made = room
    await _playing(reachy, host)
    reply = await host.addressed("enciende la luz")
    assert reply is not None and reply.text == "Estamos jugando a los colores. ¿Lo terminamos?"
    await reply.after()
    await reachy.expected[-1]("sí")
    assert host.state is GameState.IDLE and reachy.handed == ["enciende la luz"]


@pytest.mark.asyncio
async def test_no_or_silence_to_ending_plays_on(room) -> None:
    """Saying "No" resumes at once; with no answer the game resumes on its own."""
    reachy, host, made = room
    await _playing(reachy, host)
    reply = await host.addressed("pon música")
    await reply.after()
    resumed = await reachy.expected[-1]("no")
    assert resumed.text == "¡Seguimos!" and host.state is GameState.PLAYING
    await host.addressed("pon música")
    reachy.now += host_module.END_QUESTION_RESUME_S
    await ticks()
    assert host.state is GameState.PLAYING and reachy.handed == []


@pytest.mark.asyncio
async def test_quit_and_pause_said_with_the_name(room) -> None:
    """Saying "Richie, se acabó el juego" ends it; "Richie, pausa" pauses with a short "Pausa."."""
    reachy, host, made = room
    await _playing(reachy, host)
    paused = await host.addressed("pausa")
    assert paused.text == "Pausa." and host.state is GameState.PAUSED
    ended = await host.addressed("se acabó el juego")
    assert ended.text == "Vale, se acabó el juego." and host.state is GameState.IDLE
    assert await host.addressed("enciende la luz") is None  # no game: the request goes its usual way


@pytest.mark.asyncio
async def test_a_pat_resumes_a_paused_game(room) -> None:
    """While paused the head is held still, so a pat plays on."""
    reachy, host, made = room
    await _playing(reachy, host)
    host.pause()
    await ticks(0.05)
    reachy.pats += 1
    await ticks()
    assert host.state is GameState.PLAYING
    assert "¡Seguimos!" in reachy.said


@pytest.mark.asyncio
async def test_a_call_pauses_and_quiet_or_a_long_pause_ends(room) -> None:
    """A ringing call pauses the game; nobody playing for IDLE_END_S ends it; so does a pause of PAUSED_END_S."""
    reachy, host, made = room
    await _playing(reachy, host)
    reachy.busy = True
    await ticks()
    assert host.state is GameState.PAUSED
    reachy.pats += 1  # a pat answers the call, not the game
    await ticks()
    assert host.state is GameState.PAUSED
    reachy.busy = False
    reachy.now += host_module.PAUSED_END_S
    await ticks()
    assert host.state is GameState.IDLE
    await _playing(reachy, host)
    reachy.now += host_module.IDLE_END_S
    await ticks()
    assert host.state is GameState.IDLE and reachy.said[-1] == "Vale, se acabó el juego."


@pytest.mark.asyncio
async def test_other_announcements_wait_only_while_a_game_plays(room) -> None:
    """The runtime holds other announcements while ``holding()``; a pause lets them through."""
    reachy, host, made = room
    assert not host.holding()
    await _playing(reachy, host)
    assert host.holding()
    host.pause()
    assert not host.holding()


@pytest.mark.asyncio
async def test_status_and_unknown_games(room) -> None:
    """The admin app sees the state, the game and its progress; an unknown game is refused."""
    reachy, host, made = room
    assert host.status()["state"] == "idle" and host.status()["games"][0]["key"] == "colors"
    assert host.start("chess", "es") == "There is no game 'chess'."
    await _playing(reachy, host)
    status = host.status()
    assert (status["state"], status["game"]) == ("playing", "colors")


def test_every_host_line_is_cached() -> None:
    """The questions and replies are fixed lines in both languages, so the reply voice caches them."""
    phrases = host_phrases()
    assert "¿Jugamos a los colores? Yo digo un color y tú me traes algo de ese color." in phrases
    assert "Shall we play color hunt? I say a color, and you bring me something that color." in phrases
    assert "Estamos jugando a los colores. ¿Lo terminamos?" in phrases
    assert "Paused. Keep playing, or stop?" in phrases
