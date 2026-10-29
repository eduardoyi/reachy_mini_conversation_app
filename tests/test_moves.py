import time
import threading
from unittest.mock import MagicMock, call
from collections.abc import Callable

import numpy as np
import pytest

from reachy_mini.utils import create_head_pose
from reachy_mini.utils.interpolation import compose_world_offset
from reachy_mini_conversation_app.moves import (
    HEAD_HOLD_MAX_S,
    ATTENTIVE_HOLD_S,
    ATTENTIVE_ANTENNAS,
    HEAD_RECORDING_MAX_S,
    PERK_MAX_SPEED_RAD_S,
    HEAD_RECORDING_COLUMNS,
    ANTENNA_TOUCH_PRESS_RAD,
    GazeTurnMove,
    BreathingMove,
    MovementManager,
    HeadTouchDetector,
    AntennaTouchDetector,
    ActivationAcknowledgementMove,
    head_world_yaw,
)
from reachy_mini_conversation_app.dance_emotion_moves import GotoQueueMove, EmotionQueueMove


class _FakeMove:
    """Minimal non-emotion Move stub returning a fixed head pose."""

    def __init__(self, head: np.ndarray) -> None:
        self._head = head
        self.duration = 10.0

    def evaluate(self, t: float):
        return (self._head, np.array([0.0, 0.0]), 0.0)


def _wait_for(predicate: Callable[[], bool], timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_stop_can_skip_neutral_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sleep shutdown should stop the movement loop without undoing the sleep pose."""
    robot = MagicMock()
    manager = MovementManager(robot)
    started = threading.Event()

    def fake_working_loop() -> None:
        started.set()
        while not manager._stop_event.is_set():
            time.sleep(0.001)

    monkeypatch.setattr(manager, "working_loop", fake_working_loop)

    manager.start()
    assert started.wait(timeout=1.0)

    manager.stop(reset_to_neutral=False)

    assert manager._thread is None
    robot.goto_target.assert_not_called()


def test_head_tracking_follows_speaking() -> None:
    """Once enabled, tracking owns the head when idle and releases it while the assistant speaks."""
    robot = MagicMock()
    robot.get_current_head_pose.return_value = np.eye(4)
    robot.get_current_joint_positions.return_value = ([0.0] * 6, [0.0, 0.0])
    manager = MovementManager(robot)
    manager.start()
    try:
        # The head_tracking tool enables tracking with full weight.
        manager.set_head_tracking(True)
        assert _wait_for(lambda: call(weight=1.0) in robot.start_head_tracking.call_args_list)

        # Speaking with a locked face captures the anchor and releases the head.
        manager.set_speaking(True)
        assert _wait_for(lambda: call(weight=0.0) in robot.start_head_tracking.call_args_list)
        assert _wait_for(lambda: manager._track_anchor is not None)

        # Done speaking hands the head back to tracking.
        robot.start_head_tracking.reset_mock()
        manager.set_speaking(False)
        assert _wait_for(lambda: call(weight=1.0) in robot.start_head_tracking.call_args_list)
        assert _wait_for(lambda: manager._track_anchor is None)
    finally:
        manager.stop(reset_to_neutral=False)

    robot.stop_head_tracking.assert_called_once()


def test_speaking_anchor_composes_emotions_and_holds_dances_from_neutral() -> None:
    """While speaking: hold the anchor, compose emotions onto it, play dances from neutral."""
    robot = MagicMock()
    manager = MovementManager(robot)
    anchor = create_head_pose(0, 0, 0, 0, 0, 20, degrees=True)
    manager._track_anchor = anchor

    # No move: the head holds the captured look-at anchor.
    manager.state.current_move = None
    head, _, _ = manager._get_primary_pose(manager._now())
    assert np.allclose(head, anchor)

    # Emotion: composed onto the anchor exactly like the daemon wobble.
    emotion_head = create_head_pose(0, 0, 0, 0, 0, 15, degrees=True)
    recorded = MagicMock()
    recorded.get.return_value = _FakeMove(emotion_head)
    manager.state.current_move = EmotionQueueMove("happy", recorded)
    manager.state.move_start_time = manager._now()
    head, _, _ = manager._get_primary_pose(manager._now())
    assert np.allclose(head, compose_world_offset(anchor, emotion_head))

    # Any other move (e.g. a dance) plays from its own neutral base, ignoring the anchor.
    dance_head = create_head_pose(0, 0, 0, 0, 25, 0, degrees=True)
    manager.state.current_move = _FakeMove(dance_head)
    manager.state.move_start_time = manager._now()
    head, _, _ = manager._get_primary_pose(manager._now())
    assert np.allclose(head, dance_head)


def test_activation_acknowledgement_is_worker_owned_relative_and_returns_to_start() -> None:
    """The activation command preserves the worker's full-body baseline."""
    robot = MagicMock()
    manager = MovementManager(robot)
    start_head = create_head_pose(12, -8, 5, 3, 11, 19, degrees=True)
    start_pose = (start_head, (0.31, -0.27), 0.42)
    manager._last_commanded_pose = start_pose

    manager.acknowledge_activation()

    robot.set_target.assert_not_called()
    assert manager._command_queue.qsize() == 1
    manager._poll_signals(manager._now())

    assert len(manager.move_queue) == 1
    move = manager.move_queue.popleft()
    assert isinstance(move, ActivationAcknowledgementMove)

    at_start, antennas_start, yaw_start = move.evaluate(0.0)
    at_peak, antennas_peak, yaw_peak = move.evaluate(move.duration / 2.0)
    at_end, antennas_end, yaw_end = move.evaluate(move.duration)

    np.testing.assert_array_equal(at_start, start_head)
    np.testing.assert_array_equal(at_end, start_head)
    assert not np.allclose(at_peak, start_head)
    np.testing.assert_array_equal(antennas_start, start_pose[1])
    np.testing.assert_array_equal(antennas_peak, start_pose[1])
    np.testing.assert_array_equal(antennas_end, start_pose[1])
    assert (yaw_start, yaw_peak, yaw_end) == (start_pose[2],) * 3


class _AntennaClock:
    """Drive the antenna layer tick by tick on a fake clock, as the worker loop would."""

    def __init__(self, manager: MovementManager, antennas: tuple[float, float] = (-0.1745, 0.1745)) -> None:
        self.manager = manager
        self.now = 1000.0
        manager._now = lambda: self.now
        manager._last_listening_blend_time = self.now
        manager._last_listening_toggle_time = self.now - 1.0  # past the listening debounce
        manager._last_commanded_pose = (np.eye(4), antennas, 0.0)
        manager._listening_antennas = antennas

    def tick(self, target: tuple[float, float] = (-0.1745, 0.1745), dt: float = 1 / 60) -> tuple[float, float]:
        self.now += dt
        self.manager._poll_signals(self.now)
        antennas = self.manager._calculate_blended_antennas(target)
        self.manager._issue_control_command(np.eye(4), antennas, 0.0)
        return antennas

    def run(self, seconds: float, target: tuple[float, float] = (-0.1745, 0.1745)) -> list[tuple[float, float]]:
        return [self.tick(target) for _ in range(round(seconds * 60))]


def test_wake_springs_the_antennas_to_the_alert_pose_and_holds_it() -> None:
    """A wake flicks the antennas past the alert pose, then they settle there and stay while he listens."""
    manager = MovementManager(MagicMock())
    clock = _AntennaClock(manager)

    manager.acknowledge_activation()
    path = clock.run(1.0)

    # The right antenna's alert angle is positive: it overshoots, then settles on the pose.
    assert max(right for _, right in path) > ATTENTIVE_ANTENNAS[1] + np.deg2rad(3)
    np.testing.assert_allclose(path[-1], ATTENTIVE_ANTENNAS, atol=1e-3)

    # Listening, then the pause while he works out an answer, keep the pose.
    manager.set_listening(True)
    clock.run(3.0)
    manager.set_listening(False)
    np.testing.assert_allclose(clock.run(ATTENTIVE_HOLD_S - 1.0)[-1], ATTENTIVE_ANTENNAS, atol=1e-3)


def test_answering_releases_the_alert_antennas_smoothly() -> None:
    """Speaking drops the hold; the antennas ease back to the move's antennas instead of snapping."""
    manager = MovementManager(MagicMock())
    clock = _AntennaClock(manager)
    manager.acknowledge_activation()
    clock.run(1.0)

    manager.set_speaking(True)
    first = clock.tick()
    assert abs(first[1] - ATTENTIVE_ANTENNAS[1]) < np.deg2rad(5)
    np.testing.assert_allclose(clock.run(0.5)[-1], (-0.1745, 0.1745), atol=1e-6)


@pytest.mark.parametrize("release", ["timeout", "conversation_closed", "sleep"])
def test_alert_antennas_release_without_an_answer(release: str) -> None:
    """A wake nobody follows up on, a closed conversation, or sleep all let the antennas relax."""
    manager = MovementManager(MagicMock())
    clock = _AntennaClock(manager)
    manager.acknowledge_activation()
    clock.run(1.0)

    if release == "timeout":
        clock.run(ATTENTIVE_HOLD_S)
    elif release == "conversation_closed":
        manager.set_conversation_active(False)
    else:
        manager.go_to_sleep()
    clock.run(1.0)

    assert manager._attentive_until is None
    assert manager._perk_started_at is None


def test_following_turns_perk_again_when_the_user_speaks() -> None:
    """Without a wake word, the user starting to talk raises the antennas again."""
    manager = MovementManager(MagicMock())
    clock = _AntennaClock(manager)

    manager.set_listening(True)
    np.testing.assert_allclose(clock.run(1.0)[-1], ATTENTIVE_ANTENNAS, atol=1e-3)


def test_perk_out_of_the_sleep_pose_is_speed_capped() -> None:
    """Waking from the folded sleep pose swings the antennas up no faster than the cap."""
    from reachy_mini.reachy_mini import SLEEP_ANTENNAS_JOINT_POSITIONS

    manager = MovementManager(MagicMock())
    sleep_antennas = (float(SLEEP_ANTENNAS_JOINT_POSITIONS[0]), float(SLEEP_ANTENNAS_JOINT_POSITIONS[1]))
    clock = _AntennaClock(manager, sleep_antennas)
    manager.acknowledge_activation()

    path = [sleep_antennas, *clock.run(1.5, target=sleep_antennas)]
    steps = np.abs(np.diff(np.asarray(path), axis=0))
    assert steps.max() <= PERK_MAX_SPEED_RAD_S / 60 + 1e-9
    np.testing.assert_allclose(path[-1], ATTENTIVE_ANTENNAS, atol=1e-3)


def _robot_facing(yaw_deg: float = 0.0, body_yaw: float = 0.0) -> MagicMock:
    robot = MagicMock()
    robot.get_current_head_pose.return_value = create_head_pose(0, 0, 0, 0, 0, yaw_deg, degrees=True)
    robot.get_current_joint_positions.return_value = ([body_yaw] + [0.0] * 6, [0.0, 0.0])
    return robot


def _start_next_move(manager: MovementManager, now: float) -> None:
    manager.state.current_move = None
    manager._manage_move_queue(now)


@pytest.mark.parametrize(("speech_deg", "turns"), [(60.0, True), (-50.0, True), (15.0, False), (None, False)])
def test_listening_turns_toward_speech_from_clearly_elsewhere(speech_deg: float | None, turns: bool) -> None:
    """User speech queues a turn only when it came from well away from where the head faces."""
    robot = _robot_facing(0.0)
    speech_yaw = None if speech_deg is None else np.radians(speech_deg)
    manager = MovementManager(robot, speaker_direction=lambda: speech_yaw)
    manager._last_listening_toggle_time -= 1.0  # past the listening debounce

    manager.set_listening(True)
    manager._poll_signals(manager._now())

    turns_queued = [move for move in manager.move_queue if isinstance(move, GazeTurnMove)]
    if turns:
        assert len(turns_queued) == 1
        assert turns_queued[0].target_yaw == pytest.approx(speech_yaw)
    else:
        assert turns_queued == []


def test_speaker_turn_without_direction_source_is_a_no_op() -> None:
    """Without a direction source the manager behaves as before."""
    manager = MovementManager(_robot_facing(0.0))
    manager.turn_toward_speaker()
    manager._poll_signals(manager._now())
    assert not manager.move_queue


def test_newer_speaker_turn_replaces_one_that_has_not_started() -> None:
    """Two people talking in quick succession produce one pending turn, toward the latest."""
    robot = _robot_facing(0.0)
    speech = iter([np.radians(60.0), np.radians(-60.0)])
    manager = MovementManager(robot, speaker_direction=lambda: next(speech))

    manager.turn_toward_speaker()
    manager.turn_toward_speaker()
    manager._poll_signals(manager._now())

    assert [move.target_yaw for move in manager.move_queue] == [pytest.approx(np.radians(-60.0))]


def test_gaze_turn_eases_head_and_body_from_the_present_pose() -> None:
    """A turn starts exactly at the measured pose and ends with head and body facing the target."""
    start_head = create_head_pose(0, 0, 0, 0, 10, 20, degrees=True)
    move = GazeTurnMove(np.radians(70.0))
    move.begin((start_head, (0.1, -0.1), np.radians(5.0)))

    assert 0.5 <= move.duration <= 1.4
    head0, antennas0, body0 = move.evaluate(0.0)
    head_mid, _, body_mid = move.evaluate(move.duration / 2)
    head1, antennas1, body1 = move.evaluate(move.duration)

    np.testing.assert_allclose(head0, start_head, atol=1e-9)
    assert body0 == pytest.approx(np.radians(5.0))
    assert np.radians(20.0) < head_world_yaw(head_mid) < np.radians(70.0)
    assert np.radians(5.0) < body_mid < np.radians(70.0)
    assert head_world_yaw(head1) == pytest.approx(np.radians(70.0))
    np.testing.assert_allclose(head1, create_head_pose(0, 0, 0, 0, 0, 70, degrees=True), atol=1e-9)
    assert body1 == pytest.approx(np.radians(70.0))
    np.testing.assert_array_equal(antennas0, [0.1, -0.1])
    np.testing.assert_array_equal(antennas1, [0.1, -0.1])


def test_speaker_turn_pauses_face_tracking_and_resumes_after() -> None:
    """Tracking lets go during the turn so the head can leave the old face, then picks up whoever is there."""
    robot = _robot_facing(0.0)
    manager = MovementManager(robot, speaker_direction=lambda: np.radians(60.0))
    manager.set_head_tracking(True)
    manager.turn_toward_speaker()
    manager._poll_signals(manager._now())
    robot.start_head_tracking.reset_mock()

    now = manager._now()
    _start_next_move(manager, now)
    turn = manager.state.current_move
    assert isinstance(turn, GazeTurnMove)
    assert robot.start_head_tracking.call_args_list == [call(weight=0.0)]
    assert manager._gaze_yaw == pytest.approx(np.radians(60.0))

    manager._manage_move_queue(now + turn.duration + 0.01)
    assert robot.start_head_tracking.call_args_list == [call(weight=0.0), call(weight=1.0)]


def test_clearing_a_running_turn_resumes_face_tracking() -> None:
    """An interruption mid-turn must not leave tracking paused."""
    robot = _robot_facing(0.0)
    manager = MovementManager(robot, speaker_direction=lambda: np.radians(-60.0))
    manager.set_head_tracking(True)
    manager.turn_toward_speaker()
    manager._poll_signals(manager._now())
    _start_next_move(manager, manager._now())
    robot.start_head_tracking.reset_mock()

    manager.clear_move_queue()
    manager._poll_signals(manager._now())

    assert robot.start_head_tracking.call_args_list == [call(weight=1.0)]


def test_breathing_stays_facing_the_last_speaker() -> None:
    """Idle breathing happens around the gaze instead of drifting back to forward."""
    robot = _robot_facing(50.0, body_yaw=np.radians(50.0))
    manager = MovementManager(robot)
    manager._gaze_yaw = np.radians(50.0)
    manager.state.last_activity_time = manager._now() - 10.0

    manager._manage_breathing(manager._now())

    breathing = manager.move_queue[-1]
    assert isinstance(breathing, BreathingMove)
    head, _, body_yaw = breathing.evaluate(breathing.interpolation_duration + 3.0)
    assert head_world_yaw(head) == pytest.approx(np.radians(50.0))
    assert body_yaw == pytest.approx(np.radians(50.0))


def test_emotions_play_toward_the_speaker() -> None:
    """A forward-recorded emotion is rotated, head and body, toward the gaze."""
    manager = MovementManager(_robot_facing(0.0))
    manager._gaze_yaw = np.radians(40.0)
    emotion_head = create_head_pose(0, 0, 0, 0, 10, 0, degrees=True)
    recorded = MagicMock()
    recorded.get.return_value = _FakeMove(emotion_head)
    manager.state.current_move = EmotionQueueMove("happy", recorded)
    manager.state.move_start_time = manager._now()

    head, _, body_yaw = manager._get_primary_pose(manager._now())

    assert head_world_yaw(head) == pytest.approx(np.radians(40.0))
    assert body_yaw == pytest.approx(np.radians(40.0))


def test_explicit_head_moves_reset_the_gaze() -> None:
    """move_head and sweep_look aim in absolute terms, so the speaker gaze no longer applies afterwards."""
    manager = MovementManager(_robot_facing(0.0))
    manager._gaze_yaw = np.radians(40.0)
    manager.move_queue.append(
        GotoQueueMove(
            target_head_pose=np.eye(4),
            start_head_pose=np.eye(4),
            target_antennas=(0.0, 0.0),
            start_antennas=(0.0, 0.0),
            target_body_yaw=0.0,
            start_body_yaw=0.0,
            duration=1.0,
        )
    )

    _start_next_move(manager, manager._now())

    assert manager._gaze_yaw == 0.0


def test_conversation_tracks_faces_then_faces_forward_again() -> None:
    """A conversation turns tracking on; its end turns it off and eases back to forward."""
    robot = _robot_facing(0.0)
    manager = MovementManager(robot)

    manager.set_conversation_active(True)
    manager._poll_signals(manager._now())
    robot.start_head_tracking.assert_called_once_with(weight=1.0)

    manager._gaze_yaw = np.radians(35.0)
    manager.set_conversation_active(False)
    manager._poll_signals(manager._now())

    robot.stop_head_tracking.assert_called_once()
    assert [move.target_yaw for move in manager.move_queue if isinstance(move, GazeTurnMove)] == [0.0]


def test_conversation_end_keeps_tracking_the_user_turned_on() -> None:
    """Tracking the user asked for ("look at me") outlives the conversation."""
    robot = _robot_facing(0.0)
    manager = MovementManager(robot)

    manager.set_head_tracking(True)
    manager.set_conversation_active(True)
    manager.set_conversation_active(False)
    manager._poll_signals(manager._now())

    robot.start_head_tracking.assert_called_once_with(weight=1.0)
    robot.stop_head_tracking.assert_not_called()


def test_a_photo_frames_the_face_then_lets_go() -> None:
    """Framing a photo turns tracking on for the countdown and off afterwards, easing back to the gaze."""
    robot = _robot_facing(0.0)
    manager = MovementManager(robot)

    manager.frame_faces(True)
    manager._poll_signals(manager._now())
    robot.start_head_tracking.assert_called_once_with(weight=1.0)

    manager.frame_faces(False)
    manager._poll_signals(manager._now())
    robot.stop_head_tracking.assert_called_once()
    assert [move.target_yaw for move in manager.move_queue if isinstance(move, GazeTurnMove)] == [0.0]


def test_speaker_turns_wait_while_a_photo_is_framed() -> None:
    """A voice from the side does not turn him away from the face he is framing; afterwards it does again."""
    robot = _robot_facing(0.0)
    manager = MovementManager(robot, speaker_direction=lambda: np.radians(-90.0))

    manager.frame_faces(True)
    manager.turn_toward_speaker()
    manager._poll_signals(manager._now())
    assert not [move for move in manager.move_queue if isinstance(move, GazeTurnMove) and move.target_yaw != 0.0]

    manager.frame_faces(False)
    manager.turn_toward_speaker()
    manager._poll_signals(manager._now())
    assert [move.target_yaw for move in manager.move_queue if isinstance(move, GazeTurnMove)][-1] == pytest.approx(
        np.radians(-90.0)
    )


def test_a_photo_that_never_ends_stops_holding_speaker_turns() -> None:
    """Framing whose end was lost (its activation replaced) holds turns for PHOTO_FRAMING_MAX_S at most."""
    robot = _robot_facing(0.0)
    manager = MovementManager(robot, speaker_direction=lambda: np.radians(-90.0))
    manager.frame_faces(True)
    manager._poll_signals(manager._now())

    manager._framing_until = manager._now() - 1.0  # PHOTO_FRAMING_MAX_S later
    manager.turn_toward_speaker()
    manager._poll_signals(manager._now())
    assert [move.target_yaw for move in manager.move_queue if isinstance(move, GazeTurnMove)][-1] == pytest.approx(
        np.radians(-90.0)
    )


def test_a_photo_leaves_tracking_someone_turned_on() -> None:
    """Tracking asked for before the photo keeps going after it."""
    robot = _robot_facing(0.0)
    manager = MovementManager(robot)

    manager.set_head_tracking(True)
    manager.frame_faces(True)
    manager.frame_faces(False)
    manager._poll_signals(manager._now())

    robot.start_head_tracking.assert_called_once_with(weight=1.0)
    robot.stop_head_tracking.assert_not_called()


def test_a_conversation_started_during_a_photo_ends_its_tracking() -> None:
    """A photo cut short by a conversation hands its tracking over; the conversation's end stops it."""
    robot = _robot_facing(0.0)
    manager = MovementManager(robot)

    manager.frame_faces(True)
    manager.set_conversation_active(True)
    manager.frame_faces(False)  # a late end of the photo no longer stops the conversation's tracking
    manager._poll_signals(manager._now())
    robot.stop_head_tracking.assert_not_called()

    manager.set_conversation_active(False)
    manager._poll_signals(manager._now())
    robot.stop_head_tracking.assert_called_once()


def test_turning_tracking_off_eases_back_to_the_gaze() -> None:
    """Stopping tracking hands the head back smoothly rather than snapping to the last command."""
    robot = _robot_facing(25.0)
    manager = MovementManager(robot)
    manager._gaze_yaw = np.radians(-30.0)
    manager.set_head_tracking(True)
    manager.set_head_tracking(False)
    manager._poll_signals(manager._now())

    assert [move.target_yaw for move in manager.move_queue if isinstance(move, GazeTurnMove)] == [
        pytest.approx(np.radians(-30.0))
    ]


def test_speaking_while_breathing_keeps_facing_the_tracked_face() -> None:
    """Regression: idle breathing used to pull the head and body back to forward as soon as Reachy spoke."""
    robot = _robot_facing(50.0, body_yaw=np.radians(40.0))
    robot.get_tracked_face.return_value.detected = True
    manager = MovementManager(robot)
    manager.set_head_tracking(True)
    manager._poll_signals(manager._now())
    manager.state.last_activity_time = manager._now() - 10.0
    manager._manage_breathing(manager._now())
    _start_next_move(manager, manager._now())
    breathing = manager.state.current_move
    assert isinstance(breathing, BreathingMove) and breathing.base_yaw == 0.0

    manager.set_speaking(True)
    manager._poll_signals(manager._now())

    for elapsed in (0.0, breathing.interpolation_duration + 1.3):
        head, _, body_yaw = manager._get_primary_pose(manager.state.move_start_time + elapsed)
        assert head_world_yaw(head) == pytest.approx(np.radians(50.0))
        assert body_yaw == pytest.approx(np.radians(40.0))


def test_sleep_holds_the_pose_until_an_activation_wakes_him() -> None:
    """Asleep, moves and speaker turns are ignored and closing a conversation keeps the pose; a wake raises the head."""
    from reachy_mini_conversation_app.moves import PoseTransitionMove

    robot = _robot_facing(0.0)
    manager = MovementManager(robot, speaker_direction=lambda: np.radians(60.0))
    manager.set_conversation_active(True)
    manager.go_to_sleep()
    manager._poll_signals(manager._now())

    assert manager._sleeping
    robot.stop_head_tracking.assert_called_once()
    [sleep] = manager.move_queue
    assert isinstance(sleep, PoseTransitionMove) and sleep.duration == float("inf")

    manager.queue_move(_FakeMove(np.eye(4)))
    manager.turn_toward_speaker()
    manager.clear_move_queue()
    manager.set_conversation_active(False)
    manager._poll_signals(manager._now())
    manager._manage_move_queue(manager._now())
    assert manager.state.current_move is sleep and not manager.move_queue

    manager.acknowledge_activation()
    manager._poll_signals(manager._now())
    assert not manager._sleeping
    # The rise is the acknowledgement: a head lift from the captured sleep pose would dip the head after it.
    [wake] = manager.move_queue
    assert isinstance(wake, PoseTransitionMove) and wake.duration < float("inf")
    assert manager._attentive_until is not None


def test_sleep_and_wake_ease_between_the_sleep_and_neutral_poses() -> None:
    """The sleep move ends on the SDK sleep pose facing forward; waking ends on the neutral pose."""
    from reachy_mini.reachy_mini import SLEEP_HEAD_POSE, SLEEP_ANTENNAS_JOINT_POSITIONS
    from reachy_mini_conversation_app.moves import wake_move, sleep_move

    sleep = sleep_move()
    sleep.begin((np.eye(4), (0.0, 0.0), 0.5))
    head, antennas, body_yaw = sleep.evaluate(10.0)
    assert np.allclose(head, SLEEP_HEAD_POSE, atol=1e-3)
    assert np.allclose(antennas, SLEEP_ANTENNAS_JOINT_POSITIONS)
    assert body_yaw == pytest.approx(0.0)
    assert sleep.evaluate(0.0)[2] == pytest.approx(0.5)

    wake = wake_move()
    wake.begin((np.asarray(SLEEP_HEAD_POSE), (-3.05, 3.05), 0.0))
    assert np.allclose(wake.evaluate(wake.duration)[0], np.eye(4), atol=1e-3)


def _touch_ticks(
    detector: AntennaTouchDetector,
    seconds: float,
    target: Callable[[float], tuple[float, float]],
    measured: Callable[[float], tuple[float, float]],
    start: float = 0.0,
) -> list[float]:
    """Feed the detector at the 20 Hz check rate; return when touches fired."""
    fired = []
    for step in range(round(seconds / 0.05)):
        now = round(start + step * 0.05, 3)
        if detector.update(now, target(now), measured(now)):
            fired.append(now)
    return fired


def test_antenna_sway_and_the_perk_are_not_touches() -> None:
    """Breathing sways 15 deg at 0.5 Hz and the perk moves at 6 rad/s; a measurement 0.1 s behind is not a touch."""

    def sway(t: float) -> tuple[float, float]:
        angle = np.deg2rad(15) * np.sin(2 * np.pi * 0.5 * t)
        return (float(angle), float(-angle))

    def perk(t: float) -> tuple[float, float]:
        angle = min(0.61, 6.0 * (t % 2.0))
        return (-angle, angle)

    for target in (sway, perk):
        detector = AntennaTouchDetector()
        assert _touch_ticks(detector, 10.0, target, lambda t, target=target: target(t - 0.1)) == []
        assert detector.peak_deviation < ANTENNA_TOUCH_PRESS_RAD


def test_a_held_bend_is_one_touch_and_a_bump_is_none() -> None:
    """Held 20 deg off for a moment: one touch while held; a 0.1 s bump or a second press within 2 s: none."""
    still = (-0.17, 0.17)
    detector = AntennaTouchDetector()
    bent = np.deg2rad(20)

    def held(t: float) -> tuple[float, float]:
        return (still[0] + bent, still[1]) if 1.0 <= t < 3.0 else still

    fired = _touch_ticks(detector, 4.0, lambda t: still, held)
    assert fired == [pytest.approx(1.2, abs=0.06)]

    def bump(t: float) -> tuple[float, float]:
        return (still[0], still[1] - bent) if 5.0 <= t < 5.1 else still

    assert _touch_ticks(detector, 1.0, lambda t: still, bump, start=4.5) == []

    def twice(t: float) -> tuple[float, float]:
        return (still[0] + bent, still[1]) if 6.0 <= t < 6.5 or 7.0 <= t < 7.5 or 10.0 <= t < 10.5 else still

    assert _touch_ticks(detector, 6.0, lambda t: still, twice, start=5.5) == [
        pytest.approx(6.2, abs=0.06),
        pytest.approx(10.2, abs=0.06),
    ]


def test_the_worker_reports_a_touch_from_the_robots_measured_antennas() -> None:
    """The control loop compares the cached measured antennas with its own targets and calls the handler."""
    robot = MagicMock()
    touches: list[bool] = []
    manager = MovementManager(robot, on_antenna_touch=lambda: touches.append(True))
    manager._last_commanded_pose = (np.eye(4), (-0.17, 0.17), 0.0)
    robot.get_current_joint_positions.return_value = ([0.0] * 7, [-0.17, 0.17])
    for step in range(10):
        manager._check_antenna_touch(100.0 + step * 0.05)
    assert touches == []

    robot.get_current_joint_positions.return_value = ([0.0] * 7, [0.3, 0.17])
    for step in range(10, 20):
        manager._check_antenna_touch(100.0 + step * 0.05)
    assert touches == [True]

    robot.get_current_joint_positions.side_effect = RuntimeError("no state yet")
    manager._check_antenna_touch(200.0)  # a missing reading is skipped, not raised


def _recording_robot() -> MagicMock:
    robot = MagicMock()
    measured = np.eye(4)
    measured[2, 3] = -0.002  # pushed 2 mm down
    robot.get_current_head_pose.return_value = measured
    robot.get_current_joint_positions.return_value = ([0.1] * 7, [-0.2, 0.2])
    return robot


def test_a_head_test_records_the_commanded_and_measured_head_on_every_tick() -> None:
    """Each tick keeps where the head was told to be and where it is, until the recording stops."""
    robot = _recording_robot()
    manager = MovementManager(robot)
    manager._now = lambda: 100.0
    commanded = np.eye(4)
    commanded[2, 3] = 0.001
    manager._last_commanded_pose = (commanded, (-0.17, 0.17), 0.0)
    manager._record_head(99.0)  # not recording yet
    manager.start_head_recording()
    manager._record_head(100.0)
    manager._record_head(100.01)
    rows = manager.stop_head_recording()
    manager._record_head(100.02)

    assert len(rows) == 2 and all(len(row) == len(HEAD_RECORDING_COLUMNS) for row in rows)
    row = dict(zip(HEAD_RECORDING_COLUMNS, rows[0]))
    assert row["t"] == 100.0 and row["cmd_23"] == 0.001 and row["meas_23"] == -0.002
    assert row["joint_6"] == 0.1 and row["cmd_antenna_left"] == -0.17 and row["antenna_right"] == 0.2
    assert manager.stop_head_recording() == []


def test_a_head_test_that_never_ends_stops_recording() -> None:
    """A test whose end never comes stops keeping rows after HEAD_RECORDING_MAX_S."""
    manager = MovementManager(_recording_robot())
    manager._now = lambda: 100.0
    manager.start_head_recording()
    manager._record_head(100.0 + HEAD_RECORDING_MAX_S + 1.0)
    assert manager.stop_head_recording() == []


def test_an_antenna_touch_during_a_head_test_does_not_wake() -> None:
    """A pat near the antennas is logged during the test but cannot start an activation and end it."""
    robot = MagicMock()
    touches: list[bool] = []
    manager = MovementManager(robot, on_antenna_touch=lambda: touches.append(True))
    manager._last_commanded_pose = (np.eye(4), (-0.17, 0.17), 0.0)
    robot.get_current_joint_positions.return_value = ([0.0] * 7, [0.3, 0.17])
    manager.start_head_recording()
    for step in range(10):
        manager._check_antenna_touch(100.0 + step * 0.05)
    assert touches == []


def test_holding_still_stops_breathing_where_the_head_is_and_keeps_it_from_restarting() -> None:
    """Held still, breathing ends without moving the head and does not start again until released."""
    robot = _robot_facing(0.0)
    manager = MovementManager(robot)
    manager.state.last_activity_time = manager._now() - 10.0
    manager._manage_breathing(manager._now())
    now = manager._now()
    _start_next_move(manager, now)
    assert isinstance(manager.state.current_move, BreathingMove)
    held = manager._get_primary_pose(now + 3.0)[0].copy()

    manager.hold_still(True)
    manager._poll_signals(now + 3.0)
    assert manager.state.current_move is None and not manager.move_queue
    assert np.allclose(manager._get_primary_pose(now + 10.0)[0], held)
    manager.state.last_activity_time = now - 100.0
    manager._manage_breathing(now + 10.0)
    assert not manager.move_queue

    manager.hold_still(False)
    manager._poll_signals(now + 10.0)
    manager.state.last_activity_time = now - 100.0
    manager._manage_breathing(now + 10.0)
    assert isinstance(manager.move_queue[-1], BreathingMove)


def test_holding_still_skips_speaker_turns_and_ends_on_its_own() -> None:
    """No turn toward a voice while held still; a hold nobody releases lapses after HEAD_HOLD_MAX_S."""
    robot = _robot_facing(0.0)
    clock = {"now": 100.0}
    manager = MovementManager(robot, speaker_direction=lambda: np.radians(60.0))
    manager._now = lambda: clock["now"]
    manager.hold_still(True)
    manager._poll_signals(clock["now"])
    manager.turn_toward_speaker()
    manager._poll_signals(clock["now"])
    assert not any(isinstance(move, GazeTurnMove) for move in manager.move_queue)

    clock["now"] += HEAD_HOLD_MAX_S + 1.0
    manager.turn_toward_speaker()
    manager._poll_signals(clock["now"])
    assert any(isinstance(move, GazeTurnMove) for move in manager.move_queue)


def _head_at(x_mm: float = 0.0, z_mm: float = 0.0, roll_deg: float = 0.0) -> np.ndarray:
    pose = np.eye(4)
    roll = np.radians(roll_deg)
    pose[1:3, 1:3] = [[np.cos(roll), -np.sin(roll)], [np.sin(roll), np.cos(roll)]]
    pose[0, 3], pose[2, 3] = x_mm / 1000.0, z_mm / 1000.0
    return pose


def test_a_sudden_move_of_the_still_head_is_one_touch_and_staying_pushed_is_not_more() -> None:
    """A push past 1 mm counts once; the head then resting where it was pushed counts nothing more."""
    detector = HeadTouchDetector()
    command = np.eye(4)
    ticks = [100.0 + step * 0.02 for step in range(200)]
    touches = [
        now
        for now in ticks
        if detector.update(now, command, _head_at(x_mm=0.0 if now < 102.0 else 2.0, z_mm=0.03 * np.sin(now * 40)))
    ]
    assert touches == [pytest.approx(102.0, abs=0.03)]
    assert detector.touch_move_m == pytest.approx(0.002, abs=0.0002)


def test_a_small_tilt_is_a_touch_but_noise_is_not() -> None:
    """A 1 degree tilt in 0.2 s counts; 0.4 mm and 0.2 degrees of jitter (the untouched maximum) does not."""
    detector = HeadTouchDetector()
    command = np.eye(4)
    jitter = [
        detector.update(100.0 + step * 0.02, command, _head_at(x_mm=0.4 * (step % 2), roll_deg=0.2 * (step % 2)))
        for step in range(150)
    ]
    assert not any(jitter)
    assert detector.update(103.1, command, _head_at(roll_deg=1.0))
    assert 0.7 <= np.degrees(detector.touch_tilt_rad) <= 1.0  # from the oldest reading in the window


def test_a_moving_command_or_a_fresh_hold_is_never_a_touch() -> None:
    """While the command moves (breathing) or before it has been still for the settle time, nothing counts."""
    detector = HeadTouchDetector()
    for step in range(100):
        now = 100.0 + step * 0.02
        breath = _head_at(z_mm=5.0 * np.sin(now))
        assert not detector.update(now, breath, _head_at(z_mm=5.0 * np.sin(now - 0.5)))
    still = np.eye(4)
    assert not detector.update(110.0, still, _head_at())
    assert not detector.update(110.5, still, _head_at(x_mm=3.0))  # still settling after the hold began


def test_touches_are_counted_only_while_the_head_is_held_still() -> None:
    """The movement loop counts touches on the held head; outside a hold, the same push is not counted."""
    robot = MagicMock()
    manager = MovementManager(robot)
    clock = {"now": 100.0}
    manager._now = lambda: clock["now"]
    manager._last_commanded_pose = (np.eye(4), (-0.17, 0.17), 0.0)

    def push_at(at: float) -> None:
        for step in range(150):
            clock["now"] = at + step * 0.02
            robot.get_current_head_pose.return_value = _head_at(x_mm=2.0 if step >= 100 else 0.0)
            manager._check_head_touch(clock["now"])

    push_at(100.0)
    assert manager.head_touches() == 0
    manager.hold_still(True)
    manager._poll_signals(clock["now"])
    push_at(clock["now"])
    assert manager.head_touches() == 1


def test_a_restart_in_the_sleep_pose_is_not_an_antenna_touch() -> None:
    """Restarted asleep, the antennas are folded: the sleep pose starts there instead of upright, so no touch."""
    robot = MagicMock()
    folded = [-3.05, 3.05]
    robot.get_current_joint_positions.return_value = ([0.0] * 7, folded)
    robot.get_current_head_pose.return_value = np.eye(4)
    touches: list[bool] = []
    manager = MovementManager(robot, on_antenna_touch=lambda: touches.append(True))
    assert manager._last_commanded_pose[1] == (-3.05, 3.05)

    clock = {"now": 100.0}
    manager._now = lambda: clock["now"]
    manager._fall_asleep()
    for step in range(40):  # 2 s of control ticks; the folded antennas never move
        clock["now"] = 100.0 + step * 0.05
        manager._update_primary_motion(clock["now"])
        head, antennas, body_yaw = manager._get_primary_pose(clock["now"])
        manager._issue_control_command(head, manager._calculate_blended_antennas(antennas), body_yaw)
        manager._check_antenna_touch(clock["now"])
    assert touches == []


def test_without_a_reading_the_antennas_start_from_neutral() -> None:
    """A robot that has not reported its joints yet leaves the usual neutral start."""
    robot = MagicMock()
    robot.get_current_joint_positions.side_effect = AssertionError("No joint positions received yet.")
    assert MovementManager(robot)._last_commanded_pose[1] == (0.0, 0.0)
