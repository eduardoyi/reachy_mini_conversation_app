"""Tests for app-level runtime behavior."""

import sys
import types
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import reachy_mini_conversation_app.main as main_mod
from reachy_mini_conversation_app import config
from reachy_mini_conversation_app.utils import parse_args


def _install_fake_handler_module(monkeypatch, module_name: str, class_name: str) -> MagicMock:
    """Install a fake provider module and return its handler constructor."""
    module = types.ModuleType(module_name)
    handler_constructor = MagicMock(name=class_name)
    setattr(module, class_name, handler_constructor)
    monkeypatch.setitem(sys.modules, module_name, module)
    return handler_constructor


def test_parse_args_defaults_to_sdk_media_backend(monkeypatch) -> None:
    """The CLI keeps the SDK's automatic media selection by default."""
    monkeypatch.setattr(sys, "argv", ["reachy-mini-conversation-app"])

    args, unknown = parse_args()

    assert args.media_backend == "default"
    assert unknown == []


@pytest.mark.parametrize("media_backend", ["default", "local", "webrtc", "no_media"])
def test_parse_args_accepts_supported_media_backends(monkeypatch, media_backend: str) -> None:
    """The CLI accepts each primary app media-backend choice."""
    monkeypatch.setattr(sys, "argv", ["reachy-mini-conversation-app", "--media-backend", media_backend])

    args, unknown = parse_args()

    assert args.media_backend == media_backend
    assert unknown == []


def test_parse_args_rejects_unknown_media_backend(monkeypatch) -> None:
    """An unsupported SDK backend fails during argument parsing."""
    monkeypatch.setattr(sys, "argv", ["reachy-mini-conversation-app", "--media-backend", "gstreamer"])

    with pytest.raises(SystemExit) as exc_info:
        parse_args()

    assert exc_info.value.code == 2


def test_run_forwards_media_backend_to_an_internally_constructed_robot(monkeypatch) -> None:
    """The selected backend reaches ReachyMini when run constructs the robot."""
    constructor_kwargs: dict[str, object] = {}

    def fail_after_recording(**kwargs: object) -> None:
        constructor_kwargs.update(kwargs)
        raise RuntimeError("stop after constructor capture")

    monkeypatch.setattr(main_mod, "ReachyMini", fail_after_recording)
    args = SimpleNamespace(debug=False, robot_name="simulator", media_backend="webrtc")

    with pytest.raises(SystemExit) as exc_info:
        main_mod.run(args)

    assert exc_info.value.code == 1
    assert constructor_kwargs == {"media_backend": "webrtc", "robot_name": "simulator"}


def test_run_defaults_media_backend_for_legacy_namespace(monkeypatch) -> None:
    """Programmatic callers with older namespaces retain SDK auto-detection."""
    constructor_kwargs: dict[str, object] = {}

    def fail_after_recording(**kwargs: object) -> None:
        constructor_kwargs.update(kwargs)
        raise RuntimeError("stop after constructor capture")

    monkeypatch.setattr(main_mod, "ReachyMini", fail_after_recording)
    args = SimpleNamespace(debug=False, robot_name=None)

    with pytest.raises(SystemExit) as exc_info:
        main_mod.run(args)

    assert exc_info.value.code == 1
    assert constructor_kwargs == {"media_backend": "default"}


def test_run_does_not_construct_a_robot_when_one_is_injected(monkeypatch) -> None:
    """An SDK-injected robot keeps its existing media configuration."""
    constructor = MagicMock(side_effect=AssertionError("injected robot must be reused"))
    monkeypatch.setattr(main_mod, "ReachyMini", constructor)
    wake_up = MagicMock(side_effect=RuntimeError("stop after injected robot"))
    monkeypatch.setattr(main_mod.app_lifecycle, "wake_up_if_sleeping", wake_up)
    args = SimpleNamespace(debug=False, robot_name="ignored", media_backend="webrtc")
    robot = MagicMock()

    with pytest.raises(RuntimeError, match="stop after injected robot"):
        main_mod.run(args, robot=robot)

    constructor.assert_not_called()
    wake_up.assert_called_once()


def test_build_conversation_handler_uses_huggingface_for_default_provider(monkeypatch) -> None:
    """The default provider keeps the existing Hugging Face constructor path."""
    monkeypatch.delenv(config.REALTIME_PROVIDER_ENV, raising=False)
    config.refresh_runtime_config_from_env()
    handler_constructor = _install_fake_handler_module(
        monkeypatch,
        "reachy_mini_conversation_app.huggingface_realtime",
        "HuggingFaceRealtimeHandler",
    )
    deps = object()

    handler = main_mod._build_conversation_handler(
        deps,
        config.get_realtime_provider(),
        instance_path="instance",
        startup_voice="Aiden",
        logger=MagicMock(),
    )

    assert handler is handler_constructor.return_value
    handler_constructor.assert_called_once_with(deps, instance_path="instance", startup_voice="Aiden")


def test_build_conversation_handler_uses_gemini_only_when_selected(monkeypatch) -> None:
    """Selecting Gemini uses its local import and preserves the startup voice contract."""
    monkeypatch.setattr(config.config, "REALTIME_PROVIDER", config.config.REALTIME_PROVIDER)
    monkeypatch.setattr(config.config, "GEMINI_API_KEY", config.config.GEMINI_API_KEY)
    monkeypatch.setenv(config.REALTIME_PROVIDER_ENV, config.GEMINI_BACKEND)
    config.refresh_runtime_config_from_env()
    handler_constructor = _install_fake_handler_module(
        monkeypatch,
        "reachy_mini_conversation_app.gemini_live",
        "GeminiLiveHandler",
    )
    logger = MagicMock()
    deps = object()
    secret = "gemini-test-secret"
    monkeypatch.setenv(config.GEMINI_API_KEY_ENV, secret)

    handler = main_mod._build_conversation_handler(
        deps,
        config.get_realtime_provider(),
        instance_path="instance",
        startup_voice="Aiden",
        logger=logger,
    )

    assert handler is handler_constructor.return_value
    handler_constructor.assert_called_once_with(deps, instance_path="instance", startup_voice="Aiden")
    assert secret not in str(logger.info.call_args_list)


def test_build_conversation_handler_uses_openai_live_only_when_selected(monkeypatch) -> None:
    """Selecting OpenAI Live imports and constructs only its lazy provider branch."""
    monkeypatch.setattr(config.config, "REALTIME_PROVIDER", config.config.REALTIME_PROVIDER)
    monkeypatch.setenv(config.REALTIME_PROVIDER_ENV, config.REALTIME_PROVIDER_OPENAI_LIVE)
    config.refresh_runtime_config_from_env()
    handler_constructor = _install_fake_handler_module(
        monkeypatch,
        "reachy_mini_conversation_app.openai_live",
        "OpenAILiveHandler",
    )
    logger = MagicMock()
    deps = object()

    handler = main_mod._build_conversation_handler(
        deps,
        config.get_realtime_provider(),
        instance_path="instance",
        startup_voice="marin",
        logger=logger,
    )

    assert handler is handler_constructor.return_value
    handler_constructor.assert_called_once_with(deps, instance_path="instance", startup_voice="marin")


def test_build_conversation_handler_rejects_unknown_provider_before_importing_backend(monkeypatch) -> None:
    """An unknown provider cannot silently select either backend."""
    hf_constructor = _install_fake_handler_module(
        monkeypatch,
        "reachy_mini_conversation_app.huggingface_realtime",
        "HuggingFaceRealtimeHandler",
    )
    gemini_constructor = _install_fake_handler_module(
        monkeypatch,
        "reachy_mini_conversation_app.gemini_live",
        "GeminiLiveHandler",
    )

    with pytest.raises(ValueError, match="Unsupported realtime provider"):
        main_mod._build_conversation_handler(
            object(),
            "unknown",
            instance_path=None,
            startup_voice=None,
            logger=MagicMock(),
        )

    hf_constructor.assert_not_called()
    gemini_constructor.assert_not_called()


@pytest.mark.parametrize("acoustic_enabled", [False, True])
@pytest.mark.parametrize(
    "realtime_provider",
    [config.REALTIME_PROVIDER_HUGGINGFACE, config.REALTIME_PROVIDER_OPENAI_LIVE],
)
@pytest.mark.parametrize("speech_wobble", [True, False])
def test_hybrid_run_defers_provider_factory_until_activation(
    monkeypatch, acoustic_enabled, realtime_provider, speech_wobble
) -> None:
    """Hybrid startup constructs only the local runtime, even without cloud config."""
    monkeypatch.setattr(config.config, "SPEECH_WOBBLE_ENABLED", speech_wobble)
    monkeypatch.setattr(config.config, "REALTIME_PROVIDER", realtime_provider)
    monkeypatch.setattr(config.config, "HYBRID_RUNTIME_ENABLED", True)
    monkeypatch.setattr(config.config, "LOCAL_STT_BASE_URL", None)
    monkeypatch.setattr(config.config, "LOCAL_ACOUSTIC_WAKE_ENABLED", acoustic_enabled)
    monkeypatch.setattr(config.config, "LOCAL_ACOUSTIC_WAKE_MODEL_DIR", "/private/models")
    acoustic_loader = MagicMock()
    monkeypatch.setattr(main_mod.OpenWakeWordDetector, "from_directory", acoustic_loader)

    monkeypatch.setattr(main_mod.app_lifecycle, "wake_up_if_sleeping", lambda *_args: False)
    monkeypatch.setattr(main_mod.app_lifecycle, "initialize_tools_with_default_fallback", lambda *_args: None)
    monkeypatch.setattr(config, "resolve_app_timeout_minutes", lambda: None)
    monkeypatch.setattr(main_mod, "setup_logger", lambda _debug: MagicMock())

    class FakeMovementManager:
        def __init__(self, current_robot: object, **_kwargs: object) -> None:
            self.current_robot = current_robot

        def set_conversation_active(self, _active: bool) -> None:
            return None

        def turn_toward_speaker(self) -> None:
            return None

        def start(self) -> None:
            return None

        def stop(self, reset_to_neutral: bool = False) -> None:
            return None

        def queue_move(self, _move: object) -> None:
            return None

        def acknowledge_activation(self) -> None:
            return None

        def clear_move_queue(self) -> None:
            return None

    class FakeHybridRuntime:
        factory: object

        def __init__(self, factory: object, **_kwargs: object) -> None:
            self.factory = factory
            assert _kwargs["acoustic_wake_detector"] is (acoustic_loader.return_value if acoustic_enabled else None)

    class FakeLocalStream:
        def __init__(self, handler: object, _robot: object, **_kwargs: object) -> None:
            self.handler = handler

        def launch(self) -> None:
            return None

        def close(self) -> None:
            return None

        def seconds_since_activity(self) -> float:
            return 0.0

    monkeypatch.setattr("reachy_mini_conversation_app.moves.MovementManager", FakeMovementManager)
    monkeypatch.setattr("reachy_mini_conversation_app.hybrid_runtime.HybridRuntime", FakeHybridRuntime)
    monkeypatch.setattr("reachy_mini_conversation_app.console.LocalStream", FakeLocalStream)
    build_handler = MagicMock(side_effect=AssertionError("provider must wait for activation"))
    monkeypatch.setattr(main_mod, "_build_conversation_handler", build_handler)

    robot = MagicMock()
    args = SimpleNamespace(debug=False, robot_name=None, media_backend="default", no_camera=False, ui=False)

    warm_import = MagicMock()
    monkeypatch.setattr(main_mod, "importlib", SimpleNamespace(import_module=warm_import))
    main_mod.run(args, robot=robot)

    if realtime_provider == config.REALTIME_PROVIDER_OPENAI_LIVE:
        assert [call.args[0] for call in warm_import.call_args_list] == [
            "reachy_mini_conversation_app.openai_live",
            "openai.resources.responses",
        ]
    else:
        warm_import.assert_not_called()
    build_handler.assert_not_called()
    assert acoustic_loader.call_count == int(acoustic_enabled)
    assert robot.enable_wobbling.call_count == int(speech_wobble)
    assert robot.disable_wobbling.call_count >= int(not speech_wobble)


@pytest.mark.parametrize(("strategy", "voice_wake_enabled"), [("continuous", True), ("activation", False)])
def test_hybrid_run_wires_loopback_context_wake_and_local_actions(
    monkeypatch, strategy: str, voice_wake_enabled: bool
) -> None:
    """Configured local STT composes each context strategy without opening cloud."""
    monkeypatch.setattr(config.config, "HYBRID_RUNTIME_ENABLED", True)
    monkeypatch.setattr(config.config, "LOCAL_STT_BASE_URL", "http://127.0.0.1:8765/v1")
    monkeypatch.setattr(config.config, "LOCAL_CONTEXT_STRATEGY", strategy)
    monkeypatch.setattr(config.config, "LOCAL_STT_MODEL", "local-asr")
    monkeypatch.setattr(config.config, "LOCAL_STT_TIMEOUT_SECONDS", 5.0)
    monkeypatch.setattr(config.config, "LOCAL_CONTEXT_WINDOW_SECONDS", 60.0)
    monkeypatch.setattr(config.config, "LOCAL_CONTEXT_MAX_CHARS", 2_000)
    monkeypatch.setattr(config.config, "LOCAL_VAD_RMS_THRESHOLD", 500.0)
    monkeypatch.setattr(config.config, "LOCAL_VAD_SILENCE_SECONDS", 0.6)
    monkeypatch.setattr(config.config, "LOCAL_WAKE_WORD", "Reachy")
    monkeypatch.setattr(config.config, "LOCAL_WAKE_COOLDOWN_SECONDS", 2.0)
    monkeypatch.setattr(config.config, "LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS", 2.0)
    monkeypatch.setattr(config.config, "REALTIME_TRANSCRIPTION_LANGUAGE", "en")
    monkeypatch.setattr(main_mod.app_lifecycle, "wake_up_if_sleeping", lambda *_args: False)
    monkeypatch.setattr(main_mod.app_lifecycle, "initialize_tools_with_default_fallback", lambda *_args: None)
    monkeypatch.setattr(config, "resolve_app_timeout_minutes", lambda: None)
    monkeypatch.setattr(main_mod, "setup_logger", lambda _debug: MagicMock())

    class FakeMovementManager:
        def __init__(self, current_robot: object, **_kwargs: object) -> None:
            self.current_robot = current_robot

        def set_conversation_active(self, _active: bool) -> None:
            return None

        def turn_toward_speaker(self) -> None:
            return None

        def start(self) -> None:
            return None

        def stop(self, reset_to_neutral: bool = False) -> None:
            return None

        def queue_move(self, _move: object) -> None:
            return None

        def acknowledge_activation(self) -> None:
            return None

        def clear_move_queue(self) -> None:
            return None

    class FakeHybridRuntime:
        instance: "FakeHybridRuntime | None" = None

        def __init__(self, factory: object, **kwargs: object) -> None:
            self.factory = factory
            self.kwargs = kwargs
            FakeHybridRuntime.instance = self

        def observe_local_transcript(self, _segment: object) -> None:
            return None

    class FakeLocalStream:
        def __init__(self, handler: object, _robot: object, **_kwargs: object) -> None:
            self.handler = handler

        def launch(self) -> None:
            return None

        def close(self) -> None:
            return None

        def seconds_since_activity(self) -> float:
            return 0.0

    monkeypatch.setattr("reachy_mini_conversation_app.moves.MovementManager", FakeMovementManager)
    monkeypatch.setattr("reachy_mini_conversation_app.hybrid_runtime.HybridRuntime", FakeHybridRuntime)
    monkeypatch.setattr("reachy_mini_conversation_app.console.LocalStream", FakeLocalStream)
    build_handler = MagicMock(side_effect=AssertionError("provider must wait for activation"))
    monkeypatch.setattr(main_mod, "_build_conversation_handler", build_handler)

    args = SimpleNamespace(debug=False, robot_name=None, media_backend="default", no_camera=False, ui=False)
    main_mod.run(args, robot=MagicMock())

    runtime = FakeHybridRuntime.instance
    assert runtime is not None
    if voice_wake_enabled:
        assert runtime.kwargs["local_processor"] is runtime.kwargs["context_processor"]
        assert runtime.kwargs["wake_detector"] is not None
        assert runtime.kwargs["intent_router"] is not None
        assert runtime.kwargs["action_executor"] is not None
    else:
        assert runtime.kwargs["local_processor"] is None
        assert runtime.kwargs["context_processor"] is not None
        assert runtime.kwargs["wake_detector"] is None
        assert runtime.kwargs["intent_router"] is None
        assert runtime.kwargs["action_executor"] is None
    assert runtime.kwargs["local_processing_close_callback"] is not None
    assert runtime.kwargs["context_processor"].config.transcript_window_seconds == 60.0
    assert runtime.kwargs["wake_followup_timeout_s"] == 2.0
    build_handler.assert_not_called()


def test_inactivity_timeout_thread_goes_to_sleep() -> None:
    """The watchdog should use the shared sleep shutdown path once activity is too old."""
    stream_manager = SimpleNamespace(seconds_since_activity=lambda: 10.0, close=MagicMock())
    go_to_sleep = MagicMock(return_value={"status": "sleeping"})

    thread = main_mod._start_inactivity_timeout_thread(
        timeout_minutes=0.0001,
        stream_manager=stream_manager,
        logger=MagicMock(),
        app_stop_event=threading.Event(),
        go_to_sleep=go_to_sleep,
    )

    thread.join(timeout=1.0)
    assert not thread.is_alive()
    go_to_sleep.assert_called_once_with()
    stream_manager.close.assert_not_called()


def test_inactivity_timeout_thread_closes_stream_manager_without_sleep_callback() -> None:
    """The watchdog should still close the stream when no sleep callback is available."""
    stream_manager = SimpleNamespace(seconds_since_activity=lambda: 10.0, close=MagicMock())

    thread = main_mod._start_inactivity_timeout_thread(
        timeout_minutes=0.0001,
        stream_manager=stream_manager,
        logger=MagicMock(),
        app_stop_event=threading.Event(),
    )

    thread.join(timeout=1.0)
    assert not thread.is_alive()
    stream_manager.close.assert_called_once_with()


@pytest.mark.parametrize(
    "hybrid, directory, error", [(False, "/private/models", "HYBRID_RUNTIME"), (True, "", "MODEL_DIR")]
)
def test_acoustic_startup_requires_explicit_hybrid_and_assets(monkeypatch, hybrid, directory, error) -> None:
    """Acoustic startup fails before media initialization without explicit prerequisites."""
    monkeypatch.setattr(config.config, "LOCAL_ACOUSTIC_WAKE_ENABLED", True)
    monkeypatch.setattr(config.config, "HYBRID_RUNTIME_ENABLED", hybrid)
    monkeypatch.setattr(config.config, "LOCAL_ACOUSTIC_WAKE_MODEL_DIR", directory)
    loader = MagicMock()
    monkeypatch.setattr(main_mod.OpenWakeWordDetector, "from_directory", loader)
    with pytest.raises(ValueError, match=error):
        main_mod.run(SimpleNamespace(debug=False))
    loader.assert_not_called()
