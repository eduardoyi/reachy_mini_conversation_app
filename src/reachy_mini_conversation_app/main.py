"""Entrypoint for the Reachy Mini conversation app."""

from __future__ import annotations
import sys
import time
import asyncio
import logging
import argparse
import importlib
import threading
from typing import TYPE_CHECKING, Any, Optional
from pathlib import Path
from collections.abc import Callable, Awaitable

from fastapi import FastAPI, Request, Response

from reachy_mini import ReachyMini, ReachyMiniApp
from reachy_mini_conversation_app import app_lifecycle
from reachy_mini_conversation_app.utils import (
    parse_args,
    setup_logger,
    log_connection_troubleshooting,
)
from reachy_mini_conversation_app.wake_clips import WakeClipRecorder, wake_clips_directory
from reachy_mini_conversation_app.acoustic_wake_word import OpenWakeWordDetector


if TYPE_CHECKING:
    from reachy_mini_conversation_app.console import LocalStream
    from reachy_mini_conversation_app.hybrid_runtime import HybridRuntime
    from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
    from reachy_mini_conversation_app.conversation_handler import ConversationHandler


def _build_home_devices() -> Any:
    """Merge the configured light systems (IKEA hub, Tuya), or return None when there are none."""
    from reachy_mini_conversation_app.config import config
    from reachy_mini_conversation_app.home_devices import HomeLayout, HomeDevices
    from reachy_mini_conversation_app.tuya_devices import TuyaHome

    log = logging.getLogger(__name__)
    providers: dict[str, Any] = {}
    if config.DIRIGERA_HOST and config.DIRIGERA_TOKEN:
        from reachy_mini_conversation_app.dirigera_hub import DirigeraHub

        providers["dirigera"] = DirigeraHub(config.DIRIGERA_HOST, config.DIRIGERA_TOKEN)
    tuya = TuyaHome(Path(config.TUYA_DEVICES_FILE))
    if tuya.configured:
        tuya.start_listening()
        providers["tuya"] = tuya
    if not providers:
        return None
    log.info("Home lights available from %s", ", ".join(providers))
    return HomeDevices(providers, HomeLayout.load(Path(config.HOME_LAYOUT_FILE)))


def _build_tv() -> Any:
    """Return the paired TV, or None when there is none."""
    from reachy_mini_conversation_app.config import config
    from reachy_mini_conversation_app.android_tv import AndroidTV, is_paired

    if not config.ANDROID_TV_HOST or not is_paired(Path(config.ANDROID_TV_CERT_DIR)):
        return None
    logging.getLogger(__name__).info("TV available at %s", config.ANDROID_TV_HOST)
    return AndroidTV(config.ANDROID_TV_HOST, Path(config.ANDROID_TV_CERT_DIR))


def _build_display() -> Any:
    """Return the TRMNL e-ink display, or None when TRMNL_ENABLED is off."""
    from reachy_mini_conversation_app.config import config

    if not config.TRMNL_ENABLED:
        return None
    from google import genai
    from openai import AsyncOpenAI

    from reachy_mini_conversation_app.eink_display import OPENAI_IMAGE_PREFIX, EInkDisplay, render_photo
    from reachy_mini_conversation_app.web_pictures import WebPictures

    api_key = (config.GEMINI_API_KEY or "").strip()
    openai_key = (config.OPENAI_API_KEY or "").strip()
    uses_openai = config.DISPLAY_IMAGE_MODEL.startswith(OPENAI_IMAGE_PREFIX)
    pictures = None
    if config.BRAVE_API_KEY and config.TYPESAFE_API_KEY:
        pictures = WebPictures(
            brave_api_key=config.BRAVE_API_KEY,
            typesafe_api_key=config.TYPESAFE_API_KEY,
            check_model=config.DISPLAY_CHECK_MODEL,
            screen_preview=render_photo,
        )
    return EInkDisplay(
        port=config.TRMNL_PORT,
        refresh_s=config.TRMNL_REFRESH_SECONDS,
        cache_dir=Path.home() / ".cache" / "reachy-mini-conversation-app" / "display",
        image_model=config.DISPLAY_IMAGE_MODEL,
        client=genai.Client(api_key=api_key) if api_key and not uses_openai else None,
        openai_client=AsyncOpenAI(api_key=openai_key, max_retries=0) if openai_key and uses_openai else None,
        pictures=pictures,
    )


def _build_now_playing(display: Any) -> Any:
    """Return the watcher that shows Spotify songs on the e-ink screen, or None when it is off or not set up."""
    from reachy_mini_conversation_app.config import config

    if display is None or not config.DISPLAY_NOW_PLAYING:
        return None
    from reachy_mini_conversation_app.now_playing import NowPlaying, spotify_fetcher
    from reachy_mini_conversation_app.tools.spotify_control import SPOTIFY_DUCKER, SpotifyDucker

    spotify = SpotifyDucker._configuration()
    if spotify is None:
        return None
    credentials_path, device_id = spotify
    now_playing = NowPlaying(
        display,
        spotify_fetcher(credentials_path, device_id),
        in_conversation=lambda: SPOTIFY_DUCKER.in_conversation,
        art_dir=display.spool.parent / "album-art",
    )
    SPOTIFY_DUCKER.playback_changed_callback = now_playing.refresh_soon
    return now_playing


def _build_quick_commands(deps: Any) -> tuple[Any, Any]:
    """Return the quick-command handler and its reply voice, or (None, None) when disabled."""
    from reachy_mini_conversation_app.config import config

    if not config.QUICK_COMMANDS_ENABLED:
        return None, None
    from reachy_mini_conversation_app.weather import Place, WeatherService
    from reachy_mini_conversation_app.local_speech import LocalSpeech
    from reachy_mini_conversation_app.quick_commands import FIXED_PHRASES, QuickCommands

    home = None
    if config.HOME_LOCATION and config.HOME_LATITUDE is not None and config.HOME_LONGITUDE is not None:
        home = Place(config.HOME_LOCATION, config.HOME_LATITUDE, config.HOME_LONGITUDE)
    weather = WeatherService(home, home_name=config.HOME_LOCATION) if config.HOME_LOCATION else None
    speech = LocalSpeech(
        (config.GEMINI_API_KEY or "").strip() or None,
        config.GEMINI_VOICE,
        model=config.LOCAL_SPEECH_MODEL,
        cacheable=FIXED_PHRASES,
    )
    logging.getLogger(__name__).info(
        "Quick commands enabled (lights, music, TV, time, weather%s)", "" if weather else "; no HOME_LOCATION"
    )
    return QuickCommands(deps, weather=weather).handle, speech.stream


def _start_inactivity_timeout_thread(
    timeout_minutes: float,
    stream_manager: LocalStream,
    logger: logging.Logger,
    app_stop_event: threading.Event | None,
    go_to_sleep: Callable[[], dict[str, Any]] | None = None,
) -> threading.Thread:
    """Start a daemon that puts the app to sleep after inactivity."""
    timeout_seconds = timeout_minutes * 60.0

    def poll_inactivity_timeout() -> None:
        logger.info("App inactivity timeout enabled: %.1f minutes.", timeout_minutes)
        while app_stop_event is None or not app_stop_event.is_set():
            elapsed = stream_manager.seconds_since_activity()
            if elapsed >= timeout_seconds:
                logger.info("No activity for %.1f minutes; going to sleep.", elapsed / 60.0)
                try:
                    if go_to_sleep is not None:
                        go_to_sleep()
                    else:
                        stream_manager.close()
                except Exception as e:
                    logger.error("Error while going to sleep after inactivity timeout: %s", e)
                    try:
                        stream_manager.close()
                    except Exception as close_error:
                        logger.error("Error while closing stream manager after inactivity timeout: %s", close_error)
                return
            time.sleep(1.0)

    thread = threading.Thread(target=poll_inactivity_timeout, daemon=True)
    thread.start()
    return thread


def _build_conversation_handler(
    deps: ToolDependencies,
    realtime_provider: str,
    instance_path: str | None,
    startup_voice: str | None,
    logger: logging.Logger,
) -> ConversationHandler:
    """Build the selected realtime handler without importing unused providers."""
    from reachy_mini_conversation_app.config import (
        HF_LOCAL_CONNECTION_MODE,
        REALTIME_PROVIDER_GEMINI,
        REALTIME_PROVIDER_HUGGINGFACE,
        REALTIME_PROVIDER_OPENAI_LIVE,
        get_hf_connection_selection,
    )

    if realtime_provider == REALTIME_PROVIDER_GEMINI:
        from reachy_mini_conversation_app.gemini_live import GeminiLiveHandler

        logger.info("Using Gemini Live realtime handler")
        return GeminiLiveHandler(
            deps,
            instance_path=instance_path,
            startup_voice=startup_voice,
        )

    if realtime_provider == REALTIME_PROVIDER_OPENAI_LIVE:
        from reachy_mini_conversation_app.openai_live import OpenAILiveHandler

        logger.info("Using OpenAI Live realtime handler")
        return OpenAILiveHandler(
            deps,
            instance_path=instance_path,
            startup_voice=startup_voice,
        )

    if realtime_provider != REALTIME_PROVIDER_HUGGINGFACE:
        raise ValueError(f"Unsupported realtime provider: {realtime_provider!r}.")

    from reachy_mini_conversation_app.huggingface_realtime import HuggingFaceRealtimeHandler

    hf_connection_selection = get_hf_connection_selection()
    transport_label = (
        "Hugging Face direct websocket"
        if hf_connection_selection.mode == HF_LOCAL_CONNECTION_MODE and hf_connection_selection.has_target
        else "Hugging Face session proxy"
    )
    logger.info("Using Hugging Face realtime handler (%s)", transport_label)
    return HuggingFaceRealtimeHandler(
        deps,
        instance_path=instance_path,
        startup_voice=startup_voice,
    )


def main() -> None:
    """Entrypoint for the Reachy Mini conversation app."""
    args, _ = parse_args()
    if args.command == "tool-spaces":
        from reachy_mini_conversation_app.tool_spaces import handle_tool_spaces_command

        logger = setup_logger(args.debug)
        try:
            raise SystemExit(handle_tool_spaces_command(args))
        except Exception as exc:
            logger.error("tool-spaces command failed: %s", exc)
            raise SystemExit(1) from exc
    run(args)


def run(
    args: argparse.Namespace,
    robot: ReachyMini = None,
    app_stop_event: Optional[threading.Event] = None,
    settings_app: Optional[FastAPI] = None,
    instance_path: Optional[str] = None,
) -> None:
    """Run the Reachy Mini conversation app."""
    # Putting these dependencies here makes the dashboard faster to load when the conversation app is installed
    from reachy_mini_conversation_app.moves import MovementManager
    from reachy_mini_conversation_app.config import (
        REALTIME_PROVIDER_GEMINI,
        REALTIME_PROVIDER_OPENAI_LIVE,
        config,
        set_instance_path,
        get_realtime_provider,
        get_hf_connection_selection,
        resolve_app_timeout_minutes,
        refresh_runtime_config_from_env,
    )
    from reachy_mini_conversation_app.startup_settings import (
        StartupSettings,
        load_startup_settings_into_runtime,
    )

    logger = setup_logger(args.debug)
    logger.info("Starting Reachy Mini Conversation App")
    set_instance_path(instance_path)
    startup_settings = StartupSettings()

    if instance_path is not None:
        try:
            from dotenv import load_dotenv

            env_path = Path(instance_path) / ".env"
            if env_path.exists():
                load_dotenv(dotenv_path=str(env_path), override=True)
                refresh_runtime_config_from_env()
                logger.info("Loaded instance configuration from %s", env_path)
        except Exception as e:
            logger.warning("Failed to load instance configuration: %s", e)

        try:
            startup_settings = load_startup_settings_into_runtime(instance_path)
        except Exception as e:
            logger.warning("Failed to load startup settings: %s", e)

    acoustic_wake_detector: OpenWakeWordDetector | None = None
    wake_clips: WakeClipRecorder | None = None
    if config.LOCAL_ACOUSTIC_WAKE_ENABLED:
        if not config.HYBRID_RUNTIME_ENABLED:
            raise ValueError("Acoustic wake requires HYBRID_RUNTIME_ENABLED=1")
        if not config.LOCAL_ACOUSTIC_WAKE_MODEL_DIR:
            raise ValueError("Acoustic wake requires LOCAL_ACOUSTIC_WAKE_MODEL_DIR")
        if config.WAKE_CLIPS_ENABLED:
            wake_clips = WakeClipRecorder(wake_clips_directory(), keep_days=config.WAKE_CLIPS_KEEP_DAYS)
            logger.info("Wake clips enabled: saving locally for %d days", config.WAKE_CLIPS_KEEP_DAYS)
        acoustic_wake_detector = OpenWakeWordDetector.from_directory(
            Path(config.LOCAL_ACOUSTIC_WAKE_MODEL_DIR).expanduser(),
            threshold=config.LOCAL_ACOUSTIC_WAKE_THRESHOLD,
            event_observer=wake_clips.save_wake_event if wake_clips is not None else None,
        )
        logger.info(
            "Supervised official33 acoustic wake enabled; threshold=%.2f cooldown=3s",
            config.LOCAL_ACOUSTIC_WAKE_THRESHOLD,
        )
    elif config.WAKE_CLIPS_ENABLED:
        logger.warning("WAKE_CLIPS_ENABLED needs LOCAL_ACOUSTIC_WAKE_ENABLED=1; no wake clips are saved")

    realtime_provider = get_realtime_provider()
    if realtime_provider == REALTIME_PROVIDER_GEMINI:
        logger.info("Configured Gemini Live realtime backend")
    elif realtime_provider == REALTIME_PROVIDER_OPENAI_LIVE:
        logger.info("Configured OpenAI Live realtime backend")
        # Load lazy SDK modules before microphone capture can be stalled by imports.
        importlib.import_module("reachy_mini_conversation_app.openai_live")
        importlib.import_module("openai.resources.responses")
    else:
        logger.info(
            "Configured Hugging Face realtime backend, connection mode: %s",
            get_hf_connection_selection().mode,
        )

    from reachy_mini_conversation_app.console import LocalStream
    from reachy_mini_conversation_app.tools.core_tools import ToolDependencies

    if robot is None:
        try:
            media_backend = getattr(args, "media_backend", "default")
            robot_kwargs: dict[str, Any] = {"media_backend": media_backend}
            if args.robot_name is not None:
                robot_kwargs["robot_name"] = args.robot_name

            logger.info("Initializing ReachyMini (media backend: %s)", media_backend)
            robot = ReachyMini(**robot_kwargs)

        except TimeoutError as e:
            logger.error(f"Connection timeout: Failed to connect to Reachy Mini daemon. Details: {e}")
            log_connection_troubleshooting(logger, args.robot_name)
            sys.exit(1)

        except ConnectionError as e:
            logger.error(f"Connection failed: Unable to establish connection to Reachy Mini. Details: {e}")
            log_connection_troubleshooting(logger, args.robot_name)
            sys.exit(1)

        except Exception as e:
            logger.error(f"Unexpected error during robot initialization: {type(e).__name__}: {e}")
            logger.error("Please check your configuration and try again.")
            sys.exit(1)

    # Restarted while asleep (e.g. after "good night"): stay asleep, so only his name wakes him and
    # the acoustic detector firing on ordinary speech does not open a conversation.
    started_asleep = app_lifecycle.wake_up_if_sleeping(robot, logger, stay_asleep=config.HYBRID_RUNTIME_ENABLED)
    started_asleep = started_asleep and config.HYBRID_RUNTIME_ENABLED

    speaker_direction = None
    if config.SPEAKER_FOLLOWING_ENABLED:
        from reachy_mini_conversation_app.moves import head_world_yaw
        from reachy_mini_conversation_app.speaker_direction import SpeakerDirection

        client = getattr(robot, "client", None)
        speaker_direction = SpeakerDirection(
            f"http://{getattr(client, 'host', 'localhost')}:{getattr(client, 'port', 8000)}",
            # The mic array is in the head, so its directions are relative to where the head faces.
            head_yaw=lambda: head_world_yaw(robot.get_current_head_pose()),
        )
    movement_manager = MovementManager(
        current_robot=robot,
        speaker_direction=speaker_direction.recent_speech_yaw if speaker_direction is not None else None,
    )
    if started_asleep:
        movement_manager.go_to_sleep()

    face_service = None
    if config.FACE_RECOGNITION_ENABLED and not args.no_camera:
        from reachy_mini_conversation_app.camera_frames import encode_jpeg, decode_image, capture_frame
        from reachy_mini_conversation_app.face_identity import FaceRegistry, FaceRecognitionService
        from reachy_mini_conversation_app.household_memory import memory_path_for_instance

        face_service = FaceRecognitionService(
            lambda: capture_frame(robot.media),
            FaceRegistry(memory_path_for_instance(instance_path).parent / "faces"),
            encode_jpeg=encode_jpeg,
            decode_image=decode_image,
        )

    def current_person_id() -> str | None:
        """Return who the camera recognizes in front of the robot, if anyone."""
        seen = face_service.person_in_front() if face_service is not None else None
        return seen.match.person_id if seen is not None else None

    deps = ToolDependencies(
        reachy_mini=robot,
        movement_manager=movement_manager,
        instance_path=instance_path,
        camera_enabled=not args.no_camera,
        face_recognition=face_service,
        current_person_id=current_person_id if face_service is not None else None,
    )
    deps.home_hub = _build_home_devices()
    deps.tv = _build_tv()
    deps.display = _build_display()
    deps.now_playing = _build_now_playing(deps.display)

    def build_handler(startup_voice: Optional[str] = None) -> ConversationHandler:
        """Build the selected realtime handler for the current runtime config."""
        handler = _build_conversation_handler(
            deps,
            get_realtime_provider(),
            instance_path=instance_path,
            startup_voice=startup_voice,
            logger=logger,
        )
        if face_service is not None:
            handler.set_identity_hint_provider(face_service.identity_hint)
        return handler

    stream_manager: LocalStream | None = None
    hybrid_runtime: HybridRuntime | None = None
    if config.HYBRID_RUNTIME_ENABLED:
        from reachy_mini_conversation_app.ambient import AmbientAudioBuffer
        from reachy_mini_conversation_app.wake_word import REACHY_TRANSCRIPT_ALIASES, TranscriptWakeWordDetector
        from reachy_mini_conversation_app.local_actions import LocalActionExecutor
        from reachy_mini_conversation_app.hybrid_runtime import HybridRuntime
        from reachy_mini_conversation_app.ambient_context import (
            AmbientContextConfig,
            AmbientContextStrategy,
            AmbientContextProcessor,
        )
        from reachy_mini_conversation_app.local_intent_router import LocalIntentRouter
        from reachy_mini_conversation_app.local_audio_backends import RmsEnergyVAD, OpenAICompatibleLocalSTT

        def build_hybrid_handler() -> ConversationHandler:
            """Build a provider only after an explicit hybrid activation."""
            startup_voice = startup_settings.voice
            if stream_manager is not None:
                startup_voice = stream_manager.get_startup_voice() or startup_voice
            return build_handler(startup_voice)

        def queue_activation_acknowledgement(_reason: str) -> None:
            """Queue a small movement-layer acknowledgment without motor I/O, then face the speaker."""
            movement_manager.acknowledge_activation()
            if config.SPEAKER_FOLLOWING_ENABLED:
                movement_manager.turn_toward_speaker()
            if face_service is not None:
                # Start recognizing now so the name is known when the provider's startup context is sent.
                face_service.set_active(True)

        # People recognized during the current conversation, for per-person follow-up windows.
        conversation_people: set[str] = set()

        def follow_up_window_for() -> float:
            """Wait longer for an answer while someone who needs more time (e.g. Enzo) is in the conversation."""
            windows = config.HYBRID_FOLLOW_UP_BY_PERSON
            if not windows:
                return 0.0
            person = current_person_id() if face_service is not None else None
            if person:
                conversation_people.add(person)
            return max((windows.get(person_id, 0.0) for person_id in conversation_people), default=0.0)

        async def on_cloud_session(active: bool) -> None:
            """Follow faces and lower Spotify while a wake-word conversation is open; undo both afterwards."""
            from reachy_mini_conversation_app.tools.spotify_control import SPOTIFY_DUCKER

            conversation_people.clear()

            if config.SPEAKER_FOLLOWING_ENABLED:
                movement_manager.set_conversation_active(active)
            if face_service is not None:
                face_service.set_active(active)
            if active:
                await SPOTIFY_DUCKER.session_started()
            else:
                await SPOTIFY_DUCKER.session_ended()

        def silence_local_output() -> None:
            """Stop queued local movement and playback through their owners."""
            movement_manager.clear_move_queue()
            if stream_manager is not None:
                stream_manager.clear_audio_queue()

        context_processor: AmbientContextProcessor | None = None
        wake_detector: TranscriptWakeWordDetector | None = None
        intent_router: LocalIntentRouter | None = None
        action_executor: LocalActionExecutor | None = None
        local_processor: AmbientContextProcessor | None = None
        request_transcriber: Callable[[Any], Awaitable[str]] | None = None
        if config.LOCAL_STT_BASE_URL is not None:
            context_strategy = (
                AmbientContextStrategy.CONTINUOUS_TRANSCRIPT
                if config.LOCAL_CONTEXT_STRATEGY == "continuous"
                else AmbientContextStrategy.ACTIVATION_BUFFER
            )
            local_stt = OpenAICompatibleLocalSTT(
                config.LOCAL_STT_BASE_URL,
                model=config.LOCAL_STT_MODEL,
                request_model=config.LOCAL_STT_REQUEST_MODEL,
                language=config.REALTIME_TRANSCRIPTION_LANGUAGE,
                timeout_seconds=config.LOCAL_STT_TIMEOUT_SECONDS,
                max_text_chars=config.LOCAL_CONTEXT_MAX_CHARS,
            )
            if config.LOCAL_STT_REQUEST_MODEL:
                request_transcriber = local_stt.transcribe_request
            context_processor = AmbientContextProcessor(
                vad=RmsEnergyVAD(threshold=config.LOCAL_VAD_RMS_THRESHOLD),
                stt=local_stt,
                config=AmbientContextConfig(
                    strategy=context_strategy,
                    transcript_window_seconds=config.LOCAL_CONTEXT_WINDOW_SECONDS,
                    max_transcript_chars=config.LOCAL_CONTEXT_MAX_CHARS,
                    min_silence_seconds=config.LOCAL_VAD_SILENCE_SECONDS,
                    stt_timeout_seconds=config.LOCAL_STT_TIMEOUT_SECONDS,
                    latin_script_only=config.LOCAL_STT_LATIN_ONLY,
                ),
            )
            if context_strategy is AmbientContextStrategy.CONTINUOUS_TRANSCRIPT:
                local_processor = context_processor
                # With acoustic wake too, this is the backup for a wake word spoken mid-sentence.
                wake_detector = TranscriptWakeWordDetector(
                    config.LOCAL_WAKE_WORD,
                    aliases=REACHY_TRANSCRIPT_ALIASES if config.LOCAL_WAKE_WORD.casefold() == "reachy" else (),
                    cooldown_seconds=config.LOCAL_WAKE_COOLDOWN_SECONDS,
                )
                intent_router = LocalIntentRouter(wake_word=config.LOCAL_WAKE_WORD)
                action_executor = LocalActionExecutor(
                    deps,
                    stop_callback=silence_local_output,
                    silence_callback=silence_local_output,
                )
            else:
                logger.info(
                    "Activation-buffer context selected; transcript wake disabled and keyboard stays available"
                )
            logger.info(
                "Local ambient processing enabled strategy=%s endpoint=loopback",
                context_strategy.value,
            )
        else:
            logger.info("Local STT endpoint not configured; keyboard/acoustic activation needs no STT")

        addressee_check = None
        if config.HYBRID_ADDRESSEE_CHECK_ENABLED and (config.OPENAI_API_KEY or "").strip():
            from reachy_mini_conversation_app.addressee_check import AddresseeCheck

            addressee_check = AddresseeCheck(
                api_key=(config.OPENAI_API_KEY or "").strip(), model=config.OPENAI_LIVE_BACKEND_MODEL
            )
            logger.info("Addressee check enabled: side conversations close the conversation early")

        quick_commands, reply_speaker = _build_quick_commands(deps)

        async def on_listening(listening: bool) -> None:
            """Lower Spotify the moment Reachy hears his name, like a smart speaker; restore it afterwards."""
            from reachy_mini_conversation_app.tools.spotify_control import SPOTIFY_DUCKER

            if listening:
                await SPOTIFY_DUCKER.session_started()
            else:
                await SPOTIFY_DUCKER.session_ended()

        hybrid_runtime = HybridRuntime(
            build_hybrid_handler,
            ambient_buffer=AmbientAudioBuffer(max_seconds=config.HYBRID_AMBIENT_BUFFER_SECONDS),
            local_processor=local_processor,
            context_processor=context_processor,
            wake_detector=wake_detector,
            acoustic_wake_detector=acoustic_wake_detector,
            wake_clips=wake_clips,
            intent_router=intent_router,
            action_executor=action_executor,
            local_processing_close_callback=context_processor.close if context_processor is not None else None,
            acknowledgement_callback=queue_activation_acknowledgement,
            cloud_session_observer=on_cloud_session,
            inactivity_timeout_s=config.HYBRID_CLOUD_INACTIVITY_SECONDS or 0.0,
            follow_up_window_s=config.HYBRID_FOLLOW_UP_SECONDS,
            follow_up_window_for=follow_up_window_for,
            addressee_check=addressee_check,
            quick_commands=quick_commands,
            reply_speaker=reply_speaker,
            speaking_callback=getattr(movement_manager, "set_speaking", None),
            request_transcriber=request_transcriber,
            listening_observer=on_listening,
            wake_followup_timeout_s=config.LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS,
            start_asleep=started_asleep,
        )
        runtime_for_tools = hybrid_runtime

        def go_to_standby() -> dict[str, Any]:
            """Sleep pose, then close the conversation after the good night; the wake word still works."""
            movement_manager.go_to_sleep()
            runtime_for_tools.request_close("go_to_sleep")
            return {
                "status": "sleeping",
                "wakes_on_name": True,
                "note": "Say one short good night now, once. You no longer hear the room.",
            }

        def end_conversation() -> dict[str, Any]:
            """Close the conversation after Reachy's goodbye; the wake word starts a new one."""
            closing = runtime_for_tools.request_close("assistant_ended")
            if not closing:
                return {"status": "no_conversation"}
            return {
                "status": "ending",
                "note": "Say one short goodbye now, once. You no longer hear the room; do not say goodbye again.",
            }

        deps.standby = go_to_standby
        deps.end_conversation = end_conversation
        if context_processor is not None:
            # Also needed with acoustic wake: the runtime takes the request after the wake from local STT.
            context_processor.set_transcript_observer(hybrid_runtime.observe_local_transcript)
        from reachy_mini_conversation_app.tools.spotify_control import SPOTIFY_DUCKER

        SPOTIFY_DUCKER.music_playing_callback = getattr(hybrid_runtime, "request_music_wind_down", None)
        SPOTIFY_DUCKER.music_stopped_callback = getattr(hybrid_runtime, "cancel_music_wind_down", None)
        handler: ConversationHandler | HybridRuntime = hybrid_runtime
        logger.info("Hybrid runtime enabled; provider construction deferred until activation")
    else:
        handler = build_handler(startup_settings.voice)

    own_ui_server = None

    effective_settings_app = settings_app
    if args.ui and settings_app is None:
        effective_settings_app = FastAPI()

        @effective_settings_app.middleware("http")
        async def _no_cache(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
            """Serve everything no-store so browsers don't keep stale UI modules."""
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            return response

    stream_manager = LocalStream(
        handler,
        robot,
        settings_app=effective_settings_app,
        instance_path=instance_path,
        handler_factory=None if hybrid_runtime is not None else build_handler,
        startup_voice=startup_settings.voice,
        hybrid_runtime=hybrid_runtime,
        face_recognition=face_service,
        background_tasks=[deps.now_playing.run] if deps.now_playing is not None else None,
    )

    # The page is served immediately, so the API must be live before the slow startup work below.
    if effective_settings_app is not None:
        stream_manager._init_settings_ui_if_needed()

    go_to_sleep_lock = threading.Lock()
    go_to_sleep_requested = threading.Event()

    def go_to_sleep_and_stop_app() -> dict[str, Any]:
        """Put Reachy to sleep, then stop the current app."""
        if not go_to_sleep_lock.acquire(blocking=False):
            return {"status": "already_requested"}

        try:
            if go_to_sleep_requested.is_set():
                return {"status": "already_requested"}
            go_to_sleep_requested.set()

            logger.info("Going to sleep before stopping conversation app.")
            sleep_error: str | None = None

            try:
                robot.disable_wobbling()
            except Exception as e:
                logger.debug("Error disabling wobbling before sleep: %s", e)

            movement_manager.stop(reset_to_neutral=False)

            try:
                robot.goto_sleep()
            except Exception as e:
                sleep_error = f"{type(e).__name__}: {e}"
                logger.error("Failed to move Reachy Mini to sleep pose: %s", e)

            stop_current_app_requested = False
            if app_stop_event is None or not app_stop_event.is_set():
                stop_current_app_requested = app_lifecycle.request_stop_current_app(robot, logger)
            local_stop_requested = True
            if app_stop_event is not None:
                app_stop_event.set()
            else:
                try:
                    stream_manager.close()
                except Exception as e:
                    local_stop_requested = False
                    logger.error("Error while closing stream manager after go_to_sleep: %s", e)

            result: dict[str, Any] = {
                "status": "sleeping" if sleep_error is None else "stop_requested",
                "stop_current_app_requested": stop_current_app_requested,
                "local_stop_requested": local_stop_requested,
            }
            if sleep_error is not None:
                result["error"] = f"go_to_sleep movement failed: {sleep_error}"
            return result
        finally:
            go_to_sleep_lock.release()

    deps.go_to_sleep = go_to_sleep_and_stop_app

    def run_go_to_sleep_tool() -> dict[str, Any]:
        return app_lifecycle.run_go_to_sleep_tool(deps, logger)

    if args.ui and settings_app is None and effective_settings_app is not None:
        import uvicorn

        own_ui_server = uvicorn.Server(
            uvicorn.Config(effective_settings_app, host="0.0.0.0", port=7860, log_level="warning")
        )
        threading.Thread(target=own_ui_server.run, daemon=True, name="ui-server").start()
        logger.info("Web UI available at http://localhost:7860")

    try:
        app_lifecycle.initialize_tools_with_default_fallback(instance_path, logger)
    except Exception as e:
        logger.error("Failed to initialize tools: %s", e)
        sys.exit(1)

    # Each async service → its own thread/loop
    movement_manager.start()
    if speaker_direction is not None:
        speaker_direction.start()
        if hybrid_runtime is None:
            # Without the hybrid runtime the conversation is always open.
            movement_manager.set_conversation_active(True)
    if face_service is not None:
        face_service.start()
        if hybrid_runtime is None:
            face_service.set_active(True)
    if deps.display is not None:
        deps.display.start()
    # Audio-reactive head motion follows SDK playback. The explicit simulator
    # output bypass does not feed assistant PCM into this wobbler.
    if config.SPEECH_WOBBLE_ENABLED:
        robot.enable_wobbling()
    else:
        robot.disable_wobbling()
        logger.info("Speech wobbling disabled by configuration")

    timeout_minutes = resolve_app_timeout_minutes()
    if timeout_minutes is not None:
        _start_inactivity_timeout_thread(timeout_minutes, stream_manager, logger, app_stop_event, run_go_to_sleep_tool)

    def poll_stop_event() -> None:
        """Poll the stop event to allow graceful shutdown.

        Deliberately does NOT put the robot to sleep: an external stop
        (mobile app, dashboard, app switch) means "stop this app", not
        "power the robot down" — the daemon returns it to the neutral
        pose afterwards, awake and ready for the next app. Sleeping is
        reserved for the explicit paths (the voice go_to_sleep tool and
        the inactivity timeout).
        """
        if app_stop_event is not None:
            app_stop_event.wait()

        logger.info("App stop event detected, shutting down...")
        try:
            stream_manager.close()
        except Exception as e:
            logger.error(f"Error while closing stream manager: {e}")

    if app_stop_event:
        threading.Thread(target=poll_stop_event, daemon=True).start()

    try:
        stream_manager.launch()
    except KeyboardInterrupt:
        logger.info("Keyboard interruption in main thread... closing server.")
    finally:
        if own_ui_server is not None:
            own_ui_server.should_exit = True

        # Stop the motion writes without changing the robot's posture. If
        # the shutdown came from the voice go_to_sleep tool the robot is
        # already in the sleep pose; on a plain stop it stays awake and
        # the daemon returns it to neutral once the process exits.
        movement_manager.stop(reset_to_neutral=False)
        if speaker_direction is not None:
            speaker_direction.stop()
        if face_service is not None:
            face_service.stop()
        if deps.display is not None:
            deps.display.stop()
        try:
            robot.disable_wobbling()
        except Exception as e:
            logger.debug(f"Error disabling wobbling during shutdown: {e}")

        # Ensure media is explicitly closed before disconnecting
        try:
            robot.media.close()
        except Exception as e:
            logger.debug(f"Error closing media during shutdown: {e}")

        # A conversation open at exit leaves Spotify lowered or an episode paused; put it back.
        if hybrid_runtime is not None:
            from reachy_mini_conversation_app.tools.spotify_control import SPOTIFY_DUCKER

            SPOTIFY_DUCKER.restore_on_exit()

        # prevent connection to keep alive some threads
        robot.client.disconnect()
        time.sleep(1)
        logger.info("Shutdown complete.")


class ReachyMiniConversationApp(ReachyMiniApp):  # type: ignore[misc]
    """Reachy Mini Apps entry point for the conversation app."""

    custom_app_url = "http://0.0.0.0:7860/"
    dont_start_webserver = False

    def run(self, reachy_mini: ReachyMini, stop_event: threading.Event) -> None:
        """Run the Reachy Mini conversation app."""
        asyncio.set_event_loop(asyncio.new_event_loop())

        args, _ = parse_args()

        instance_path = self._get_instance_path().parent
        run(
            args,
            robot=reachy_mini,
            app_stop_event=stop_event,
            settings_app=self.settings_app,
            instance_path=instance_path,
        )


if __name__ == "__main__":
    app = ReachyMiniConversationApp()
    try:
        app.wrapped_run()
    except KeyboardInterrupt:
        app.stop()
