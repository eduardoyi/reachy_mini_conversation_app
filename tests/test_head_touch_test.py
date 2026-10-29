from pathlib import Path

from reachy_mini_conversation_app.head_touch_test import HEAD_TEST_INTRO, HEAD_TEST_PHASES, save_head_test


def test_a_head_test_is_saved_with_time_since_the_start_and_the_last_cue_heard(tmp_path: Path) -> None:
    """Each row's time counts from the start, and its phase is the last cue heard by then."""
    started = 1000.0
    heard = [started + 2.0, started + 13.0, started + 21.0]
    rows = [(started + 0.5, 1.0), (started + 2.5, 2.0), (started + 13.0, 3.0), (started + 999.0, 4.0)]

    path = save_head_test(rows, ("t", "z"), started, heard, tmp_path)

    assert path is not None and path.parent == tmp_path
    lines = path.read_text().splitlines()
    assert lines[0] == "phase,t,z"
    assert lines[1] == f"{HEAD_TEST_INTRO},0.500,1.000000"  # the first cue was still being spoken
    assert lines[2] == f"{HEAD_TEST_PHASES[0]},2.500,2.000000"
    assert lines[3] == f"{HEAD_TEST_PHASES[1]},13.000,3.000000"
    assert lines[4].startswith(f"{HEAD_TEST_PHASES[2]},999.000")  # past the last cue heard: still its phase


def test_an_empty_head_test_saves_nothing(tmp_path: Path) -> None:
    """No readings, no file."""
    assert save_head_test([], ("t",), 0.0, [], tmp_path / "head-test") is None
    assert not (tmp_path / "head-test").exists()
