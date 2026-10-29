from pathlib import Path

from reachy_mini_conversation_app.head_touch_test import HEAD_TEST_BEAT_S, HEAD_TEST_PHASES, save_head_test


def test_a_head_test_is_saved_with_time_since_the_first_cue_and_its_phase(tmp_path: Path) -> None:
    """Each row's time counts from the first cue, and its phase is the cue it falls under."""
    started = 1000.0
    rows = [(started + 0.5, 1.0), (started + HEAD_TEST_BEAT_S + 0.5, 2.0), (started + 999.0, 3.0)]

    path = save_head_test(rows, ("t", "z"), started, tmp_path)

    assert path is not None and path.parent == tmp_path
    lines = path.read_text().splitlines()
    assert lines[0] == "phase,t,z"
    assert lines[1] == "rest,0.500,1.000000"
    assert lines[2] == f"tap,{HEAD_TEST_BEAT_S + 0.5:.3f},2.000000"
    assert lines[3].startswith(f"{HEAD_TEST_PHASES[-1]},999.000")  # past the last cue: still the last phase


def test_an_empty_head_test_saves_nothing(tmp_path: Path) -> None:
    """No readings, no file."""
    assert save_head_test([], ("t",), 0.0, tmp_path / "head-test") is None
    assert not (tmp_path / "head-test").exists()
