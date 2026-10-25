"""Movement system driving sequential primary moves.

Design overview
- Primary moves (emotions, dances, goto, breathing) are mutually exclusive and run
  sequentially.
- There is a single control point to the robot: `ReachyMini.set_target`.
- The control loop runs near 100 Hz and is phase-aligned via a monotonic clock.
- Idle behaviour starts an infinite `BreathingMove` after a short inactivity delay
  unless listening is active.

Threading model
- A dedicated worker thread owns all real-time state and issues `set_target`
  commands.
- Other threads communicate via a command queue (enqueue moves, mark activity,
  toggle listening).

Units and frames
- Antennas and `body_yaw` are in radians.

Safety
- A wake or listening springs the antennas to an alert pose and holds it until he answers,
  then blends them back.
- Interpolations and blends are used to avoid jumps at all times.
- `set_target` errors are rate-limited in logs.
"""

from __future__ import annotations
import time
import logging
import threading
from queue import Empty, Queue
from typing import Any, Dict, Tuple, Callable
from collections import deque
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from reachy_mini import ReachyMini
from reachy_mini.utils import create_head_pose
from reachy_mini.motion.move import Move
from reachy_mini.reachy_mini import SLEEP_HEAD_POSE, INIT_ANTENNAS_JOINT_POSITIONS, SLEEP_ANTENNAS_JOINT_POSITIONS
from reachy_mini.utils.interpolation import compose_world_offset, linear_pose_interpolation
from reachy_mini_conversation_app.dance_emotion_moves import GotoQueueMove, DanceQueueMove, EmotionQueueMove


logger = logging.getLogger(__name__)

# Configuration constants
CONTROL_LOOP_FREQUENCY_HZ = 60.0  # Hz - Target frequency for the movement control loop
# Speech from a direction this close to where the head already looks does not trigger a turn.
SPEAKER_TURN_MIN_RAD = np.deg2rad(25.0)
SPEAKER_TURN_MIN_DURATION_S = 0.5
SPEAKER_TURN_MAX_DURATION_S = 1.4
# Wake cue: on a wake the antennas spring out to an alert V (the pose of the "attentive" emotion) and hold
# it while he listens, so you can see he heard his name. They drop back once he starts answering.
ATTENTIVE_ANTENNAS = (-float(np.deg2rad(35.0)), float(np.deg2rad(35.0)))
# Without an answer, stop holding this long after the wake or the user's last words.
ATTENTIVE_HOLD_S = 8.0
# Damped spring toward the alert pose: ~20% overshoot, settled in about 0.4 s.
PERK_DECAY_S = 0.1
PERK_FREQUENCY_HZ = 3.0
# Caps the perk's swing, e.g. out of the folded sleep pose; recorded emotions peak near 8 rad/s.
PERK_MAX_SPEED_RAD_S = 6.0

# Type definitions
FullBodyPose = Tuple[NDArray[np.float32], Tuple[float, float], float]  # (head_pose_4x4, antennas, body_yaw)


class BreathingMove(Move):  # type: ignore
    """Breathing move with interpolation to neutral and then continuous breathing patterns."""

    def __init__(
        self,
        interpolation_start_pose: NDArray[np.float32],
        interpolation_start_antennas: Tuple[float, float],
        interpolation_duration: float = 1.0,
        base_yaw: float = 0.0,
    ):
        """Initialize breathing move.

        Args:
            interpolation_start_pose: 4x4 matrix of current head pose to interpolate from
            interpolation_start_antennas: Current antenna positions to interpolate from
            interpolation_duration: Duration of interpolation to neutral (seconds)
            base_yaw: World yaw (radians) the head and body breathe around, e.g. toward the last speaker

        """
        self.interpolation_start_pose = interpolation_start_pose
        self.interpolation_start_antennas = np.array(interpolation_start_antennas)
        self.interpolation_duration = interpolation_duration
        self.base_yaw = float(base_yaw)

        # Neutral positions for breathing base
        self.neutral_head_pose = create_head_pose(0, 0, 0, 0, 0, self.base_yaw, degrees=False)
        self.neutral_antennas = np.array([-0.1745, 0.1745])  # ~10° offset to reduce shaking

        # Breathing parameters
        self.breathing_z_amplitude = 0.005  # 5mm gentle breathing
        self.breathing_frequency = 0.1  # Hz (6 breaths per minute)
        self.antenna_sway_amplitude = np.deg2rad(15)  # 15 degrees
        self.antenna_frequency = 0.5  # Hz (faster antenna sway)

    @property
    def duration(self) -> float:
        """Duration property required by official Move interface."""
        return float("inf")  # Continuous breathing (never ends naturally)

    def evaluate(self, t: float) -> tuple[NDArray[np.float64] | None, NDArray[np.float64] | None, float | None]:
        """Evaluate breathing move at time t."""
        if t < self.interpolation_duration:
            # Phase 1: Interpolate to neutral base position
            interpolation_t = t / self.interpolation_duration

            # Interpolate head pose
            head_pose = linear_pose_interpolation(
                self.interpolation_start_pose,
                self.neutral_head_pose,
                interpolation_t,
            )

            # Interpolate antennas
            antennas_interp = (
                1 - interpolation_t
            ) * self.interpolation_start_antennas + interpolation_t * self.neutral_antennas
            antennas = antennas_interp.astype(np.float64)

        else:
            # Phase 2: Breathing patterns from neutral base
            breathing_time = t - self.interpolation_duration

            # Gentle z-axis breathing
            z_offset = self.breathing_z_amplitude * np.sin(2 * np.pi * self.breathing_frequency * breathing_time)
            head_pose = create_head_pose(
                x=0, y=0, z=z_offset, roll=0, pitch=0, yaw=self.base_yaw, degrees=False, mm=False
            )

            # Antenna sway (opposite directions)
            antenna_sway = self.antenna_sway_amplitude * np.sin(2 * np.pi * self.antenna_frequency * breathing_time)
            antennas = np.array([antenna_sway, -antenna_sway], dtype=np.float64)

        # Return in official Move interface format: (head_pose, antennas_array, body_yaw)
        return (head_pose, antennas, self.base_yaw)


class ActivationAcknowledgementMove(Move):  # type: ignore
    """Short relative head lift that returns to the captured full-body pose."""

    duration = 0.35

    def __init__(self, start_pose: FullBodyPose) -> None:
        """Capture the worker's last commanded pose as the gesture baseline."""
        self._start_pose = clone_full_body_pose(start_pose)

    def evaluate(self, t: float) -> tuple[NDArray[np.float64], NDArray[np.float64], float]:
        """Return a relative chin-up lift and the exact captured pose at both endpoints."""
        if t <= 0.0 or t >= self.duration:
            start_head, start_antennas, body_yaw = clone_full_body_pose(self._start_pose)
            return (
                np.asarray(start_head, dtype=np.float64),
                np.asarray(start_antennas, dtype=np.float64),
                body_yaw,
            )

        progress = max(0.0, min(1.0, t / self.duration))
        pitch = -8.0 * np.sin(np.pi * progress)  # negative pitch looks up
        offset = create_head_pose(0, 0, 0, 0, pitch, 0, degrees=True)
        head_pose = compose_world_offset(self._start_pose[0], offset, reorthonormalize=True)
        antennas_array = np.asarray(self._start_pose[1], dtype=np.float64)
        return head_pose, antennas_array, self._start_pose[2]


def head_pose_z_bob(move: BreathingMove, t: float) -> float:
    """Return the breathing move's vertical offset (meters) at time ``t`` after its interpolation phase."""
    breathing_time = t - move.interpolation_duration
    return float(move.breathing_z_amplitude * np.sin(2 * np.pi * move.breathing_frequency * breathing_time))


def head_world_yaw(head_pose: NDArray[np.float64]) -> float:
    """Return the yaw (radians, positive = left) the head faces in the world frame."""
    return float(np.arctan2(head_pose[1, 0], head_pose[0, 0]))


def yaw_offset(yaw: float) -> NDArray[np.float64]:
    """Return a world-frame rotation about the vertical axis."""
    return np.asarray(create_head_pose(0, 0, 0, 0, 0, yaw, degrees=False), dtype=np.float64)


class GazeTurnMove(Move):  # type: ignore
    """Turn head and body together to face a world yaw, starting from the pose the robot holds when it begins."""

    def __init__(self, target_yaw: float) -> None:
        """Prepare a turn toward ``target_yaw``; the worker calls ``begin`` when the move starts."""
        self.target_yaw = float(target_yaw)
        self._start_pose: FullBodyPose | None = None
        self._duration = SPEAKER_TURN_MAX_DURATION_S
        self._target_head = yaw_offset(self.target_yaw)

    @property
    def duration(self) -> float:
        """Longer turns take longer, within fixed bounds."""
        return self._duration

    def begin(self, start_pose: FullBodyPose) -> None:
        """Capture the pose the turn starts from and size the turn's duration."""
        self._start_pose = clone_full_body_pose(start_pose)
        delta = abs(self.target_yaw - head_world_yaw(np.asarray(start_pose[0], dtype=np.float64)))
        self._duration = float(
            np.clip(
                SPEAKER_TURN_MIN_DURATION_S + delta / np.pi * SPEAKER_TURN_MAX_DURATION_S,
                SPEAKER_TURN_MIN_DURATION_S,
                SPEAKER_TURN_MAX_DURATION_S,
            )
        )

    def evaluate(self, t: float) -> tuple[NDArray[np.float64], NDArray[np.float64] | None, float]:
        """Ease from the start pose to the neutral pose rotated toward the target yaw."""
        if self._start_pose is None:
            return self._target_head.copy(), None, self.target_yaw
        start_head, start_antennas, start_body_yaw = self._start_pose
        progress = float(np.clip(t / self._duration, 0.0, 1.0))
        eased = progress * progress * (3.0 - 2.0 * progress)
        head = linear_pose_interpolation(np.asarray(start_head, dtype=np.float64), self._target_head, eased)
        body_yaw = start_body_yaw + (self.target_yaw - start_body_yaw) * eased
        return head, np.asarray(start_antennas, dtype=np.float64), body_yaw


SLEEP_TRANSITION_S = 2.0
WAKE_TRANSITION_S = 1.5


class PoseTransitionMove(Move):  # type: ignore
    """Ease from the pose the robot holds when it begins to a fixed pose, optionally holding it until cleared."""

    def __init__(
        self,
        target_head: NDArray[np.float64],
        target_antennas: Tuple[float, float],
        *,
        transition_s: float,
        hold: bool = False,
    ) -> None:
        """Prepare a transition; the worker calls ``begin`` when the move starts."""
        self._target_head = np.asarray(target_head, dtype=np.float64).copy()
        self._target_antennas = np.asarray(target_antennas, dtype=np.float64)
        self._transition_s = transition_s
        self._hold = hold
        self._start_pose: FullBodyPose | None = None

    @property
    def duration(self) -> float:
        """A held pose lasts until the queue is cleared."""
        return float("inf") if self._hold else self._transition_s

    def begin(self, start_pose: FullBodyPose) -> None:
        """Capture the pose the transition starts from."""
        self._start_pose = clone_full_body_pose(start_pose)

    def evaluate(self, t: float) -> tuple[NDArray[np.float64], NDArray[np.float64], float]:
        """Ease head, antennas and body (to facing forward) toward the target, then hold it."""
        if self._start_pose is None:
            return self._target_head.copy(), self._target_antennas.copy(), 0.0
        start_head, start_antennas, start_body_yaw = self._start_pose
        progress = float(np.clip(t / self._transition_s, 0.0, 1.0))
        eased = progress * progress * (3.0 - 2.0 * progress)
        head = linear_pose_interpolation(np.asarray(start_head, dtype=np.float64), self._target_head, eased)
        antennas = np.asarray(start_antennas, dtype=np.float64) * (1.0 - eased) + self._target_antennas * eased
        return head, antennas, start_body_yaw * (1.0 - eased)


def sleep_move() -> PoseTransitionMove:
    """Lower into the SDK's sleep pose and stay there until woken."""
    sleep_antennas = (float(SLEEP_ANTENNAS_JOINT_POSITIONS[0]), float(SLEEP_ANTENNAS_JOINT_POSITIONS[1]))
    return PoseTransitionMove(SLEEP_HEAD_POSE, sleep_antennas, transition_s=SLEEP_TRANSITION_S, hold=True)


def wake_move() -> PoseTransitionMove:
    """Rise from the sleep pose back to the neutral pose."""
    neutral_antennas = (float(INIT_ANTENNAS_JOINT_POSITIONS[0]), float(INIT_ANTENNAS_JOINT_POSITIONS[1]))
    return PoseTransitionMove(np.eye(4), neutral_antennas, transition_s=WAKE_TRANSITION_S)


def clone_full_body_pose(pose: FullBodyPose) -> FullBodyPose:
    """Create a deep copy of a full body pose tuple."""
    head, antennas, body_yaw = pose
    return (head.copy(), (float(antennas[0]), float(antennas[1])), float(body_yaw))


@dataclass
class MovementState:
    """State tracking for the movement system."""

    # Primary move state
    current_move: Move | None = None
    move_start_time: float | None = None
    last_activity_time: float = 0.0

    # Status flags
    last_primary_pose: FullBodyPose | None = None

    def update_activity(self) -> None:
        """Update the last activity time."""
        self.last_activity_time = time.monotonic()


@dataclass
class LoopFrequencyStats:
    """Track rolling loop frequency statistics."""

    mean: float = 0.0
    m2: float = 0.0
    min_freq: float = float("inf")
    count: int = 0
    last_freq: float = 0.0
    potential_freq: float = 0.0

    def reset(self) -> None:
        """Reset accumulators while keeping the last potential frequency."""
        self.mean = 0.0
        self.m2 = 0.0
        self.min_freq = float("inf")
        self.count = 0


class MovementManager:
    """Coordinate sequential moves and robot output at 100 Hz.

    Responsibilities:
    - Own a real-time loop that samples the current primary move (if any) and calls
      `set_target` exactly once per tick.
    - Start an idle `BreathingMove` after `idle_inactivity_delay` when not
      listening and no moves are queued.
    - Expose thread-safe APIs so other threads can enqueue moves or mark activity
      without touching internal state.

    Timing:
    - All elapsed-time calculations rely on `time.monotonic()` through `self._now`
      to avoid wall-clock jumps.
    - The loop attempts 100 Hz

    Concurrency:
    - External threads communicate via `_command_queue` messages.
    """

    def __init__(
        self,
        current_robot: ReachyMini,
        speaker_direction: Callable[[], float | None] | None = None,
    ):
        """Initialize movement manager.

        ``speaker_direction`` returns the world yaw recent speech came from, or None; when given,
        the robot turns toward whoever starts talking.
        """
        self.current_robot = current_robot
        self._head_tracking = False
        self._speaker_direction = speaker_direction
        # World yaw the robot faces when idle: toward the last speaker, 0 when facing forward.
        self._gaze_yaw = 0.0
        # Tracking enabled for a conversation (not by the user), so it ends with the conversation.
        self._tracking_auto = False
        # Tracking paused while a speaker turn runs, to resume once the turn ends.
        self._turn_paused_tracking = False
        # Asleep in standby: the sleep pose holds until a wake (activation or a new conversation).
        self._sleeping = False
        self._sleep_hold: PoseTransitionMove | None = None

        # Single timing source for durations
        self._now = time.monotonic

        # Movement state
        self.state = MovementState()
        self.state.last_activity_time = self._now()
        neutral_pose = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
        self.state.last_primary_pose = (neutral_pose, (0.0, 0.0), 0.0)

        # Move queue (primary moves)
        self.move_queue: deque[Move] = deque()

        # Configuration
        self.idle_inactivity_delay = 0.3  # seconds
        self.target_frequency = CONTROL_LOOP_FREQUENCY_HZ
        self.target_period = 1.0 / self.target_frequency

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._is_listening = False
        # Speaking pauses tracking; the captured look-at pose anchors queued moves.
        self._is_speaking = False
        self._track_anchor: NDArray[np.float64] | None = None
        self._track_anchor_body_yaw = 0.0
        self._last_commanded_pose: FullBodyPose = clone_full_body_pose(self.state.last_primary_pose)
        self._listening_antennas: Tuple[float, float] = self._last_commanded_pose[1]
        self._antenna_unfreeze_blend = 1.0
        self._antenna_blend_duration = 0.4  # seconds to blend back after listening
        self._last_listening_blend_time = self._now()
        # Wake cue: the alert antennas hold while listening or until this deadline; None when released.
        self._attentive_until: float | None = None
        self._perk_started_at: float | None = None  # when the antennas began springing to the alert pose
        self._perk_from: Tuple[float, float] = self._last_commanded_pose[1]
        self._breathing_active = False  # true when breathing move is running or queued
        self._listening_debounce_s = 0.15
        self._last_listening_toggle_time = self._now()
        self._last_set_target_err = 0.0
        self._set_target_err_interval = 1.0  # seconds between error logs
        self._set_target_err_suppressed = 0

        # Cross-thread signalling
        self._command_queue: "Queue[Tuple[str, Any]]" = Queue()

        self._shared_state_lock = threading.Lock()
        self._shared_last_activity_time = self.state.last_activity_time
        self._shared_is_listening = self._is_listening
        self._status_lock = threading.Lock()
        self._freq_stats = LoopFrequencyStats()
        self._freq_snapshot = LoopFrequencyStats()

    def queue_move(self, move: Move) -> None:
        """Queue a primary move to run after the currently executing one.

        Thread-safe: the move is enqueued via the worker command queue so the
        control loop remains the sole mutator of movement state.
        """
        self._command_queue.put(("queue_move", move))

    def acknowledge_activation(self) -> None:
        """Queue a relative activation acknowledgment on the movement worker (waking up first if asleep)."""
        self._command_queue.put(("acknowledge_activation", None))

    def go_to_sleep(self) -> None:
        """Lower into the sleep pose and hold it until woken; thread-safe.

        While asleep, other moves and speaker turns are ignored; an activation acknowledgement, a new
        conversation, or ``wake_up`` raises the head first.
        """
        self._command_queue.put(("go_to_sleep", None))

    def wake_up(self) -> None:
        """Rise from the sleep pose if asleep; thread-safe."""
        self._command_queue.put(("wake_up", None))

    def clear_move_queue(self) -> None:
        """Stop the active move and discard any queued primary moves.

        Thread-safe: executed by the worker thread via the command queue.
        """
        self._command_queue.put(("clear_queue", None))

    def set_moving_state(self, duration: float) -> None:
        """Mark the robot as actively moving for the provided duration.

        Legacy hook used by goto helpers to keep inactivity and breathing logic
        aware of manual motions. Thread-safe via the command queue.
        """
        self._command_queue.put(("set_moving_state", duration))

    def is_idle(self) -> bool:
        """Return True when the robot has been inactive longer than the idle delay."""
        with self._shared_state_lock:
            last_activity = self._shared_last_activity_time
            listening = self._shared_is_listening

        if listening:
            return False

        return self._now() - last_activity >= self.idle_inactivity_delay

    def set_listening(self, listening: bool) -> None:
        """Enable or disable listening mode without touching shared state directly.

        While listening:
        - The antennas hold the alert pose, then keep holding it while he works out an answer.
        - Blending is reset so that once released the antennas return smoothly.
        - Idle breathing is suppressed.

        Thread-safe: the change is posted to the worker command queue.
        """
        with self._shared_state_lock:
            if self._shared_is_listening == listening:
                return
        self._command_queue.put(("set_listening", listening))

    def set_head_tracking(self, enabled: bool) -> None:
        """Start or stop following the user's face; thread-safe via the command queue."""
        self._command_queue.put(("set_head_tracking", enabled))

    def turn_toward_speaker(self) -> None:
        """Turn to face where recent speech came from, if it is clearly elsewhere; thread-safe."""
        self._command_queue.put(("turn_toward_speaker", None))

    def set_conversation_active(self, active: bool) -> None:
        """Follow faces during a conversation, then stop and face forward again; thread-safe.

        Tracking the user turned on or off themselves (``set_head_tracking``) is left alone at the end.
        """
        self._command_queue.put(("set_conversation_active", active))

    def set_speaking(self, speaking: bool) -> None:
        """Pause head tracking while the assistant speaks, resume it afterwards.

        On speaking, the current look-at pose is captured as an anchor so queued
        moves stay on the user; when speaking stops the head is handed back to
        daemon tracking. Thread-safe: posted to the worker command queue.
        """
        self._command_queue.put(("set_speaking", speaking))

    def _poll_signals(self, current_time: float) -> None:
        """Apply queued commands."""
        while True:
            try:
                command, payload = self._command_queue.get_nowait()
            except Empty:
                break
            self._handle_command(command, payload, current_time)

    def _handle_command(self, command: str, payload: Any, current_time: float) -> None:
        """Handle a single cross-thread command."""
        if self._sleeping and self._handle_sleeping_command(command, payload):
            return
        if command == "queue_move":
            if isinstance(payload, Move):
                self.move_queue.append(payload)
                self.state.update_activity()
                duration = getattr(payload, "duration", None)
                if duration is not None:
                    try:
                        duration_str = f"{float(duration):.2f}"
                    except (TypeError, ValueError):
                        duration_str = str(duration)
                else:
                    duration_str = "?"
                logger.debug(
                    "Queued move with duration %ss, queue size: %s",
                    duration_str,
                    len(self.move_queue),
                )
            else:
                logger.warning("Ignored queue_move command with invalid payload: %s", payload)
        elif command == "acknowledge_activation":
            with self._status_lock:
                start_pose = clone_full_body_pose(self._last_commanded_pose)
            self.move_queue.append(ActivationAcknowledgementMove(start_pose))
            self._attentive_until = self._now() + ATTENTIVE_HOLD_S
            self.state.update_activity()
            logger.debug("Queued activation acknowledgment move")
        elif command == "clear_queue":
            self.move_queue.clear()
            self._end_move(self.state.current_move)
            self.state.current_move = None
            self.state.move_start_time = None
            self._breathing_active = False
            logger.info("Cleared move queue and stopped current move")
        elif command == "set_moving_state":
            try:
                duration = float(payload)
            except (TypeError, ValueError):
                logger.warning("Invalid moving state duration: %s", payload)
                return
            self.state.update_activity()
        elif command == "mark_activity":
            self.state.update_activity()
        elif command == "set_listening":
            desired_state = bool(payload)
            now = self._now()
            if now - self._last_listening_toggle_time < self._listening_debounce_s:
                return
            self._last_listening_toggle_time = now

            if self._is_listening == desired_state:
                return

            self._is_listening = desired_state
            self._last_listening_blend_time = now
            if desired_state:
                self._request_speaker_turn()
            else:
                # Keep the alert pose while he works out an answer; speaking releases it.
                self._attentive_until = now + ATTENTIVE_HOLD_S
            self._antenna_unfreeze_blend = 0.0
            self.state.update_activity()
        elif command == "set_head_tracking":
            self._tracking_auto = False
            self._set_head_tracking(bool(payload))
        elif command == "turn_toward_speaker":
            self._request_speaker_turn()
        elif command == "set_conversation_active":
            if payload:
                if not self._head_tracking:
                    self._set_head_tracking(True)
                    self._tracking_auto = True
            else:
                self._attentive_until = None
                if self._tracking_auto:
                    self._tracking_auto = False
                    self._set_head_tracking(False)
                if self._gaze_yaw != 0.0:
                    self._queue_gaze_turn(0.0)
        elif command == "go_to_sleep":
            self._fall_asleep()
        elif command == "wake_up":
            pass  # already awake
        elif command == "set_speaking":
            if payload:
                self._attentive_until = None  # answering: drop the alert antennas
            if not self._head_tracking:
                return
            speaking = bool(payload)
            if self._is_speaking == speaking:
                return
            self._is_speaking = speaking
            try:
                if speaking and self.current_robot.get_tracked_face(wait=False).detected:
                    # Pause only once a face is locked, else speech blocks acquisition.
                    anchor_head, _, anchor_body_yaw = self._present_pose()
                    self._track_anchor = np.asarray(anchor_head, dtype=np.float64)
                    self._track_anchor_body_yaw = anchor_body_yaw
                    self.current_robot.start_head_tracking(weight=0.0)
                elif not speaking:
                    self._track_anchor = None
                    self.current_robot.start_head_tracking(weight=1.0)
            except Exception as e:
                logger.warning("Head-tracking speaking handoff failed: %s", e)
        else:
            logger.warning("Unknown command received by MovementManager: %s", command)

    def _handle_sleeping_command(self, command: str, payload: Any) -> bool:
        """Apply a command while asleep; return True when it is fully handled here (worker thread only)."""
        if command in ("acknowledge_activation", "wake_up") or (command == "set_conversation_active" and payload):
            self._wake()
            return command == "wake_up"  # the acknowledgement and conversation start still run, after waking
        if command == "clear_queue":
            # Stopping a conversation keeps the sleep pose, even when it has not started yet.
            self.move_queue = deque(move for move in self.move_queue if move is self._sleep_hold)
            return True
        if command == "set_conversation_active":
            self._tracking_auto = False  # tracking already stopped when he fell asleep; stay facing forward
            return True
        if command in ("queue_move", "turn_toward_speaker", "set_listening", "set_head_tracking", "go_to_sleep"):
            logger.debug("Asleep; ignoring %s", command)
            return True
        return False

    def _fall_asleep(self) -> None:
        """Stop tracking and other moves, then lower into the sleep pose (worker thread only)."""
        self._tracking_auto = False
        self._set_head_tracking(False)
        self.move_queue.clear()
        self._end_move(self.state.current_move)
        self.state.current_move = None
        self.state.move_start_time = None
        self._breathing_active = False
        self._is_listening = False
        self._attentive_until = None
        self._gaze_yaw = 0.0
        self._sleeping = True
        self._sleep_hold = sleep_move()
        self.move_queue.append(self._sleep_hold)
        self.state.update_activity()
        logger.info("Going to sleep until someone says my name")

    def _wake(self) -> None:
        """Leave the sleep pose for the neutral pose (worker thread only)."""
        self._sleeping = False
        self._sleep_hold = None
        self.move_queue.clear()
        self._end_move(self.state.current_move)
        self.state.current_move = None
        self.state.move_start_time = None
        self.move_queue.append(wake_move())
        self.state.update_activity()
        logger.info("Waking up")

    def _set_head_tracking(self, enabled: bool) -> None:
        """Start or stop daemon face tracking (worker thread only)."""
        if self._head_tracking == enabled:
            return
        self._head_tracking = enabled
        self._track_anchor = None
        self._turn_paused_tracking = False
        # set_speaking is gated off while disabled, so its state would go stale across a toggle.
        self._is_speaking = False
        try:
            if enabled:
                self.current_robot.start_head_tracking(weight=1.0)
            else:
                self.current_robot.stop_head_tracking()
        except Exception as e:
            logger.warning("Head-tracking toggle failed: %s", e)
            return
        if not enabled:
            # Tracking left the head wherever the face was; ease back to the idle gaze instead of snapping.
            self._queue_gaze_turn(self._gaze_yaw)

    def _request_speaker_turn(self) -> None:
        """Queue a turn toward recent speech when it came from clearly elsewhere (worker thread only)."""
        if self._speaker_direction is None:
            return
        try:
            yaw = self._speaker_direction()
        except Exception as e:
            logger.debug("Speaker direction failed: %s", e)
            return
        if yaw is None:
            return
        try:
            facing = head_world_yaw(np.asarray(self.current_robot.get_current_head_pose(), dtype=np.float64))
        except Exception as e:
            logger.debug("Speaker turn skipped; head pose unavailable: %s", e)
            return
        if abs(yaw - facing) < SPEAKER_TURN_MIN_RAD:
            return
        logger.info("Turning toward speaker: %.0f deg (facing %.0f deg)", np.degrees(yaw), np.degrees(facing))
        self._queue_gaze_turn(yaw)

    def _queue_gaze_turn(self, yaw: float) -> None:
        """Queue a turn to ``yaw``, replacing any turn that has not started yet."""
        self.move_queue = deque(move for move in self.move_queue if not isinstance(move, GazeTurnMove))
        self.move_queue.append(GazeTurnMove(yaw))
        self.state.update_activity()

    def _present_pose(self) -> FullBodyPose:
        """Return the robot's measured head pose and body yaw, falling back to the last command."""
        with self._status_lock:
            commanded = clone_full_body_pose(self._last_commanded_pose)
        try:
            head = np.asarray(self.current_robot.get_current_head_pose(), dtype=np.float64)
            head_joints, _ = self.current_robot.get_current_joint_positions()
            return (head, commanded[1], float(head_joints[0]))
        except Exception as e:
            logger.debug("Present pose unavailable; using the last command: %s", e)
            return commanded

    def _begin_move(self, move: Move) -> None:
        """Prepare a move that has just become current."""
        if isinstance(move, GazeTurnMove):
            # A turn pauses tracking so the head can leave the current face, and starts from where the head is.
            if self._head_tracking and not self._turn_paused_tracking:
                try:
                    self.current_robot.start_head_tracking(weight=0.0)
                    self._turn_paused_tracking = True
                except Exception as e:
                    logger.warning("Head-tracking pause for speaker turn failed: %s", e)
            self._track_anchor = None
            move.begin(self._present_pose())
            self._gaze_yaw = move.target_yaw
        elif isinstance(move, GotoQueueMove):
            # Explicit head moves (move_head, sweep_look) aim in absolute terms and end facing forward.
            self._gaze_yaw = 0.0
        elif isinstance(move, PoseTransitionMove):
            # Sleeping and waking start from wherever the head is and end facing forward.
            move.begin(self._present_pose())
            self._gaze_yaw = 0.0

    def _end_move(self, move: Move | None) -> None:
        """Clean up after a move finishes or is cleared."""
        if isinstance(move, GazeTurnMove) and self._turn_paused_tracking:
            self._turn_paused_tracking = False
            if self._head_tracking:
                try:
                    self.current_robot.start_head_tracking(weight=1.0)
                except Exception as e:
                    logger.warning("Head-tracking resume after speaker turn failed: %s", e)

    def _publish_shared_state(self) -> None:
        """Expose idle-related state for external threads."""
        with self._shared_state_lock:
            self._shared_last_activity_time = self.state.last_activity_time
            self._shared_is_listening = self._is_listening

    def _manage_move_queue(self, current_time: float) -> None:
        """Manage the primary move queue (sequential execution)."""
        if self.state.current_move is None or (
            self.state.move_start_time is not None
            and current_time - self.state.move_start_time >= self.state.current_move.duration
        ):
            self._end_move(self.state.current_move)
            self.state.current_move = None
            self.state.move_start_time = None

            if self.move_queue:
                self.state.current_move = self.move_queue.popleft()
                self.state.move_start_time = current_time
                self._begin_move(self.state.current_move)
                # Any real move cancels breathing mode flag
                self._breathing_active = isinstance(self.state.current_move, BreathingMove)
                logger.debug(f"Starting new move, duration: {self.state.current_move.duration}s")

    def _manage_breathing(self, current_time: float) -> None:
        """Manage automatic breathing when idle."""
        if (
            self.state.current_move is None
            and not self.move_queue
            and not self._is_listening
            and not self._breathing_active
        ):
            idle_for = current_time - self.state.last_activity_time
            if idle_for >= self.idle_inactivity_delay:
                try:
                    # These 2 functions return the latest available sensor data from the robot, but don't perform I/O synchronously.
                    # Therefore, we accept calling them inside the control loop.
                    _, current_antennas = self.current_robot.get_current_joint_positions()
                    current_head_pose = self.current_robot.get_current_head_pose()

                    self._breathing_active = True
                    self.state.update_activity()

                    breathing_move = BreathingMove(
                        interpolation_start_pose=current_head_pose,
                        interpolation_start_antennas=current_antennas,
                        interpolation_duration=1.0,
                        base_yaw=self._gaze_yaw,
                    )
                    self.move_queue.append(breathing_move)
                    logger.debug("Started breathing after %.1fs of inactivity", idle_for)
                except Exception as e:
                    self._breathing_active = False
                    logger.error("Failed to start breathing: %s", e)

        if isinstance(self.state.current_move, BreathingMove) and self.move_queue:
            self.state.current_move = None
            self.state.move_start_time = None
            self._breathing_active = False
            logger.debug("Stopping breathing due to new move activity")

        if self.state.current_move is not None and not isinstance(self.state.current_move, BreathingMove):
            self._breathing_active = False

    def _get_primary_pose(self, current_time: float) -> FullBodyPose:
        """Get the primary full body pose from current move or neutral."""
        # When a primary move is playing, sample it and cache the resulting pose
        if self.state.current_move is not None and self.state.move_start_time is not None:
            move_time = current_time - self.state.move_start_time
            head, antennas, body_yaw = self.state.current_move.evaluate(move_time)

            if head is None:
                head = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
            if antennas is None:
                antennas = np.array([-0.1745, 0.1745])  # ~10° offset
            if body_yaw is None:
                body_yaw = 0.0

            move = self.state.current_move
            anchored_emotion = self._track_anchor is not None and isinstance(move, EmotionQueueMove)
            if self._gaze_yaw != 0.0 and isinstance(move, (EmotionQueueMove, DanceQueueMove)) and not anchored_emotion:
                # Emotions and dances are recorded facing forward; play them toward the speaker.
                body_yaw = float(body_yaw) + self._gaze_yaw
                head = compose_world_offset(head, yaw_offset(self._gaze_yaw))

            antennas_tuple = (float(antennas[0]), float(antennas[1]))
            head_copy = head.copy()
            primary_full_body_pose = (
                head_copy,
                antennas_tuple,
                float(body_yaw),
            )

            self.state.last_primary_pose = clone_full_body_pose(primary_full_body_pose)
        # Otherwise reuse the last primary pose so we avoid jumps between moves
        elif self.state.last_primary_pose is not None:
            primary_full_body_pose = clone_full_body_pose(self.state.last_primary_pose)
        else:
            neutral_head_pose = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
            primary_full_body_pose = (neutral_head_pose, (0.0, 0.0), 0.0)
            self.state.last_primary_pose = clone_full_body_pose(primary_full_body_pose)

        # Speaking pauses tracking: hold the look-at anchor (breathing included), overlay emotions on it,
        # dance from neutral. The body holds where it was too, so neither swings back to forward.
        if self._track_anchor is not None:
            head_pose, antennas, body_yaw = primary_full_body_pose
            move = self.state.current_move
            if move is None:
                head_pose = self._track_anchor.copy()
                body_yaw = self._track_anchor_body_yaw
            elif isinstance(move, BreathingMove):
                head_pose = self._track_anchor.copy()
                start = self.state.move_start_time
                if start is not None and current_time - start >= move.interpolation_duration:
                    # Keep the gentle breathing bob on top of the anchor.
                    head_pose[2, 3] += head_pose_z_bob(move, current_time - start)
                body_yaw = self._track_anchor_body_yaw
            elif isinstance(move, EmotionQueueMove):
                head_pose = compose_world_offset(self._track_anchor, head_pose)
                body_yaw = self._track_anchor_body_yaw + float(body_yaw)
            primary_full_body_pose = (head_pose, antennas, body_yaw)

        return primary_full_body_pose

    def _update_primary_motion(self, current_time: float) -> None:
        """Advance queue state and idle behaviours for this tick."""
        self._manage_move_queue(current_time)
        self._manage_breathing(current_time)

    def _calculate_blended_antennas(self, target_antennas: Tuple[float, float]) -> Tuple[float, float]:
        """Hold the alert antennas while attentive, else blend back to the target antennas."""
        now = self._now()
        if self._attentive_until is not None and now >= self._attentive_until:
            self._attentive_until = None
        listening = self._is_listening or self._attentive_until is not None
        listening_antennas = self._listening_antennas
        blend = self._antenna_unfreeze_blend
        blend_duration = self._antenna_blend_duration
        last_update = self._last_listening_blend_time
        self._last_listening_blend_time = now

        if listening:
            antennas_cmd = self._perk_antennas(now, max(0.0, now - last_update))
            self._listening_antennas = antennas_cmd  # releasing blends back from wherever the perk is
            new_blend = 0.0
        else:
            self._perk_started_at = None
            dt = max(0.0, now - last_update)
            if blend_duration <= 0:
                new_blend = 1.0
            else:
                new_blend = min(1.0, blend + dt / blend_duration)
            antennas_cmd = (
                listening_antennas[0] * (1.0 - new_blend) + target_antennas[0] * new_blend,
                listening_antennas[1] * (1.0 - new_blend) + target_antennas[1] * new_blend,
            )

        if listening:
            self._antenna_unfreeze_blend = 0.0
        else:
            self._antenna_unfreeze_blend = new_blend
            if new_blend >= 1.0:
                self._listening_antennas = (
                    float(target_antennas[0]),
                    float(target_antennas[1]),
                )

        return antennas_cmd

    def _perk_antennas(self, now: float, dt: float) -> Tuple[float, float]:
        """Spring from where the antennas were toward the alert pose, speed-capped (worker thread only)."""
        with self._status_lock:
            previous = self._last_commanded_pose[1]
        if self._perk_started_at is None:
            self._perk_started_at = now
            self._perk_from = (float(previous[0]), float(previous[1]))
        t = now - self._perk_started_at
        reach = 1.0 - np.exp(-t / PERK_DECAY_S) * np.cos(2 * np.pi * PERK_FREQUENCY_HZ * t)
        max_step = PERK_MAX_SPEED_RAD_S * dt
        left, right = (
            float(np.clip(start + (alert - start) * reach, last - max_step, last + max_step))
            for start, alert, last in zip(self._perk_from, ATTENTIVE_ANTENNAS, previous)
        )
        return left, right

    def _issue_control_command(
        self, head: NDArray[np.float32], antennas: Tuple[float, float], body_yaw: float
    ) -> None:
        """Send the pose to the robot with throttled error logging."""
        try:
            self.current_robot.set_target(head=head, antennas=antennas, body_yaw=body_yaw)
        except Exception as e:
            now = self._now()
            if now - self._last_set_target_err >= self._set_target_err_interval:
                msg = f"Failed to set robot target: {e}"
                if self._set_target_err_suppressed:
                    msg += f" (suppressed {self._set_target_err_suppressed} repeats)"
                    self._set_target_err_suppressed = 0
                logger.error(msg)
                self._last_set_target_err = now
            else:
                self._set_target_err_suppressed += 1
        else:
            with self._status_lock:
                self._last_commanded_pose = clone_full_body_pose((head, antennas, body_yaw))

    def _update_frequency_stats(
        self,
        loop_start: float,
        prev_loop_start: float,
        stats: LoopFrequencyStats,
    ) -> LoopFrequencyStats:
        """Update frequency statistics based on the current loop start time."""
        period = loop_start - prev_loop_start
        if period > 0:
            stats.last_freq = 1.0 / period
            stats.count += 1
            delta = stats.last_freq - stats.mean
            stats.mean += delta / stats.count
            stats.m2 += delta * (stats.last_freq - stats.mean)
            stats.min_freq = min(stats.min_freq, stats.last_freq)
        return stats

    def _schedule_next_tick(self, loop_start: float, stats: LoopFrequencyStats) -> Tuple[float, LoopFrequencyStats]:
        """Compute sleep time to maintain target frequency and update potential freq."""
        computation_time = self._now() - loop_start
        stats.potential_freq = 1.0 / computation_time if computation_time > 0 else float("inf")
        sleep_time = max(0.0, self.target_period - computation_time)
        return sleep_time, stats

    def _record_frequency_snapshot(self, stats: LoopFrequencyStats) -> None:
        """Store a thread-safe snapshot of current frequency statistics."""
        with self._status_lock:
            self._freq_snapshot = LoopFrequencyStats(
                mean=stats.mean,
                m2=stats.m2,
                min_freq=stats.min_freq,
                count=stats.count,
                last_freq=stats.last_freq,
                potential_freq=stats.potential_freq,
            )

    def _maybe_log_frequency(self, loop_count: int, print_interval_loops: int, stats: LoopFrequencyStats) -> None:
        """Emit frequency telemetry when enough loops have elapsed."""
        if loop_count % print_interval_loops != 0 or stats.count == 0:
            return

        variance = stats.m2 / stats.count if stats.count > 0 else 0.0
        lowest = stats.min_freq if stats.min_freq != float("inf") else 0.0
        logger.debug(
            "Loop freq - avg: %.2fHz, variance: %.4f, min: %.2fHz, last: %.2fHz, potential: %.2fHz, target: %.1fHz",
            stats.mean,
            variance,
            lowest,
            stats.last_freq,
            stats.potential_freq,
            self.target_frequency,
        )
        stats.reset()

    def start(self) -> None:
        """Start the worker thread that drives the 100 Hz control loop."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Move worker already running; start() ignored")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self.working_loop, daemon=True)
        self._thread.start()
        logger.debug("Move worker started")

    def stop(self, reset_to_neutral: bool = True) -> None:
        """Request the worker thread to stop and wait for it to exit.

        Optionally resets the robot to a neutral position after stopping.
        """
        if self._thread is None or not self._thread.is_alive():
            logger.debug("Move worker not running; stop() ignored")
            return

        if reset_to_neutral:
            logger.info("Stopping movement manager and resetting to neutral position...")
        else:
            logger.info("Stopping movement manager...")

        # Clear any queued moves and stop current move
        self.clear_move_queue()

        # Stop the worker thread first so it doesn't interfere
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        logger.debug("Move worker stopped")

        if self._head_tracking:
            try:
                self.current_robot.stop_head_tracking()
            except Exception as e:
                logger.warning("Failed to stop head tracking: %s", e)

        if not reset_to_neutral:
            return

        # Reset to neutral position using goto_target (same approach as wake_up)
        try:
            neutral_head_pose = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
            neutral_antennas = [-0.1745, 0.1745]  # ~10° offset to reduce shaking
            neutral_body_yaw = 0.0

            # Use goto_target directly on the robot
            self.current_robot.goto_target(
                head=neutral_head_pose,
                antennas=neutral_antennas,
                duration=2.0,
                body_yaw=neutral_body_yaw,
            )

            logger.info("Reset to neutral position completed")

        except Exception as e:
            logger.error(f"Failed to reset to neutral position: {e}")

    def get_status(self) -> Dict[str, Any]:
        """Return a lightweight status snapshot for observability."""
        with self._status_lock:
            pose_snapshot = clone_full_body_pose(self._last_commanded_pose)
            freq_snapshot = LoopFrequencyStats(
                mean=self._freq_snapshot.mean,
                m2=self._freq_snapshot.m2,
                min_freq=self._freq_snapshot.min_freq,
                count=self._freq_snapshot.count,
                last_freq=self._freq_snapshot.last_freq,
                potential_freq=self._freq_snapshot.potential_freq,
            )

        head_matrix = pose_snapshot[0].tolist() if pose_snapshot else None
        antennas = pose_snapshot[1] if pose_snapshot else None
        body_yaw = pose_snapshot[2] if pose_snapshot else None

        return {
            "queue_size": len(self.move_queue),
            "is_listening": self._is_listening,
            "sleeping": self._sleeping,
            "breathing_active": self._breathing_active,
            "last_commanded_pose": {
                "head": head_matrix,
                "antennas": antennas,
                "body_yaw": body_yaw,
            },
            "loop_frequency": {
                "last": freq_snapshot.last_freq,
                "mean": freq_snapshot.mean,
                "min": freq_snapshot.min_freq,
                "potential": freq_snapshot.potential_freq,
                "samples": freq_snapshot.count,
            },
        }

    def working_loop(self) -> None:
        """Run the primary-move control loop with a single set_target() call per tick."""
        logger.debug("Starting enhanced movement control loop (100Hz)")

        loop_count = 0
        prev_loop_start = self._now()
        print_interval_loops = max(1, int(self.target_frequency * 2))
        freq_stats = self._freq_stats

        while not self._stop_event.is_set():
            loop_start = self._now()
            loop_count += 1

            if loop_count > 1:
                freq_stats = self._update_frequency_stats(loop_start, prev_loop_start, freq_stats)
            prev_loop_start = loop_start

            # 1) Poll external commands
            self._poll_signals(loop_start)

            # 2) Manage the primary move queue (start new move, end finished move, breathing)
            self._update_primary_motion(loop_start)

            # 3) Build the primary full-body pose for this tick
            head, antennas, body_yaw = self._get_primary_pose(loop_start)

            # 4) Apply listening antenna freeze or blend-back
            antennas_cmd = self._calculate_blended_antennas(antennas)

            # 5) Single set_target call - the only control point
            self._issue_control_command(head, antennas_cmd, body_yaw)

            # 6) Adaptive sleep to align to next tick, then publish shared state
            sleep_time, freq_stats = self._schedule_next_tick(loop_start, freq_stats)
            self._publish_shared_state()
            self._record_frequency_snapshot(freq_stats)

            # 7) Periodic telemetry on loop frequency
            self._maybe_log_frequency(loop_count, print_interval_loops, freq_stats)

            if sleep_time > 0:
                time.sleep(sleep_time)

        logger.debug("Movement control loop stopped")
