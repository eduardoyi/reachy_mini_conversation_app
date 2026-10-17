from reachy_mini_conversation_app.spoken_turns import SpokenTurnAssembler


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000

    def __call__(self) -> int:
        self.now += 100
        return self.now


def _assembler(speaker: object = None) -> tuple[SpokenTurnAssembler, list[tuple[str, str, int, int, str | None]]]:
    emitted: list[tuple[str, str, int, int, str | None]] = []
    assembler = SpokenTurnAssembler(
        lambda role, text, start, end, who: emitted.append((role, text, start, end, who)),
        speaker=speaker,  # type: ignore[arg-type]
        clock_ms=_Clock(),
    )
    return assembler, emitted


def test_pieces_join_as_sent_and_each_utterance_is_emitted_once() -> None:
    """Pieces keep their own spacing; the answer starting ends the user's utterance; nothing repeats."""
    assembler, emitted = _assembler(lambda: "eduardo")

    assembler.user_piece("mi hermano Pablo empieza un trabajo")
    assembler.user_piece(" nuevo en Lisboa")
    assembler.assistant_piece("Eso es una ")
    assembler.assistant_piece("excelente noticia.")
    assembler.turn_complete()
    assembler.turn_complete()

    assert emitted == [
        ("user", "mi hermano Pablo empieza un trabajo nuevo en Lisboa", 1100, 1200, "eduardo"),
        ("assistant", "Eso es una excelente noticia.", 1300, 1400, None),
    ]


def test_a_late_user_piece_during_the_answer_is_emitted_before_the_answer() -> None:
    """Transcription that trails the answer's start still reaches memory, ahead of the answer."""
    assembler, emitted = _assembler()

    assembler.user_piece("On Sunday Fernanda has her")
    assembler.assistant_piece("That sounds ")
    assembler.user_piece(" swimming competition at 10:00.")
    assembler.assistant_piece("lovely.")
    assembler.turn_complete()

    assert [(role, text) for role, text, *_ in emitted] == [
        ("user", "On Sunday Fernanda has her"),
        ("user", "swimming competition at 10:00."),
        ("assistant", "That sounds lovely."),
    ]


def test_interruptions_drop_the_answer_and_reset_drops_everything() -> None:
    """A cut-off answer never reaches memory, and a reconnect forgets unfinished turns without flushing."""
    assembler, emitted = _assembler()

    assembler.user_piece("tell me a story")
    assembler.assistant_piece("Once upon a ")
    assembler.interrupted()
    assembler.turn_complete()
    assembler.user_piece("half a sentence")
    assembler.reset()
    assembler.turn_complete()

    assert [(role, text) for role, text, *_ in emitted] == [("user", "tell me a story")]


def test_empty_pieces_and_a_failing_speaker_lookup_are_harmless() -> None:
    """Blank transcripts emit nothing; if recognition fails the utterance simply has no speaker."""

    def broken() -> str:
        raise RuntimeError("camera busy")

    assembler, emitted = _assembler(broken)

    assembler.user_piece(None)
    assembler.user_piece("")
    assembler.user_piece("   ")
    assembler.turn_complete()
    assembler.user_piece("hello")
    assembler.turn_complete()

    assert [(role, text, who) for role, text, _start, _end, who in emitted] == [("user", "hello", None)]
