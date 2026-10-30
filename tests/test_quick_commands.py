"""Tests for everyday commands answered without the cloud conversation."""

import asyncio
from typing import Any
from datetime import datetime, timezone
from unittest.mock import MagicMock

import numpy as np
import pytest

from reachy_mini_conversation_app import moves, photographer, quick_commands
from reachy_mini_conversation_app.maribel import MaribelReply
from reachy_mini_conversation_app.weather import Forecast, DayForecast
from reachy_mini_conversation_app.whatsapp import SendResult
from reachy_mini_conversation_app.android_tv import TVState
from reachy_mini_conversation_app.home_devices import HubDevice
from reachy_mini_conversation_app.quick_commands import (
    SENT_TO,
    COUNTDOWN,
    PHOTO_SENT,
    SEND_TO_WHO,
    PHOTO_UNSURE,
    SEND_TO_ONLY,
    FIXED_PHRASES,
    MARIBEL_ASKING,
    MESSAGE_PROMPT,
    MESSAGE_UNSURE,
    PHOTO_NOT_SENT,
    MESSAGE_NOTHING,
    MESSAGE_NOT_SENT,
    PHOTO_NO_PICTURE,
    MARIBEL_PASSED_ON,
    MARIBEL_UNREACHABLE,
    QuickCommands,
    parse,
    say_date,
    say_time,
    normalize,
    maribel_request,
    resolve_light_target,
)
from reachy_mini_conversation_app.head_touch_test import (
    HEAD_TEST_CUES,
    HEAD_TEST_DONE,
    HEAD_TEST_FAILED,
    HEAD_TEST_PAUSE_S,
)
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies


@pytest.mark.parametrize(
    ("text", "kind", "language", "args"),
    [
        ("Turn off the lights.", "power", "en", {"on": False, "target": "the lights"}),
        (
            "Can you turn on the living room lights, please?",
            "power",
            "en",
            {"on": True, "target": "the living room lights"},
        ),
        ("lights off", "power", "en", {"on": False, "target": "lights"}),
        ("Apaga las luces", "power", "es", {"on": False, "target": "las luces"}),
        ("apaga a luz", "power", "pt", {"on": False, "target": "a luz"}),
        ("spegni le luci", "power", "it", {"on": False, "target": "le luci"}),
        ("desliga as luzes", "power", "pt", {"on": False, "target": "as luzes"}),
        ("pon la tele", "power", "es", {"on": True, "target": "tv"}),
        ("dim the lights", "brightness", "en", {"level": 30, "target": "the lights"}),
        ("pon las luces al 50 por ciento", "brightness", "es", {"level": 50, "target": "las luces"}),
        ("make the TV lights blue", "color", "en", {"color": "blue", "target": "the tv lights"}),
        ("pon las luces azules", "color", "es", {"color": "blue", "target": "las luces"}),
        ("metti le luci rosse", "color", "it", {"color": "red", "target": "le luci"}),
        ("What time is it?", "time", "en", {}),
        ("¿Qué hora es?", "time", "es", {}),
        ("que horas são", "time", "pt", {}),
        ("che ore sono", "time", "it", {}),
        ("what's the date today", "date", "en", {}),
        ("¿Va a llover mañana?", "weather", "es", {"tomorrow": True, "place": None, "rain_only": True}),
        ("what's the weather in Madrid", "weather", "en", {"tomorrow": False, "place": "madrid", "rain_only": False}),
        ("che tempo fa domani", "weather", "it", {"tomorrow": True, "place": None, "rain_only": False}),
        ("pause", "pause", "en", {}),
        ("siguiente canción", "next", "es", {}),
        ("play some music", "resume", "en", {}),
        ("pon a Bad Bunny", "play", "es", {"query": "bad bunny"}),
        ("play despacito by luis fonsi", "play", "en", {"query": "despacito by luis fonsi"}),
        ("plays the rolling stones", "play", "en", {"query": "the rolling stones"}),
        ("stop. Oké, graag.", "stop", "en", {}),
        ("Pause the music. Thanks a lot", "pause", "en", {}),
        ("turn up the tv", "volume", "en", {"direction": "up", "device": "tv"}),
        ("set the volume to 40", "volume", "en", {"level": 40, "device": None}),
        ("baja la música", "volume", "es", {"direction": "down", "device": "music"}),
        ("go to sleep", "sleep", "en", {}),
        ("vete a dormir", "sleep", "es", {}),
        ("never mind", "cancel", "en", {}),
        ("put Bluey on YouTube", "youtube", "en", {"query": "bluey"}),
        ("abre Netflix", "open_app", "es", {"app": "netflix"}),
    ],
)
def test_everyday_commands_are_recognized(text: str, kind: str, language: str, args: dict[str, Any]) -> None:
    """Common requests in the household's four languages map to one command."""
    command = parse(text)
    assert command is not None
    assert (command.kind, command.language, command.args) == (kind, language, args)


@pytest.mark.parametrize(
    "text",
    [
        "tell me a story about dinosaurs",
        "what's the capital of France",
        "play with me",
        "stop. no, keep playing",
        "play hide and seek",
        "is it true that sharks sleep",
        "turn off the lights and tell me a joke about cats and dogs and elephants please now",
        "can you tell me what you see",
        "play that song again",
        "play my favorite song",
        "pon esa canción otra vez",
        # Negations, questions and remarks about the lights are not commands.
        "don't turn the lights off",
        "no apagues la luz",
        "non spegnere la luce",
        "não desligues a luz",
        "who turned the lights off?",
        "is the TV on?",
        "we should turn off the lights",
        # Everyday phrases that share a word with a command.
        "I can't sleep",
        "it's story time",
        "no tengo tiempo",
        "pon la mesa",
        "pon un temporizador de cinco minutos",
        "put on your shoes",
        "put on a movie",
        "what's next",
        "hagamos una pausa",
        "salta",
        "baja",
    ],
)
def test_conversation_requests_are_left_to_gemini(text: str) -> None:
    """Questions, games and long requests are not commands."""
    assert parse(text) is None


def test_normalize_drops_accents_apostrophes_and_punctuation() -> None:
    """Whisper's punctuation and accents do not matter."""
    assert normalize("¿Qué hora es?") == "que hora es"
    assert normalize("What's the time?") == "whats the time"
    assert normalize("Che ore sono? L'ora!") == "che ore sono lora"


def _light(name: str, room: str | None = "Living room", areas: tuple[str, ...] = (), **kwargs: Any) -> HubDevice:
    return HubDevice(name, "light", name, room, False, True, brightness=50, areas=areas, **kwargs)


HOME = [
    _light("Living light 1", areas=("Living",)),
    _light("Dining light 1", areas=("Dining",)),
    _light("TV Lights", areas=("Living",), colors=True, source="tuya"),
    _light("Living lamp", areas=("Living",)),
    _light("Dining lamp", areas=("Dining",)),
]


@pytest.mark.parametrize(
    ("phrase", "name", "devices"),
    [
        ("the lights", "all", ()),
        ("la luz", "all", ()),
        ("everything", "all", ()),
        ("the living room lights", "Living room", ()),
        ("el salon", "Living room", ()),
        ("the dining lights", "Dining", ()),
        ("la luz del comedor", "Dining", ()),
        ("la luce della sala da pranzo", "Dining", ()),
        ("the tv lights", None, ("TV Lights",)),
        ("las luces de la tele", None, ("TV Lights",)),
        ("the lamps", None, ("Living lamp", "Dining lamp")),
        ("the living lamp", None, ("Living lamp",)),
        ("the living room lamp", None, ("Living lamp",)),
        ("la lampara de la sala", None, ("Living lamp",)),
    ],
)
def test_light_targets_resolve_across_languages(phrase: str, name: str | None, devices: tuple[str, ...]) -> None:
    """Rooms, areas and devices match in English, Spanish and Italian."""
    target = resolve_light_target(phrase, HOME)
    assert target is not None
    assert (target.name, target.devices) == (name, devices)


def test_an_unknown_light_target_is_not_guessed() -> None:
    """A name that matches nothing goes to the conversation."""
    assert resolve_light_target("the garage", HOME) is None


def test_time_and_date_sentences() -> None:
    """The clock is read in the request's language."""
    morning = datetime(2026, 10, 18, 9, 5, tzinfo=timezone.utc)
    one = datetime(2026, 10, 18, 1, 30, tzinfo=timezone.utc)
    assert say_time(morning, "en") == "It's 9:05 AM."
    assert say_time(morning, "es") == "Son las 9:05."
    assert say_time(one, "es") == "Es la 1:30."
    assert say_time(morning, "it") == "Sono le 9:05."
    assert say_time(morning, "pt") == "São 9:05."
    assert say_date(morning, "en") == "Today is Sunday, October 18."
    assert say_date(morning, "es") == "Hoy es domingo, 18 de octubre."


class _Tool:
    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.responses = responses or {}

    async def __call__(self, _deps: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        response = self.responses.get(kwargs.get("action"), {"ok": True})
        return response(kwargs) if callable(response) else response


class _Home:
    async def devices(self) -> list[HubDevice]:
        return HOME


class _TV:
    def __init__(self, state: TVState) -> None:
        self._state = state

    async def state(self) -> TVState:
        return self._state


def _runner(**kwargs: Any) -> tuple[QuickCommands, dict[str, _Tool], Any]:
    deps = ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock(), home_hub=_Home())
    deps.tv = kwargs.pop("tv", None)
    tools = {
        "lights": kwargs.pop("lights", _Tool({"set": lambda call: {"ok": True, "changed": [call["target"]]}})),
        "spotify": kwargs.pop("spotify", _Tool({"status": {"status": "No current playback.", "volume_percent": 40}})),
        "tv": kwargs.pop("tv_tool", _Tool()),
    }
    runner = QuickCommands(
        deps,
        lights=tools["lights"],
        spotify=tools["spotify"],
        tv=tools["tv"],
        spotify_configured=lambda: True,
        clock=lambda: datetime(2026, 10, 18, 9, 5, tzinfo=timezone.utc),
        **kwargs,
    )
    return runner, tools, deps


@pytest.mark.asyncio
async def test_lights_off_runs_home_lights_and_says_okay() -> None:
    """Saying "Apaga las luces" switches everything off without a word."""
    runner, tools, _ = _runner()
    reply = await runner.handle("apaga las luces")
    assert reply is not None and reply.text is None  # the lights changing is the answer
    assert reply.language == "es"
    assert tools["lights"].calls == [{"action": "set", "target": "all", "on": False}]


@pytest.mark.asyncio
async def test_a_device_list_target_changes_each_device() -> None:
    """Saying "the lamps" sets both lamps by name."""
    runner, tools, _ = _runner()
    await runner.handle("turn on the lamps")
    assert [call["target"] for call in tools["lights"].calls] == ["Living lamp", "Dining lamp"]


@pytest.mark.asyncio
async def test_lights_that_did_not_respond_are_named() -> None:
    """A light that failed is named; one already offline is not news for "all the lights"."""
    lights = _Tool(
        {"set": {"ok": True, "changed": ["Living light 1"], "failed": ["TV Lights"], "unreachable": ["Old lamp"]}}
    )
    runner, _, _ = _runner(lights=lights)
    reply = await runner.handle("turn off the lights")
    assert reply is not None and reply.text == "Done, but TV Lights didn't respond."
    offline_only = _Tool({"set": {"ok": True, "changed": ["Living light 1"], "unreachable": ["Old lamp"]}})
    runner, _, _ = _runner(lights=offline_only)
    reply = await runner.handle("turn off the lights")
    assert reply is not None and reply.text is None


@pytest.mark.asyncio
async def test_turn_the_lights_back_on() -> None:
    """Saying "back on" is the same as "on"."""
    runner, tools, _ = _runner()
    await runner.handle("turn the lights back on")
    assert tools["lights"].calls == [{"action": "set", "target": "all", "on": True}]


@pytest.mark.asyncio
async def test_unknown_light_name_goes_to_the_conversation() -> None:
    """Nothing matched locally: the conversation takes the request."""
    runner, tools, _ = _runner()
    assert await runner.handle("turn on the garage") is None
    assert tools["lights"].calls == []


@pytest.mark.asyncio
async def test_tv_power_goes_to_the_tv_tool() -> None:
    """Saying "turn off the TV" means the TV, not the TV lights."""
    runner, tools, _ = _runner(tv=_TV(TVState(True, "com.netflix.ninja", 20, False)))
    reply = await runner.handle("turn off the tv")
    assert reply is not None and reply.text is None
    assert tools["tv"].calls == [{"action": "power", "on": False}]
    assert tools["lights"].calls == []


@pytest.mark.asyncio
async def test_play_searches_then_plays_after_the_reply() -> None:
    """The artist is announced first; playback starts in the follow-up."""
    spotify = _Tool(
        {
            "search": lambda call: {
                "results": [{"name": "Bad Bunny", "creators": [], "uri": "spotify:artist:" + "a" * 22}]
                if call["search_type"] == "artist"
                else [{"name": "Tití Me Preguntó", "creators": ["Bad Bunny"], "uri": "spotify:track:" + "b" * 22}]
            },
            "play": {"status": "ok"},
        }
    )
    runner, _, _ = _runner(spotify=spotify)
    reply = await runner.handle("pon a Bad Bunny")
    assert reply is not None and reply.text == "Pongo Bad Bunny."
    assert not any(call["action"] == "play" for call in spotify.calls)
    assert reply.after is not None and await reply.after() is None
    assert spotify.calls[-1] == {"action": "play", "uri": "spotify:artist:" + "a" * 22}


@pytest.mark.asyncio
async def test_play_a_genre_searches_playlists() -> None:
    """Saying "play some jazz" plays a jazz playlist, not an artist called Jazz."""
    spotify = _Tool(
        {"search": {"results": [{"name": "Jazz Classics", "creators": [], "uri": "spotify:playlist:" + "c" * 22}]}}
    )
    runner, _, _ = _runner(spotify=spotify)
    reply = await runner.handle("play some jazz")
    assert reply is not None and reply.text == "Playing Jazz Classics."
    assert spotify.calls[1:] == [{"action": "search", "query": "jazz", "search_type": "playlist"}]


@pytest.mark.asyncio
async def test_volume_goes_to_the_tv_when_nothing_plays_on_spotify() -> None:
    """With Spotify idle and the TV on, "volume up" is the TV's."""
    runner, tools, _ = _runner(tv=_TV(TVState(True, "com.google.android.youtube.tv", 20, False)))
    reply = await runner.handle("volume up")
    assert reply is not None and reply.text is None
    assert tools["tv"].calls == [{"action": "volume", "change": "up"}]


@pytest.mark.asyncio
async def test_volume_steps_spotify_from_its_current_level() -> None:
    """While music plays, "louder" raises Spotify by 5 and "lower" drops it by 5."""
    spotify = _Tool({"status": {"status": "playing", "volume_percent": 40}})
    runner, _, _ = _runner(spotify=spotify)
    await runner.handle("louder")
    assert spotify.calls[-1] == {"action": "volume", "volume_percent": 45}
    await runner.handle("Lower.")
    assert spotify.calls[-1] == {"action": "volume", "volume_percent": 35}


@pytest.mark.asyncio
async def test_volume_steps_from_the_level_before_reachy_lowered_the_music(monkeypatch: pytest.MonkeyPatch) -> None:
    """Music at 30 is lowered to 20 while Reachy listens; "lower" means 25, not 15."""
    from reachy_mini_conversation_app.tools.spotify_control import SPOTIFY_DUCKER

    monkeypatch.setattr(SPOTIFY_DUCKER, "_restore_to", 30)
    spotify = _Tool({"status": {"status": "playing", "volume_percent": 20}})
    runner, _, _ = _runner(spotify=spotify)
    await runner.handle("lower")
    assert spotify.calls[-1] == {"action": "volume", "volume_percent": 25}


@pytest.mark.parametrize("text", ["set volume to 25", "said volume to 25%.", "put the volume at 25"])
def test_set_volume_phrasings(text: str) -> None:
    """Whisper often hears "set" as "said"; both set the level."""
    command = parse(text)
    assert command is not None and command.kind == "volume" and command.args["level"] == 25


@pytest.mark.asyncio
async def test_pause_with_nothing_playing_is_silent() -> None:
    """Nothing to pause: no tool call and no words."""
    runner, tools, _ = _runner()
    reply = await runner.handle("pause")
    assert reply is not None and reply.text is None
    assert [call["action"] for call in tools["spotify"].calls] == ["status"]


@pytest.mark.asyncio
async def test_set_the_tv_to_a_number_is_its_volume() -> None:
    """Saying "set the TV to 20" changes the TV volume, not the lights near it."""
    runner, tools, _ = _runner(tv=_TV(TVState(True, "com.netflix.ninja", 30, False)))
    await runner.handle("set the TV to 20")
    assert tools["tv"].calls == [{"action": "volume", "level": 20}]
    assert tools["lights"].calls == []


@pytest.mark.asyncio
async def test_pause_reports_a_spotify_error_instead_of_doing_nothing() -> None:
    """When Spotify's state cannot be read, music may be playing: say it did not work."""
    runner, _, _ = _runner(spotify=_Tool({"status": {"error": "Reconnect Spotify authorization."}}))
    reply = await runner.handle("pause")
    assert reply is not None and reply.text == "Sorry, that didn't work."


@pytest.mark.asyncio
async def test_resume_with_nothing_to_resume_goes_to_the_conversation() -> None:
    """No recent playback: Gemini can pick something to play."""
    spotify = _Tool({"status": {"status": "No current playback."}, "resume": {"error": "Start a song first."}})
    runner, _, _ = _runner(spotify=spotify)
    assert await runner.handle("play some music") is None


@pytest.mark.asyncio
async def test_a_spotify_search_error_is_not_reported_as_not_found() -> None:
    """A failed search says it did not work."""
    runner, _, _ = _runner(spotify=_Tool({"search": {"error": "Reconnect Spotify authorization."}}))
    reply = await runner.handle("play Bad Bunny")
    assert reply is not None and reply.text == "Sorry, that didn't work."


@pytest.mark.asyncio
async def test_sleep_lowers_the_head_after_good_night() -> None:
    """Saying "go to sleep" says good night, then the follow-up lowers the head."""
    runner, _, deps = _runner()
    reply = await runner.handle("go to sleep")
    assert reply is not None and reply.sleep and reply.text == "Good night!"
    deps.movement_manager.go_to_sleep.assert_not_called()
    assert reply.after is not None
    await reply.after()
    deps.movement_manager.go_to_sleep.assert_called_once()


class _Weather:
    def __init__(self) -> None:
        self.places: list[Any] = []

    async def geocode(self, name: str, language: str = "en") -> Any:
        return name.title()

    async def forecast(self, place: Any = None) -> Forecast:
        self.places.append(place)
        return Forecast("Lisbon", 18.4, 2, DayForecast(2, 24.6, 16.2, 10), DayForecast(61, 20, 14, 80))

    async def home_name(self, language: str) -> str:
        return {"es": "Lisboa"}.get(language, "Lisbon")


@pytest.mark.asyncio
async def test_weather_is_answered_from_the_forecast_in_the_local_name() -> None:
    """Home weather uses the city's name in the request's language."""
    weather = _Weather()
    runner, _, _ = _runner(weather=weather)
    reply = await runner.handle("¿va a llover mañana?")
    assert reply is not None and reply.text == "Sí, probablemente llueva mañana en Lisboa: 80% de probabilidad."
    reply = await runner.handle("what's the weather")
    assert (
        reply is not None and reply.text == "In Lisbon it's 18 degrees and partly cloudy. Today's high is 25, low 16."
    )


@pytest.mark.asyncio
async def test_weather_without_a_home_goes_to_the_conversation() -> None:
    """No HOME_LOCATION: Gemini answers weather as before."""
    runner, _, _ = _runner()
    assert await runner.handle("what's the weather") is None


@pytest.mark.asyncio
async def test_play_says_when_the_spotify_speaker_is_unreachable() -> None:
    """An idle Cast speaker missing from Spotify's devices is reported before any song is announced."""
    spotify = _Tool({"status": {"error": "Open Spotify on the configured device; it is unavailable or restricted."}})
    runner, _, _ = _runner(spotify=spotify)
    reply = await runner.handle("play Bad Bunny")
    assert reply is not None and "can't reach the Spotify speaker" in (reply.text or "")
    assert [call["action"] for call in spotify.calls] == ["status"]


@pytest.mark.asyncio
async def test_household_routines_run_locally_and_good_night_also_sleeps() -> None:
    """Movie mode and good night from the home layout run without the conversation."""
    from types import SimpleNamespace

    from reachy_mini_conversation_app.home_devices import Routine, HomeLayout

    calls: list[dict[str, Any]] = []

    async def lights(_deps: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"ok": True, "changed": ["TV Lights"]}

    layout = HomeLayout(
        routines=(
            Routine(say=("movie mode", "modo película"), on=("TV Lights",), off=("all",)),
            Routine(say=("good night", "buenas noches"), off=("all",), sleep=True),
        )
    )
    slept: list[bool] = []
    deps = SimpleNamespace(
        home_hub=SimpleNamespace(layout=layout),
        movement_manager=SimpleNamespace(go_to_sleep=lambda: slept.append(True)),
    )
    quick = QuickCommands(deps, lights=lights)

    reply = await quick.handle("Movie mode, please.")
    assert reply is not None and reply.text is None and calls == [{"action": "scene", "scene": "movie mode"}]
    reply = await quick.handle("¿Modo película?")
    assert reply is not None and reply.language == "es"
    reply = await quick.handle("Buenas noches")
    assert reply is not None and reply.text == "¡Buenas noches!" and reply.sleep and reply.after is not None
    await reply.after()
    assert slept == [True]
    assert await quick.handle("movie mode is fun") is None  # only the whole phrase runs a routine


@pytest.mark.asyncio
async def test_good_night_also_turns_the_tv_off_and_pauses_the_music() -> None:
    """The good night routine: lights off, music paused, TV off, then Reachy sleeps."""
    import asyncio

    from reachy_mini_conversation_app.home_devices import Routine, HomeLayout

    spotify = _Tool({"status": {"status": "playing", "volume_percent": 40}})
    lights = _Tool({"scene": {"ok": True, "changed": ["Living light 1"]}})
    runner, tools, deps = _runner(spotify=spotify, lights=lights, tv=_TV(TVState(True, None, 20, False)))
    deps.home_hub.layout = HomeLayout(  # type: ignore[attr-defined]
        routines=(Routine(say=("good night",), off=("all",), sleep=True, tv_off=True, pause_music=True),)
    )
    reply = await runner.handle("Good night")
    await asyncio.gather(*runner._background)
    assert reply is not None and reply.text == "Good night!" and reply.sleep
    assert lights.calls == [{"action": "scene", "scene": "good night"}]
    assert {"action": "pause"} in spotify.calls
    assert tools["tv"].calls == [{"action": "power", "on": False}]


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("¿Puedes dibujar a Enzo como si fuese Spiderman?", "es"),
        ("Can you show me the weather please?", "en"),
        ("Puoi fare un disegno per favore?", "it"),
        ("Podes fazer isso por favor?", "pt"),
        ("Hmm", "es"),
    ],
)
def test_guess_language_picks_the_household_language(text: str, language: str) -> None:
    """The cannot-connect line is said in the language the request was made in."""
    from reachy_mini_conversation_app.quick_commands import guess_language

    assert guess_language(text, default="es") == language


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("Ask Maribel to book a table for Friday.", "en"),
        ("Can you tell Maribel that I'll be late?", "en"),
        ("Let Maribel know the plumber called.", "en"),
        ("Pregúntale a Maribel si mañana llueve.", "es"),
        ("Dile a Maribel que compre leche.", "es"),
        ("Chiedi a Maribel di prenotare.", "it"),
        ("Pergunta à Maribel a que horas é o jantar.", "pt"),
        ("Le puedes preguntar a Maribel qué tengo en mi agenda para el viernes?", "es"),
        ("¿Puedes pedirle a Maribel que reserve mesa?", "es"),
        ("Puoi chiedere a Maribel cosa c'è in agenda?", "it"),
        ("Podes perguntar à Maribel se há reuniões amanhã?", "pt"),
        ("ask Marybel what's on my calendar", "en"),
    ],
)
def test_a_request_that_names_maribel_goes_to_her(text: str, language: str) -> None:
    """Requests that start with ask or tell Maribel, in the household's languages, go to her."""
    assert maribel_request(text) == language


@pytest.mark.parametrize(
    "text",
    ["Ask Maribel.", "What did Maribel say?", "Maribel is coming tonight", "turn off the lights", "ask Mario to call"],
)
def test_other_requests_do_not_go_to_maribel(text: str) -> None:
    """Mentioning her, or naming nothing to pass on, stays with Reachy."""
    assert maribel_request(text) is None


@pytest.fixture(autouse=True)
def _thinking_moves(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the thinking move instead of loading the emotion library."""
    queued: list[str] = []
    monkeypatch.setattr(quick_commands, "queue_intent_move", lambda _manager, intent: queued.append(intent))
    return queued


class _Maribel:
    """A stand-in for MaribelLink: accepts unless told not to, then replies with ``reply``."""

    def __init__(self, reply: MaribelReply | None, *, reachable: bool = True) -> None:
        self.reply = reply
        self.reachable = reachable
        self.sent: list[str] = []

    async def send(self, text: str) -> str | None:
        self.sent.append(text)
        return "r-1" if self.reachable else None

    async def first_reply(self, request_id: str, timeout_s: float = 10.0) -> MaribelReply | None:
        return self.reply


@pytest.mark.asyncio
async def test_asking_maribel_says_so_then_speaks_her_reply(_thinking_moves: list[str]) -> None:
    """Reachy says he is asking, then sends the words verbatim and speaks her reply."""
    runner, _, deps = _runner()
    deps.maribel = _Maribel(MaribelReply("r-1", "c-1", "answer", "It's Wednesday."))

    reply = await runner.handle("Pregúntale a Maribel qué día es hoy")

    assert reply is not None and reply.kind == "maribel" and reply.whole_request
    assert reply.text == MARIBEL_ASKING["es"] and reply.after is not None
    assert reply.after_timeout_s is not None and reply.after_timeout_s > 10
    assert deps.maribel.sent == []  # she is asked after Reachy says so
    assert await reply.after() == "It's Wednesday."
    assert deps.maribel.sent == ["Pregúntale a Maribel qué día es hoy"]
    await asyncio.sleep(0.05)
    assert _thinking_moves == ["thinking"]  # while she works


@pytest.mark.asyncio
async def test_maribel_unreachable_or_slow_gets_the_agreed_lines() -> None:
    """Unreachable and no reply within the wait each have their own line."""
    runner, _, deps = _runner()
    deps.maribel = _Maribel(None, reachable=False)
    reply = await runner.handle("Ask Maribel to call the plumber")
    assert reply is not None and reply.after is not None
    assert await reply.after() == MARIBEL_UNREACHABLE["en"]

    deps.maribel = _Maribel(None)
    reply = await runner.handle("Ask Maribel to call the plumber")
    assert reply is not None and reply.after is not None
    assert await reply.after() == MARIBEL_PASSED_ON["en"]


@pytest.mark.asyncio
async def test_her_question_listens_for_the_answer_and_sends_it_back() -> None:
    """Her question opens the answer window; the answer is sent without the acknowledgement."""
    runner, _, deps = _runner()
    deps.maribel = _Maribel(MaribelReply("r-1", "c-1", "question", "For how many?", "q-1"))
    expected: list[Any] = []
    deps.expect_answer = expected.append

    reply = await runner.handle("Ask Maribel to book dinner")
    assert reply is not None and reply.after is not None
    assert await reply.after() == "For how many?"
    (answer,) = expected

    deps.maribel.reply = MaribelReply("r-2", "c-1", "answer", "Booked for four.")
    answered = await answer("four of us")
    assert answered.text is None and answered.after is not None  # no "Asking Maribel" for an answer
    assert await answered.after() == "Booked for four."
    assert deps.maribel.sent[-1] == "four of us"


@pytest.mark.asyncio
async def test_without_maribel_configured_her_name_is_an_ordinary_request() -> None:
    """Without Maribel set up, the request goes to the conversation."""
    runner, _, _ = _runner()
    assert await runner.handle("Ask Maribel to book dinner") is None


# Photos for the WhatsApp group ---------------------------------------------------------------

GROUP = "120363416355796813@g.us"


def _jpeg() -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 120, 40)).save(buffer, "JPEG")
    return buffer.getvalue()


class _WhatsApp:
    """A stand-in for WhatsAppLink that records what it was asked to send."""

    def __init__(self, result: SendResult = SendResult.SENT) -> None:
        self.result = result
        self.sent: list[tuple[str, bytes]] = []

    async def send_image(self, chat: str, jpeg: bytes) -> SendResult:
        self.sent.append((chat, jpeg))
        return self.result


class _Display:
    """Records the screens put up, or fails like a broken spool."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.shown: list[float | None] = []

    def show(self, png: bytes, owner: str = "requested", *, expires_after_s: float | None = None) -> None:
        if self.fail:
            raise OSError("spool unwritable")
        assert png.startswith(b"\x89PNG")
        self.shown.append(expires_after_s)


@pytest.fixture
def photos(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Configure the photos group, skip the head settle, and record the order of the photo steps."""
    from reachy_mini_conversation_app.config import config

    monkeypatch.setattr(config, "WHATSAPP_PHOTOS_CHAT", GROUP)
    monkeypatch.setattr(quick_commands, "PHOTO_SETTLE_S", 0.0)
    return []


def _photo_runner(
    steps: list[str], jpeg: bytes | None = None, *, result: SendResult = SendResult.SENT, display: Any = None
) -> tuple[QuickCommands, Any]:
    picture = _jpeg() if jpeg is None else jpeg

    def photographer(media: Any, faces: Any) -> bytes | None:
        steps.append("photo")
        return picture or None

    runner, _, deps = _runner(photographer=photographer)
    deps.camera_enabled = True
    deps.whatsapp = _WhatsApp(result)
    deps.display = display
    return runner, deps


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("Take a picture", "en"),
        ("Can you take a photo of us?", "en"),
        ("take our picture please", "en"),
        ("snap a pic of everyone", "en"),
        ("Sácanos una foto", "es"),
        ("¿Puedes sacarnos una foto?", "es"),
        ("hazme una foto", "es"),
        ("toma una foto de todos", "es"),
        ("Toma una foto", "es"),
        ("Tómanos una foto", "es"),
        ("tómanos una foto a todos", "es"),
        ("¿Nos tomas una foto?", "es"),
        ("¿me sacas una foto?", "es"),
        ("tomemos una foto.", "es"),
        ("saquemos una foto de todos", "es"),
        ("vamos a hacer una foto", "es"),
        ("let's take a picture", "en"),
        ("facciamo una foto", "it"),
        ("vamos tirar uma foto", "pt"),
        ("Facci una foto", "it"),
        ("puoi farci una foto?", "it"),
        ("scatta una foto a noi", "it"),
        ("Tira-nos uma foto", "pt"),
        ("podes tirar uma fotografia?", "pt"),
        ("bate uma foto de nós", "pt"),
    ],
)
def test_asking_for_a_picture_is_a_photo_command(text: str, language: str) -> None:
    """Take a picture (of us) and its polite forms, in the household's four languages."""
    assert parse(text) == quick_commands.QuickCommand("photo", language)


@pytest.mark.parametrize(
    "text",
    [
        "take a picture of me as a pirate",
        "don't take a picture",
        "what's in this picture",
        "no me hagas una foto",
        "show me a picture of a tiger",
    ],
)
def test_other_picture_requests_are_not_the_photo_command(text: str) -> None:
    """Edits, negations and questions about pictures are left to the conversation."""
    command = parse(text)
    assert command is None or command.kind != "photo"


@pytest.mark.asyncio
async def test_a_picture_counts_down_then_is_shown_and_sent_to_the_group(photos: list[str]) -> None:
    """Three spoken beats, then the photo goes to the screen and to the WhatsApp group: "Sent!"."""
    display = _Display()
    runner, deps = _photo_runner(photos, display=display)

    reply = await runner.handle("¿Puedes sacarnos una foto?")

    assert reply is not None and reply.kind == "photo" and reply.whole_request
    assert reply.text is None and reply.beats == COUNTDOWN["es"] and reply.beat_s == 1.0
    assert photos == [] and deps.whatsapp.sent == []  # nothing happens before the countdown
    assert reply.after is not None and reply.after_timeout_s == quick_commands.PHOTO_STEP_TIMEOUT_S
    assert deps.movement_manager.frame_faces.call_args_list == []  # framing starts when the countdown does
    assert reply.before is not None
    reply.before()
    assert await reply.after() == PHOTO_SENT["es"]
    assert photos == ["photo"]
    assert [call.args for call in deps.movement_manager.frame_faces.call_args_list] == [(True,), (False,)]
    assert display.shown == [photographer.PHOTO_ON_SCREEN_S]
    assert [chat for chat, _ in deps.whatsapp.sent] == [GROUP]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "said"), [(SendResult.FAILED, PHOTO_NOT_SENT["en"]), (SendResult.UNSURE, PHOTO_UNSURE["en"])]
)
async def test_a_photo_that_is_not_sent_says_so(photos: list[str], result: SendResult, said: str) -> None:
    """A refused send and one that may still arrive have their own lines."""
    runner, _ = _photo_runner(photos, result=result)
    reply = await runner.handle("take a picture")
    assert reply is not None and reply.after is not None
    assert await reply.after() == said


@pytest.mark.asyncio
async def test_no_picture_from_the_camera_sends_nothing(photos: list[str]) -> None:
    """The camera gave nothing: Reachy says so and nothing goes to WhatsApp."""
    runner, deps = _photo_runner(photos, jpeg=b"")
    reply = await runner.handle("take a picture")
    assert reply is not None and reply.after is not None
    assert await reply.after() == PHOTO_NO_PICTURE["en"]
    assert deps.whatsapp.sent == []


@pytest.mark.asyncio
async def test_a_photo_that_takes_too_long_is_not_sent(photos: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """A stuck camera ends the step with "no picture", not silence."""
    import time

    monkeypatch.setattr(photographer, "PHOTO_TAKE_S", 0.05)

    def stuck(media: Any, faces: Any) -> bytes:
        time.sleep(0.3)
        return _jpeg()

    runner, _, deps = _runner(photographer=stuck)
    deps.camera_enabled = True
    deps.whatsapp = _WhatsApp()
    reply = await runner.handle("take a picture")
    assert reply is not None and reply.after is not None
    assert await reply.after() == PHOTO_NO_PICTURE["en"]
    assert deps.whatsapp.sent == []


@pytest.mark.asyncio
async def test_a_broken_screen_does_not_stop_the_photo_being_sent(photos: list[str]) -> None:
    """The e-ink preview is a bonus; the photo still reaches the group."""
    runner, deps = _photo_runner(photos, display=_Display(fail=True))
    reply = await runner.handle("take a photo of us")
    assert reply is not None and reply.after is not None
    assert await reply.after() == PHOTO_SENT["en"]
    assert len(deps.whatsapp.sent) == 1


@pytest.mark.asyncio
async def test_without_whatsapp_or_the_camera_a_picture_goes_to_the_conversation(
    photos: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not configured, or the camera off: today's path (Gemini and the e-ink screen)."""
    from reachy_mini_conversation_app.config import config

    runner, deps = _photo_runner(photos)
    deps.camera_enabled = False
    assert await runner.handle("take a picture") is None

    deps.camera_enabled = True
    monkeypatch.setattr(config, "WHATSAPP_PHOTOS_CHAT", None)
    assert await runner.handle("take a picture") is None

    deps.whatsapp = None
    monkeypatch.setattr(config, "WHATSAPP_PHOTOS_CHAT", GROUP)
    assert await runner.handle("take a picture") is None
    assert photos == []


@pytest.mark.asyncio
async def test_other_commands_never_touch_the_camera_or_whatsapp(photos: list[str]) -> None:
    """Privacy: only the photo command takes a picture, and it goes only to the configured group."""
    runner, deps = _photo_runner(photos)
    for text in ("turn off the lights", "what time is it", "pause"):
        reply = await runner.handle(text)
        if reply is not None and reply.after is not None:
            await reply.after()
    assert photos == [] and deps.whatsapp.sent == []


def test_every_photo_phrase_is_cached_for_instant_playback() -> None:
    """The countdown and the outcomes are fixed phrases, kept on disk after their first time."""
    for language in quick_commands.LANGUAGES:
        for text in (
            *COUNTDOWN[language],
            PHOTO_SENT[language],
            PHOTO_NOT_SENT[language],
            PHOTO_UNSURE[language],
            PHOTO_NO_PICTURE[language],
        ):
            assert text in FIXED_PHRASES
            assert (text, language) in quick_commands.PHOTO_PHRASES


# Messages and photos to family groups -------------------------------------------------------------

SERGIO_GROUP = "120363413010779824@g.us"


class _WhatsAppAudio(_WhatsApp):
    """Records voice notes too."""

    def __init__(self, result: SendResult = SendResult.SENT) -> None:
        super().__init__(result)
        self.voice_notes: list[tuple[str, bytes]] = []

    async def send_audio(self, chat: str, ogg: bytes) -> SendResult:
        self.voice_notes.append((chat, ogg))
        return self.result


@pytest.fixture
def family(photos: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> list[str]:
    """One family group (Tío Sergio) in the contacts file; voice notes encode to a marker."""
    import json

    from reachy_mini_conversation_app.config import config

    contacts = tmp_path / "contacts.json"
    contacts.write_text(
        json.dumps(
            {"contacts": [{"name": "Tío Sergio", "chat": SERGIO_GROUP, "say": ["tio sergio", "zio sergio", "sergio"]}]}
        )
    )
    monkeypatch.setattr(config, "WHATSAPP_CONTACTS_FILE", str(contacts))
    monkeypatch.setattr(quick_commands, "encode_voice_note", lambda rate, pcm: b"OggS" + pcm.tobytes()[:4])
    return photos


def _family_runner(
    steps: list[str],
    *,
    recorded: Any = (16_000, np.ones(16_000, dtype=np.int16)),
    result: SendResult = SendResult.SENT,
) -> tuple[QuickCommands, Any, list[Any]]:
    runner, deps = _photo_runner(steps)
    deps.whatsapp = _WhatsAppAudio(result)
    answers: list[Any] = []
    deps.expect_answer = answers.append

    async def record(touches: Any = None) -> Any:
        steps.append("record")
        deps.recorded_touches = touches
        return recorded

    deps.record_message = record
    return runner, deps, answers


@pytest.mark.parametrize(
    ("text", "kind", "language", "to"),
    [
        ("Send a message to Tío Sergio", "message", "en", "tio sergio"),
        ("send Sergio a message", "message", "en", "sergio"),
        ("send a message", "message", "en", None),
        ("Vamos a mandar un mensaje a Sergio", "message", "es", "sergio"),
        ("¿Puedes mandarle un audio al tío Sergio?", "message", "es", "tio sergio"),
        ("mándale un mensaje de voz a Sergio", "message", "es", "sergio"),
        ("manda un messaggio vocale allo zio Sergio", "message", "it", "zio sergio"),
        ("envia uma mensagem para o Sergio", "message", "pt", "o sergio"),
        ("send a picture to Sergio", "send_photo", "en", "sergio"),
        ("vamos a mandar una foto", "send_photo", "es", None),
        ("manda una foto a Sergio", "send_photo", "es", "sergio"),
        ("puedes poner una foto a Sergio, por favor", "send_photo", "es", "sergio"),
        ("ponle un mensaje al tío Sergio", "message", "es", "tio sergio"),
    ],
)
def test_asking_to_send_a_message_or_a_picture(text: str, kind: str, language: str, to: str | None) -> None:
    """Messages and pictures for someone, with or without the name, in the household's languages."""
    assert parse(text) == quick_commands.QuickCommand(kind, language, {"to": to})


@pytest.mark.parametrize("text", ["pon una foto", "pon una foto de un tigre", "pon la tele"])
def test_put_counts_as_send_only_with_a_recipient(text: str) -> None:
    """Pon una foto alone is not a message for anyone."""
    command = parse(text)
    assert command is None or command.kind not in ("message", "send_photo")


def test_messages_for_maribel_stay_hers() -> None:
    """Her hand-off owns "message Maribel"; the family groups never see it."""
    assert parse("send a message to Maribel") is None


@pytest.mark.asyncio
async def test_a_message_is_recorded_after_the_prompt_and_sent_as_a_voice_note(family: list[str]) -> None:
    """The prompt names him, the recording runs with the antennas perked, and the voice note goes to his group."""
    runner, deps, _ = _family_runner(family)

    reply = await runner.handle("vamos a mandar un mensaje a Sergio")

    assert reply is not None and reply.kind == "message" and reply.whole_request
    assert reply.text == MESSAGE_PROMPT["es"].format(name="Tío Sergio")
    assert family == []  # nothing is recorded before the prompt has been said
    assert reply.after is not None and reply.after_timeout_s == quick_commands.MESSAGE_STEP_TIMEOUT_S
    assert await reply.after() == SENT_TO["es"].format(name="Tío Sergio")
    assert family == ["record"]
    assert [chat for chat, _ in deps.whatsapp.voice_notes] == [SERGIO_GROUP]
    assert deps.whatsapp.voice_notes[0][1].startswith(b"OggS")
    calls = [call.args for call in deps.movement_manager.set_listening.call_args_list]
    assert calls == [(True,), (False,)]
    # Held still with the speech wobble off, so a touch on his head can end it.
    assert [call.args for call in deps.movement_manager.hold_still.call_args_list] == [(True,), (False,)]
    assert deps.recorded_touches == deps.movement_manager.head_touches
    deps.reachy_mini.disable_wobbling.assert_called_once()
    from reachy_mini_conversation_app.config import config

    assert deps.reachy_mini.enable_wobbling.call_count == int(config.SPEECH_WOBBLE_ENABLED)


@pytest.mark.asyncio
async def test_nothing_said_sends_nothing(family: list[str]) -> None:
    """No speech after the beep: Reachy says so and nothing reaches WhatsApp."""
    runner, deps, _ = _family_runner(family, recorded=None)
    reply = await runner.handle("send a message to Sergio")
    assert reply is not None and reply.after is not None
    assert await reply.after() == MESSAGE_NOTHING["en"]
    assert deps.whatsapp.voice_notes == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "said"), [(SendResult.FAILED, MESSAGE_NOT_SENT["en"]), (SendResult.UNSURE, MESSAGE_UNSURE["en"])]
)
async def test_a_message_that_is_not_sent_says_so(family: list[str], result: SendResult, said: str) -> None:
    """A refused send and one that may still arrive have their own lines."""
    runner, _, _ = _family_runner(family, result=result)
    reply = await runner.handle("send a message to Sergio")
    assert reply is not None and reply.after is not None
    assert await reply.after() == said


@pytest.mark.asyncio
async def test_a_picture_for_sergio_goes_to_his_group(family: list[str]) -> None:
    """The photo countdown, then the photo in his group, not the photos group."""
    runner, deps, _ = _family_runner(family)
    reply = await runner.handle("send a picture to Sergio")
    assert reply is not None and reply.kind == "photo" and reply.beats == COUNTDOWN["en"]
    assert reply.after is not None
    assert await reply.after() == SENT_TO["en"].format(name="Tío Sergio")
    assert [chat for chat, _ in deps.whatsapp.sent] == [SERGIO_GROUP]


@pytest.mark.asyncio
async def test_without_a_name_reachy_asks_who_and_goes_on_with_the_answer(family: list[str]) -> None:
    """Send a message without a name asks who; "a Sergio" answers it and the message flow starts."""
    runner, deps, answers = _family_runner(family)

    reply = await runner.handle("manda un mensaje")

    assert reply is not None and reply.text == SEND_TO_WHO["es"].format(names="Tío Sergio")
    assert reply.after is not None and await reply.after() is None
    (answer,) = answers
    follow = await answer("a Sergio")
    assert follow.text == MESSAGE_PROMPT["es"].format(name="Tío Sergio") and follow.after is not None
    assert await follow.after() == SENT_TO["es"].format(name="Tío Sergio")
    assert [chat for chat, _ in deps.whatsapp.voice_notes] == [SERGIO_GROUP]


@pytest.mark.asyncio
async def test_an_unknown_name_is_asked_about_and_never_sent(family: list[str]) -> None:
    """A name not in the contacts file gets the question; a second unknown name gets who he can send to."""
    runner, deps, answers = _family_runner(family)

    reply = await runner.handle("send a message to Grandpa")
    assert reply is not None and reply.text == SEND_TO_WHO["en"].format(names="Tío Sergio")
    assert reply.after is not None
    await reply.after()
    follow = await answers[0]("Mario")
    assert follow.text == SEND_TO_ONLY["en"].format(names="Tío Sergio") and follow.after is None
    cancelled = await answers[0]("never mind")
    assert cancelled.text is None and cancelled.after is None
    assert family == [] and deps.whatsapp.voice_notes == [] and deps.whatsapp.sent == []


@pytest.mark.asyncio
async def test_without_contacts_or_whatsapp_the_request_goes_to_the_conversation(
    family: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """No family groups (or no WhatsApp): Reachy does not offer to send anything."""
    from reachy_mini_conversation_app.config import config

    runner, deps, _ = _family_runner(family)
    monkeypatch.setattr(config, "WHATSAPP_CONTACTS_FILE", str(tmp_path / "none.json"))
    assert await runner.handle("send a message to Sergio") is None
    monkeypatch.undo()
    deps.whatsapp = None
    assert await runner.handle("send a picture to Sergio") is None


def test_every_fixed_message_phrase_is_cached() -> None:
    """The message outcomes are fixed phrases; the prompt and "sent to" carry a name and are not."""
    for language in quick_commands.LANGUAGES:
        for text in (MESSAGE_NOTHING[language], MESSAGE_NOT_SENT[language], MESSAGE_UNSURE[language]):
            assert text in FIXED_PHRASES


@pytest.mark.parametrize(
    ("text", "language"),
    [("Head test", "en"), ("Let's do the head test", "en"), ("haz la prueba de cabeza", "es")],
)
def test_asking_for_the_head_test_is_its_own_command(text: str, language: str) -> None:
    """Asking for the head test, in English or Spanish, runs the head-touch test."""
    command = parse(text)
    assert command is not None and command.kind == "head_test" and command.language == language


@pytest.mark.asyncio
async def test_the_head_test_cues_touches_while_recording_then_saves_a_file(tmp_path: Any) -> None:
    """Cues are spaced one phase apart; recording runs from the first cue and lands in a CSV."""
    runner, _, deps = _runner(head_test_dir=tmp_path)
    movement = deps.movement_manager
    row = (0.0,) * len(moves.HEAD_RECORDING_COLUMNS)
    movement.stop_head_recording.return_value = [row, row]

    reply = await runner.handle("head test")

    assert reply is not None and reply.kind == "head_test" and reply.text is None
    assert reply.beats == HEAD_TEST_CUES["en"] and reply.beat_pause_s == HEAD_TEST_PAUSE_S
    assert reply.before is not None and reply.after is not None and reply.on_beat is not None
    movement.start_head_recording.assert_not_called()
    reply.before()
    movement.start_head_recording.assert_called_once()
    movement.set_listening.assert_called_once_with(True)  # as while he records a message
    movement.hold_still.assert_called_once_with(True)  # no breathing under the touches
    deps.reachy_mini.disable_wobbling.assert_called_once()  # the cues must not move his head
    reply.on_beat(0)  # the first cue was heard: the rows after it fall in its phase
    assert await reply.after() == HEAD_TEST_DONE["en"]
    assert movement.set_listening.call_args_list[-1].args == (False,)
    assert movement.hold_still.call_args_list[-1].args == (False,)
    from reachy_mini_conversation_app.config import config

    assert deps.reachy_mini.enable_wobbling.call_count == int(config.SPEECH_WOBBLE_ENABLED)
    saved = list(tmp_path.iterdir())
    assert len(saved) == 1 and len(saved[0].read_text().splitlines()) == 3


@pytest.mark.asyncio
async def test_a_head_test_with_no_readings_says_so(tmp_path: Any) -> None:
    """No readings from the robot: nothing is saved and Reachy says the test failed."""
    runner, _, deps = _runner(head_test_dir=tmp_path)
    deps.movement_manager.stop_head_recording.return_value = []
    reply = await runner.handle("prueba de cabeza")
    assert reply is not None and reply.before is not None and reply.after is not None
    reply.before()
    assert await reply.after() == HEAD_TEST_FAILED["es"]
    assert list(tmp_path.iterdir()) == []
