"""Tests for configuration helpers."""

import logging

import pytest

from reachy_mini_conversation_app import config
from reachy_mini_conversation_app.wake_word import TranscriptWakeWordDetector
from reachy_mini_conversation_app.local_intent_router import LocalIntentRouter


def test_realtime_provider_defaults_to_huggingface(monkeypatch) -> None:
    """An unset realtime provider keeps the existing Hugging Face behavior."""
    monkeypatch.delenv(config.REALTIME_PROVIDER_ENV, raising=False)

    config.refresh_runtime_config_from_env()

    assert config.get_realtime_provider() == config.REALTIME_PROVIDER_HUGGINGFACE


def test_dotenv_preserves_process_values_and_loads_absent_values(monkeypatch, tmp_path) -> None:
    """Local defaults fill unset values without replacing explicit process settings."""
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        f"{config.HYBRID_RUNTIME_ENABLED_ENV}=1\n{config.LOCAL_STT_MODEL_ENV}=dotenv-model\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("REACHY_MINI_SKIP_DOTENV", raising=False)
    monkeypatch.setenv(config.HYBRID_RUNTIME_ENABLED_ENV, "0")
    monkeypatch.delenv(config.LOCAL_STT_MODEL_ENV, raising=False)
    monkeypatch.setattr(config, "find_dotenv", lambda usecwd: str(dotenv_path))

    config._load_dotenv_configuration()
    config.refresh_runtime_config_from_env()

    assert config.config.HYBRID_RUNTIME_ENABLED is False
    assert config.config.LOCAL_STT_MODEL == "dotenv-model"


def test_audio_output_defaults_to_sdk(monkeypatch) -> None:
    """Speaker routing remains on the official SDK path unless explicitly changed."""
    monkeypatch.delenv(config.AUDIO_OUTPUT_BACKEND_ENV, raising=False)

    config.refresh_runtime_config_from_env()

    assert config.get_audio_output_backend() == config.AUDIO_OUTPUT_BACKEND_SDK


def test_wake_word_defaults_to_reachy(monkeypatch) -> None:
    """The written product wake phrase is Reachy unless explicitly configured otherwise."""
    monkeypatch.delenv(config.LOCAL_WAKE_WORD_ENV, raising=False)

    config.refresh_runtime_config_from_env()

    assert config.DEFAULT_LOCAL_WAKE_WORD == "Reachy"
    assert config.config.LOCAL_WAKE_WORD == "Reachy"

    assert LocalIntentRouter().wake_word == "reachy"
    assert TranscriptWakeWordDetector().process_transcript("Reachy", is_final=True) is not None


def test_historical_tito_config_override_remains_explicit(monkeypatch) -> None:
    """The old Tito setting remains available only when a caller opts into it."""
    monkeypatch.setenv(config.LOCAL_WAKE_WORD_ENV, " Tito ")

    config.refresh_runtime_config_from_env()

    assert config.config.LOCAL_WAKE_WORD == "Tito"
    assert LocalIntentRouter(wake_word=config.config.LOCAL_WAKE_WORD).wake_word == "tito"
    assert (
        TranscriptWakeWordDetector(wake_word=config.config.LOCAL_WAKE_WORD).process_transcript("Tito", is_final=True)
        is not None
    )


@pytest.mark.parametrize("raw_value", ["gstreamer", " GSTREAMER "])
def test_audio_output_accepts_explicit_gstreamer(monkeypatch, raw_value: str) -> None:
    """The app-owned speaker path requires an explicit validated selector."""
    monkeypatch.setenv(config.AUDIO_OUTPUT_BACKEND_ENV, raw_value)

    config.refresh_runtime_config_from_env()

    assert config.get_audio_output_backend() == config.AUDIO_OUTPUT_BACKEND_GSTREAMER


def test_audio_output_rejects_unknown_values(monkeypatch) -> None:
    """Invalid speaker configuration fails closed instead of changing the production path."""
    monkeypatch.setenv(config.AUDIO_OUTPUT_BACKEND_ENV, "sounddevice")

    with pytest.raises(ValueError, match="Invalid AUDIO_OUTPUT_BACKEND"):
        config.refresh_runtime_config_from_env()


@pytest.mark.parametrize(
    "raw_value, expected",
    [
        ("gemini", config.REALTIME_PROVIDER_GEMINI),
        ("  GeMiNi  ", config.REALTIME_PROVIDER_GEMINI),
        ("openai_live", config.REALTIME_PROVIDER_OPENAI_LIVE),
        ("  OPENAI-LIVE  ", config.REALTIME_PROVIDER_OPENAI_LIVE),
        ("  HUGGINGFACE  ", config.REALTIME_PROVIDER_HUGGINGFACE),
        ("  ", config.REALTIME_PROVIDER_HUGGINGFACE),
    ],
)
def test_realtime_provider_normalizes_whitespace_and_case(monkeypatch, raw_value, expected) -> None:
    """The provider selector accepts case-insensitive values with surrounding whitespace."""
    monkeypatch.setenv(config.REALTIME_PROVIDER_ENV, raw_value)

    config.refresh_runtime_config_from_env()

    assert config.get_realtime_provider() == expected


def test_invalid_realtime_provider_fails_clearly_without_logging_credentials(monkeypatch, caplog) -> None:
    """An invalid provider fails closed without exposing a Gemini credential."""
    secret = "gemini-test-secret"
    previous_provider = config.get_realtime_provider()
    monkeypatch.setenv(config.REALTIME_PROVIDER_ENV, "openai")
    monkeypatch.setenv(config.GEMINI_API_KEY_ENV, secret)

    with caplog.at_level(logging.WARNING, logger=config.logger.name):
        with pytest.raises(ValueError, match="Invalid REALTIME_PROVIDER"):
            config.refresh_runtime_config_from_env()

    assert config.get_realtime_provider() == previous_provider
    assert secret not in caplog.text


def test_gemini_defaults_and_runtime_overrides_are_loaded(monkeypatch) -> None:
    """Gemini model and voice use documented defaults and trim explicit overrides."""
    monkeypatch.delenv(config.GEMINI_LIVE_MODEL_ENV, raising=False)
    monkeypatch.delenv(config.GEMINI_VOICE_ENV, raising=False)
    config.refresh_runtime_config_from_env()
    assert config.config.GEMINI_LIVE_MODEL == config.DEFAULT_GEMINI_LIVE_MODEL
    assert config.config.GEMINI_VOICE == config.DEFAULT_GEMINI_VOICE

    monkeypatch.setenv(config.GEMINI_LIVE_MODEL_ENV, "  custom-live-model  ")
    monkeypatch.setenv(config.GEMINI_VOICE_ENV, "  Aoede  ")
    config.refresh_runtime_config_from_env()
    assert config.config.GEMINI_LIVE_MODEL == "custom-live-model"
    assert config.config.GEMINI_VOICE == "Aoede"


@pytest.mark.parametrize("raw_value, expected", [(None, "medium"), (" LOW ", "low"), ("HIGH", "high")])
def test_gemini_extended_thinking_level_is_model_scoped(monkeypatch, raw_value, expected) -> None:
    """Extended Thinking defaults to medium and accepts only its three levels."""
    monkeypatch.setenv(config.GEMINI_LIVE_MODEL_ENV, config.GEMINI_EXTENDED_THINKING_MODEL)
    if raw_value is None:
        monkeypatch.delenv(config.GEMINI_THINKING_LEVEL_ENV, raising=False)
    else:
        monkeypatch.setenv(config.GEMINI_THINKING_LEVEL_ENV, raw_value)

    config.refresh_runtime_config_from_env()

    assert config.config.GEMINI_THINKING_LEVEL == expected


@pytest.mark.parametrize("raw_value", ["minimal", "ultra", ""])
def test_gemini_extended_thinking_level_rejects_invalid_values(monkeypatch, raw_value) -> None:
    """Unsupported or blank explicit levels fail closed for Extended Thinking."""
    monkeypatch.setenv(config.GEMINI_LIVE_MODEL_ENV, config.GEMINI_EXTENDED_THINKING_MODEL)
    if raw_value:
        monkeypatch.setenv(config.GEMINI_THINKING_LEVEL_ENV, raw_value)
    else:
        monkeypatch.setenv(config.GEMINI_THINKING_LEVEL_ENV, "minimal")

    with pytest.raises(ValueError, match="GEMINI_THINKING_LEVEL"):
        config.refresh_runtime_config_from_env()


def test_gemini_thinking_level_is_rejected_for_legacy_model(monkeypatch) -> None:
    """An explicit thinking level cannot silently change a legacy model setup."""
    monkeypatch.setenv(config.GEMINI_LIVE_MODEL_ENV, config.DEFAULT_GEMINI_LIVE_MODEL)
    monkeypatch.setenv(config.GEMINI_THINKING_LEVEL_ENV, "high")

    with pytest.raises(ValueError, match="only for"):
        config.refresh_runtime_config_from_env()


def test_gemini_model_check_accepts_sdk_models_prefix() -> None:
    """The SDK's optional models prefix still selects the Extended Thinking contract."""
    assert config.is_gemini_extended_thinking_model(f"models/{config.GEMINI_EXTENDED_THINKING_MODEL}")


def test_gemini_38_live_model_check_covers_both_38_models() -> None:
    """Plain 3.8 Live and Extended Thinking share session features; the legacy model does not."""
    assert config.is_gemini_38_live_model(config.GEMINI_38_LIVE_MODEL)
    assert config.is_gemini_38_live_model(f"models/{config.GEMINI_EXTENDED_THINKING_MODEL}")
    assert not config.is_gemini_38_live_model(config.DEFAULT_GEMINI_LIVE_MODEL)
    assert not config.is_gemini_extended_thinking_model(config.GEMINI_38_LIVE_MODEL)


def test_gemini_thinking_level_is_rejected_for_plain_38_live(monkeypatch) -> None:
    """Plain 3.8 Live does not support thinking_level, so an explicit one fails clearly."""
    monkeypatch.setenv(config.GEMINI_LIVE_MODEL_ENV, config.GEMINI_38_LIVE_MODEL)
    monkeypatch.setenv(config.GEMINI_THINKING_LEVEL_ENV, "low")

    with pytest.raises(ValueError, match="only for"):
        config.refresh_runtime_config_from_env()


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (None, config.DEFAULT_GEMINI_CONTEXT_TRIGGER_TOKENS),
        ("48000", 48_000),
        ("1000", config.DEFAULT_GEMINI_CONTEXT_TRIGGER_TOKENS),
        ("lots", config.DEFAULT_GEMINI_CONTEXT_TRIGGER_TOKENS),
    ],
)
def test_gemini_context_trigger_tokens_default_and_override(monkeypatch, raw_value, expected) -> None:
    """The context cap defaults to 32k and ignores values too small to keep the instructions."""
    monkeypatch.setattr(config.config, "GEMINI_CONTEXT_TRIGGER_TOKENS", config.config.GEMINI_CONTEXT_TRIGGER_TOKENS)
    if raw_value is None:
        monkeypatch.delenv(config.GEMINI_CONTEXT_TRIGGER_TOKENS_ENV, raising=False)
    else:
        monkeypatch.setenv(config.GEMINI_CONTEXT_TRIGGER_TOKENS_ENV, raw_value)

    config.refresh_runtime_config_from_env()

    assert config.config.GEMINI_CONTEXT_TRIGGER_TOKENS == expected


def test_openai_live_defaults_and_runtime_overrides_are_loaded(monkeypatch) -> None:
    """OpenAI Live model and voice use documented defaults and trim explicit overrides."""
    monkeypatch.setattr(config.config, "OPENAI_LIVE_MODEL", config.config.OPENAI_LIVE_MODEL)
    monkeypatch.setattr(config.config, "OPENAI_LIVE_BACKEND_MODEL", config.config.OPENAI_LIVE_BACKEND_MODEL)
    monkeypatch.setattr(config.config, "OPENAI_LIVE_VOICE", config.config.OPENAI_LIVE_VOICE)
    monkeypatch.delenv(config.OPENAI_LIVE_MODEL_ENV, raising=False)
    monkeypatch.delenv(config.OPENAI_LIVE_BACKEND_MODEL_ENV, raising=False)
    monkeypatch.delenv(config.OPENAI_LIVE_VOICE_ENV, raising=False)
    config.refresh_runtime_config_from_env()

    assert config.config.OPENAI_LIVE_MODEL == config.DEFAULT_OPENAI_LIVE_MODEL
    assert config.config.OPENAI_LIVE_BACKEND_MODEL == config.DEFAULT_OPENAI_LIVE_BACKEND_MODEL
    assert config.config.OPENAI_LIVE_VOICE == config.DEFAULT_OPENAI_LIVE_VOICE
    assert config.DEFAULT_OPENAI_LIVE_VOICE in config.OPENAI_LIVE_AVAILABLE_VOICES

    monkeypatch.setenv(config.OPENAI_LIVE_MODEL_ENV, "  custom-live-model  ")
    monkeypatch.setenv(config.OPENAI_LIVE_BACKEND_MODEL_ENV, "  custom-backend-model  ")
    monkeypatch.setenv(config.OPENAI_LIVE_VOICE_ENV, "  quartz  ")
    config.refresh_runtime_config_from_env()

    assert config.config.OPENAI_LIVE_MODEL == "custom-live-model"
    assert config.config.OPENAI_LIVE_BACKEND_MODEL == "custom-backend-model"
    assert config.config.OPENAI_LIVE_VOICE == "quartz"


def test_realtime_target_uses_gemini_api_key(monkeypatch) -> None:
    """The provider-neutral target check requires a Gemini key only for Gemini."""
    monkeypatch.setattr(config.config, "REALTIME_PROVIDER", config.config.REALTIME_PROVIDER)
    monkeypatch.setattr(config.config, "GEMINI_API_KEY", config.config.GEMINI_API_KEY)
    monkeypatch.setenv(config.REALTIME_PROVIDER_ENV, config.GEMINI_BACKEND)
    monkeypatch.delenv(config.GEMINI_API_KEY_ENV, raising=False)
    config.refresh_runtime_config_from_env()
    assert not config.has_realtime_target()
    assert config.get_realtime_missing_config_message() == "GEMINI_API_KEY is not configured."

    monkeypatch.setenv(config.GEMINI_API_KEY_ENV, "test-key")
    config.refresh_runtime_config_from_env()
    assert config.has_realtime_target()


def test_realtime_target_uses_openai_api_key(monkeypatch) -> None:
    """The OpenAI Live target check requires only its environment key."""
    monkeypatch.setattr(config.config, "REALTIME_PROVIDER", config.config.REALTIME_PROVIDER)
    monkeypatch.setattr(config.config, "OPENAI_API_KEY", config.config.OPENAI_API_KEY)
    monkeypatch.setenv(config.REALTIME_PROVIDER_ENV, config.REALTIME_PROVIDER_OPENAI_LIVE)
    monkeypatch.delenv(config.OPENAI_API_KEY_ENV, raising=False)
    config.refresh_runtime_config_from_env()

    assert not config.has_realtime_target()
    assert config.get_realtime_missing_config_message() == "OPENAI_API_KEY is not configured."

    monkeypatch.setenv(config.OPENAI_API_KEY_ENV, "test-key")
    config.refresh_runtime_config_from_env()

    assert config.has_realtime_target()


def test_realtime_target_preserves_huggingface_selection(monkeypatch) -> None:
    """The provider-neutral target check delegates Hugging Face selection unchanged."""
    monkeypatch.setenv(config.REALTIME_PROVIDER_ENV, config.HF_BACKEND)
    monkeypatch.setenv(config.HF_REALTIME_CONNECTION_MODE_ENV, config.HF_LOCAL_CONNECTION_MODE)
    monkeypatch.delenv(config.HF_REALTIME_WS_URL_ENV, raising=False)
    config.refresh_runtime_config_from_env()

    assert not config.has_realtime_target()
    assert config.get_realtime_missing_config_message() == "HF_REALTIME_WS_URL is not configured."


@pytest.mark.parametrize(
    "raw_value, expected",
    [
        ("45", 45.0),
        ("", config.DEFAULT_APP_TIMEOUT_MINUTES),  # unset/blank falls back to the default
        ("soon", config.DEFAULT_APP_TIMEOUT_MINUTES),  # unparseable falls back to the default
        ("0", None),  # non-positive disables the watchdog
        ("-1", None),
    ],
)
def test_resolve_app_timeout_minutes(monkeypatch, raw_value, expected) -> None:
    """The env timeout parses to minutes, falls back to the default, or disables on non-positive."""
    monkeypatch.setenv(config.APP_TIMEOUT_MINUTES_ENV, raw_value)

    assert config.resolve_app_timeout_minutes() == expected


def test_hybrid_runtime_defaults_are_cloud_disabled_until_activation(monkeypatch) -> None:
    """Hybrid mode is opt-in and uses the documented bounded defaults."""
    monkeypatch.delenv(config.HYBRID_RUNTIME_ENABLED_ENV, raising=False)
    monkeypatch.delenv(config.HYBRID_CLOUD_INACTIVITY_SECONDS_ENV, raising=False)
    monkeypatch.delenv(config.HYBRID_AMBIENT_BUFFER_SECONDS_ENV, raising=False)
    monkeypatch.delenv(config.LOCAL_CONTEXT_WINDOW_SECONDS_ENV, raising=False)
    config.refresh_runtime_config_from_env()

    assert config.is_hybrid_runtime_enabled() is False
    assert config.resolve_hybrid_cloud_inactivity_seconds() == 60.0
    assert config.resolve_hybrid_ambient_buffer_seconds() == 20.0
    assert config.config.LOCAL_CONTEXT_WINDOW_SECONDS == 60.0


def test_household_memory_is_disabled_by_default_and_accepts_explicit_flag(monkeypatch) -> None:
    """Household memory stays opt-in and parses the shared boolean flag."""
    monkeypatch.setattr(config.config, "HOUSEHOLD_MEMORY_ENABLED", config.config.HOUSEHOLD_MEMORY_ENABLED)
    monkeypatch.delenv(config.HOUSEHOLD_MEMORY_ENABLED_ENV, raising=False)
    config.refresh_runtime_config_from_env()
    assert config.config.HOUSEHOLD_MEMORY_ENABLED is False
    assert config.is_household_memory_enabled() is False

    monkeypatch.setenv(config.HOUSEHOLD_MEMORY_ENABLED_ENV, " yes ")
    config.refresh_runtime_config_from_env()
    assert config.config.HOUSEHOLD_MEMORY_ENABLED is True
    assert config.is_household_memory_enabled() is True


def test_household_capture_is_separately_opt_in(monkeypatch) -> None:
    """Enabling the notebook alone must not enable paid transcript processing."""
    monkeypatch.setattr(config.config, "HOUSEHOLD_MEMORY_CAPTURE_ENABLED", False)
    monkeypatch.delenv(config.HOUSEHOLD_MEMORY_CAPTURE_ENABLED_ENV, raising=False)
    config.refresh_runtime_config_from_env()
    assert config.config.HOUSEHOLD_MEMORY_CAPTURE_ENABLED is False
    monkeypatch.setenv(config.HOUSEHOLD_MEMORY_CAPTURE_ENABLED_ENV, "1")
    config.refresh_runtime_config_from_env()
    assert config.config.HOUSEHOLD_MEMORY_CAPTURE_ENABLED is True


def test_local_transcript_preview_defaults_to_disabled(monkeypatch) -> None:
    """The diagnostic transcript surface stays off unless explicitly enabled."""
    monkeypatch.delenv(config.LOCAL_TRANSCRIPT_PREVIEW_ENABLED_ENV, raising=False)

    config.refresh_runtime_config_from_env()

    assert config.is_local_transcript_preview_enabled() is False
    assert config.config.LOCAL_TRANSCRIPT_PREVIEW_ENABLED is False


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("1", True),
        (" true ", True),
        ("yes", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("off", False),
        ("unexpected", False),
    ],
)
def test_local_transcript_preview_parses_boolean_environment(monkeypatch, raw_value: str, expected: bool) -> None:
    """Preview configuration accepts the shared boolean syntax and fails closed."""
    monkeypatch.setenv(config.LOCAL_TRANSCRIPT_PREVIEW_ENABLED_ENV, raw_value)

    config.refresh_runtime_config_from_env()

    assert config.is_local_transcript_preview_enabled() is expected
    assert config.config.LOCAL_TRANSCRIPT_PREVIEW_ENABLED is expected


def test_local_transcript_preview_refreshes_without_affecting_context_bounds(monkeypatch) -> None:
    """Refreshing preview enablement leaves the bounded transcript settings intact."""
    monkeypatch.setenv(config.LOCAL_TRANSCRIPT_PREVIEW_ENABLED_ENV, "1")
    monkeypatch.setenv(config.LOCAL_CONTEXT_WINDOW_SECONDS_ENV, "60")
    monkeypatch.setenv(config.LOCAL_CONTEXT_MAX_CHARS_ENV, "2000")

    config.refresh_runtime_config_from_env()

    assert config.config.LOCAL_TRANSCRIPT_PREVIEW_ENABLED is True
    assert config.config.LOCAL_CONTEXT_WINDOW_SECONDS == 60.0
    assert config.config.LOCAL_CONTEXT_MAX_CHARS == 2_000


@pytest.mark.parametrize("raw_value", ["0", "-1", "nan", "inf", "not-a-duration"])
def test_local_context_window_rejects_unusable_values(monkeypatch, raw_value: str) -> None:
    """The derived transcript window stays finite and positive after env parsing."""
    monkeypatch.setenv(config.LOCAL_CONTEXT_WINDOW_SECONDS_ENV, raw_value)

    config.refresh_runtime_config_from_env()

    assert config.config.LOCAL_CONTEXT_WINDOW_SECONDS == config.DEFAULT_LOCAL_CONTEXT_WINDOW_SECONDS


def test_local_context_window_is_independent_from_raw_ring(monkeypatch) -> None:
    """The transcript window can exceed the fixed raw PCM retention window."""
    monkeypatch.setenv(config.HYBRID_AMBIENT_BUFFER_SECONDS_ENV, "20")
    monkeypatch.setenv(config.LOCAL_CONTEXT_WINDOW_SECONDS_ENV, "90")

    config.refresh_runtime_config_from_env()

    assert config.config.HYBRID_AMBIENT_BUFFER_SECONDS == 20.0
    assert config.config.LOCAL_CONTEXT_WINDOW_SECONDS == 90.0


def test_hybrid_runtime_settings_parse_disable_and_cap(monkeypatch) -> None:
    """Cloud timeout zero disables and the ambient ring never exceeds 20 seconds."""
    monkeypatch.setenv(config.HYBRID_RUNTIME_ENABLED_ENV, "yes")
    monkeypatch.setenv(config.HYBRID_CLOUD_INACTIVITY_SECONDS_ENV, "0")
    monkeypatch.setenv(config.HYBRID_AMBIENT_BUFFER_SECONDS_ENV, "30")
    config.refresh_runtime_config_from_env()

    assert config.is_hybrid_runtime_enabled() is True
    assert config.resolve_hybrid_cloud_inactivity_seconds() is None
    assert config.resolve_hybrid_ambient_buffer_seconds() == 20.0


@pytest.mark.parametrize("raw_value", ["nan", "NaN", "inf", "-inf"])
def test_hybrid_durations_reject_non_finite_values(monkeypatch, raw_value: str) -> None:
    """Non-finite hybrid durations fall back to documented safe defaults."""
    monkeypatch.setenv(config.HYBRID_CLOUD_INACTIVITY_SECONDS_ENV, raw_value)
    monkeypatch.setenv(config.HYBRID_AMBIENT_BUFFER_SECONDS_ENV, raw_value)

    assert config.resolve_hybrid_cloud_inactivity_seconds() == config.DEFAULT_HYBRID_CLOUD_INACTIVITY_SECONDS
    assert config.resolve_hybrid_ambient_buffer_seconds() == config.DEFAULT_HYBRID_AMBIENT_BUFFER_SECONDS


def test_local_processing_defaults_keep_endpoint_disabled(monkeypatch) -> None:
    """Local context has safe defaults but performs no STT without an endpoint."""
    for name in (
        config.LOCAL_CONTEXT_STRATEGY_ENV,
        config.LOCAL_STT_BASE_URL_ENV,
        config.LOCAL_STT_MODEL_ENV,
        config.LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS_ENV,
        config.LOCAL_CONTEXT_WINDOW_SECONDS_ENV,
        config.LOCAL_CONTEXT_MAX_CHARS_ENV,
    ):
        monkeypatch.delenv(name, raising=False)

    config.refresh_runtime_config_from_env()

    assert config.config.LOCAL_CONTEXT_STRATEGY == config.LOCAL_CONTEXT_CONTINUOUS
    assert config.config.LOCAL_STT_BASE_URL is None
    assert config.config.LOCAL_STT_MODEL == config.DEFAULT_LOCAL_STT_MODEL
    assert config.config.LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS == config.DEFAULT_LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS
    assert config.config.LOCAL_CONTEXT_MAX_CHARS == config.DEFAULT_LOCAL_CONTEXT_MAX_CHARS


@pytest.mark.parametrize("raw_value", ["0", "-1", "nan", "inf", "not-a-duration"])
def test_wake_followup_timeout_rejects_unusable_values(monkeypatch, raw_value: str) -> None:
    """A follow-up wait always remains finite and positive after env parsing."""
    monkeypatch.setenv(config.LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS_ENV, raw_value)

    config.refresh_runtime_config_from_env()

    assert config.config.LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS == config.DEFAULT_LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS


@pytest.mark.parametrize("raw_value", ["15.1", "60", "1000000"])
def test_wake_followup_timeout_rejects_values_above_bound(monkeypatch, raw_value: str) -> None:
    """A local follow-up wait cannot be expanded to the text-context window."""
    monkeypatch.setenv(config.LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS_ENV, raw_value)

    config.refresh_runtime_config_from_env()

    assert config.config.LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS == config.DEFAULT_LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS


@pytest.mark.parametrize("raw_value", ["later", "", "CONTINUOUS", " activation "])
def test_local_context_strategy_is_conservative(monkeypatch, raw_value: str) -> None:
    """Only the two documented local-context strategies are accepted."""
    monkeypatch.setenv(config.LOCAL_CONTEXT_STRATEGY_ENV, raw_value)

    expected = "activation" if raw_value.strip().lower() == "activation" else "continuous"
    assert config.resolve_local_context_strategy() == expected


@pytest.mark.parametrize("raw_value", ["127", "8001", "not-a-number", "0", "-2"])
def test_local_context_character_bound_is_enforced(monkeypatch, raw_value: str) -> None:
    """Ambient transcripts cannot be configured outside the privacy size bound."""
    monkeypatch.setenv(config.LOCAL_CONTEXT_MAX_CHARS_ENV, raw_value)

    assert config.resolve_local_context_max_chars() == config.DEFAULT_LOCAL_CONTEXT_MAX_CHARS


def test_local_processing_overrides_refresh_without_exposing_credentials(monkeypatch) -> None:
    """Local STT and wake settings refresh as ordinary non-secret metadata."""
    monkeypatch.setenv(config.LOCAL_STT_BASE_URL_ENV, " http://127.0.0.1:8080/v1 ")
    monkeypatch.setenv(config.LOCAL_STT_MODEL_ENV, " local-asr ")
    monkeypatch.setenv(config.LOCAL_STT_TIMEOUT_SECONDS_ENV, "9")
    monkeypatch.setenv(config.LOCAL_VAD_RMS_THRESHOLD_ENV, "420")
    monkeypatch.setenv(config.LOCAL_VAD_SILENCE_SECONDS_ENV, "0.8")
    monkeypatch.setenv(config.LOCAL_WAKE_WORD_ENV, " Johnny ")
    monkeypatch.setenv(config.LOCAL_WAKE_COOLDOWN_SECONDS_ENV, "4")
    monkeypatch.setenv(config.LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS_ENV, "0.75")
    monkeypatch.setenv(config.LOCAL_CONTEXT_WINDOW_SECONDS_ENV, "90")
    monkeypatch.setenv(config.LOCAL_CONTEXT_MAX_CHARS_ENV, "4096")

    config.refresh_runtime_config_from_env()

    assert config.config.LOCAL_STT_BASE_URL == "http://127.0.0.1:8080/v1"
    assert config.config.LOCAL_STT_MODEL == "local-asr"
    assert config.config.LOCAL_STT_TIMEOUT_SECONDS == 9.0
    assert config.config.LOCAL_VAD_RMS_THRESHOLD == 420.0
    assert config.config.LOCAL_VAD_SILENCE_SECONDS == 0.8
    assert config.config.LOCAL_WAKE_WORD == "Johnny"
    assert config.config.LOCAL_WAKE_COOLDOWN_SECONDS == 4.0
    assert config.config.LOCAL_WAKE_FOLLOWUP_TIMEOUT_SECONDS == 0.75
    assert config.config.LOCAL_CONTEXT_WINDOW_SECONDS == 90.0
    assert config.config.LOCAL_CONTEXT_MAX_CHARS == 4096


@pytest.mark.parametrize("enabled, expected", [(None, False), ("0", False), ("1", True)])
def test_acoustic_wake_is_opt_in_and_refreshes(monkeypatch, enabled, expected) -> None:
    """Acoustic wake defaults off and respects refreshed process configuration."""
    monkeypatch.setattr(config.config, "LOCAL_ACOUSTIC_WAKE_ENABLED", False)
    monkeypatch.setattr(config.config, "LOCAL_ACOUSTIC_WAKE_MODEL_DIR", "")
    monkeypatch.delenv(config.LOCAL_ACOUSTIC_WAKE_ENABLED_ENV, raising=False)
    if enabled is not None:
        monkeypatch.setenv(config.LOCAL_ACOUSTIC_WAKE_ENABLED_ENV, enabled)
    monkeypatch.setenv(config.LOCAL_ACOUSTIC_WAKE_MODEL_DIR_ENV, " /private/models ")
    config.refresh_runtime_config_from_env()
    assert config.config.LOCAL_ACOUSTIC_WAKE_ENABLED is expected
    assert config.config.LOCAL_ACOUSTIC_WAKE_MODEL_DIR == "/private/models"


@pytest.mark.parametrize("enabled", ["0", "1"])
def test_audio_comparison_flags_refresh(monkeypatch, enabled) -> None:
    """Refresh both comparison switches without changing the output backend."""
    monkeypatch.setenv("AUDIO_DIAGNOSTICS_ENABLED", enabled)
    monkeypatch.setenv("SPEECH_WOBBLE_ENABLED", enabled)
    config.refresh_runtime_config_from_env()
    assert config.config.AUDIO_DIAGNOSTICS_ENABLED is (enabled == "1")
    assert config.config.SPEECH_WOBBLE_ENABLED is (enabled == "1")


def test_audio_comparison_defaults(monkeypatch) -> None:
    """Normal startup keeps speech wobble and avoids diagnostic reports."""
    monkeypatch.delenv("AUDIO_DIAGNOSTICS_ENABLED", raising=False)
    monkeypatch.delenv("SPEECH_WOBBLE_ENABLED", raising=False)
    config.refresh_runtime_config_from_env()
    assert config.config.AUDIO_DIAGNOSTICS_ENABLED is False
    assert config.config.SPEECH_WOBBLE_ENABLED is True


@pytest.mark.parametrize("value", [None, "0", "1"])
def test_local_interruption_flags_are_opt_in(monkeypatch, value) -> None:
    """Experimental microphone clearing and native override stay off by default."""
    for name in (
        config.GEMINI_LIVE_LOCAL_BARGE_IN_ENV,
        "OPENAI_LIVE_LOCAL_BARGE_IN_ENABLED",
        "MACOS_LOCAL_AUDIO_FLUSH_ENABLED",
    ):
        monkeypatch.setattr(config.config, name, False)
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    config.refresh_runtime_config_from_env()
    assert config.config.GEMINI_LIVE_LOCAL_BARGE_IN_ENABLED is (value == "1")
    assert config.config.OPENAI_LIVE_LOCAL_BARGE_IN_ENABLED is (value == "1")
    assert config.config.MACOS_LOCAL_AUDIO_FLUSH_ENABLED is (value == "1")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, 0.5), ("0.7", 0.7), ("1", 1.0), ("0", 0.5), ("1.2", 0.5), ("high", 0.5), ("nan", 0.5)],
)
def test_acoustic_wake_threshold_resolution(monkeypatch: pytest.MonkeyPatch, raw: str | None, expected: float) -> None:
    """The acoustic wake threshold accepts (0, 1] and falls back to the frozen 0.5."""
    from reachy_mini_conversation_app.config import resolve_local_acoustic_wake_threshold

    if raw is None:
        monkeypatch.delenv("LOCAL_ACOUSTIC_WAKE_THRESHOLD", raising=False)
    else:
        monkeypatch.setenv("LOCAL_ACOUSTIC_WAKE_THRESHOLD", raw)

    assert resolve_local_acoustic_wake_threshold() == expected


@pytest.mark.parametrize(("raw", "expected"), [(None, "en"), ("", "en"), ("es", "es"), ("auto", ""), (" AUTO ", "")])
def test_transcription_language_auto_means_no_hint(
    monkeypatch: pytest.MonkeyPatch, raw: str | None, expected: str
) -> None:
    """Unset keeps the English default; "auto" gives no hint so each utterance's language is detected."""
    from reachy_mini_conversation_app.config import _normalize_transcription_language

    assert _normalize_transcription_language(raw) == expected


@pytest.mark.parametrize(
    ("port", "refresh", "expected"),
    [
        (None, None, (8090, 1)),
        ("9000", "900", (9000, 900)),
        ("http", "0", (8090, 1)),
        ("80", "99999", (8090, 1)),
    ],
)
def test_trmnl_display_settings_fall_back_to_defaults(
    monkeypatch: pytest.MonkeyPatch, port: str | None, refresh: str | None, expected: tuple
) -> None:
    """The display's port and refresh accept sane numbers and ignore anything else."""
    for name, raw in (("TRMNL_PORT", port), ("TRMNL_REFRESH_SECONDS", refresh)):
        if raw is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, raw)
    monkeypatch.setenv("TRMNL_ENABLED", "1")

    config.refresh_runtime_config_from_env()

    settings = config.config
    assert settings.TRMNL_ENABLED is True
    assert (settings.TRMNL_PORT, settings.TRMNL_REFRESH_SECONDS) == expected
    monkeypatch.delenv("TRMNL_ENABLED")
    config.refresh_runtime_config_from_env()


_WHATSAPP_SETTINGS = {
    "WHATSAPP_API_URL": " http://127.0.0.1:8792 ",
    "WHATSAPP_API_AUTH": "reachy:secret",
    "WHATSAPP_PHOTOS_CHAT": "120363416355796813@g.us",
}


@pytest.mark.parametrize("missing", [None, *_WHATSAPP_SETTINGS])
def test_whatsapp_photos_need_all_three_settings(monkeypatch: pytest.MonkeyPatch, missing: str | None) -> None:
    """The server URL, its password and the group id are read trimmed; photos are on only with all three."""
    from reachy_mini_conversation_app.main import _build_whatsapp
    from reachy_mini_conversation_app.whatsapp import WhatsAppLink
    from reachy_mini_conversation_app.tools.core_tools import whatsapp_photos_configured

    for name, value in _WHATSAPP_SETTINGS.items():
        monkeypatch.setenv(name, " " if name == missing else value)
    config.refresh_runtime_config_from_env()

    if missing is None:
        assert config.config.WHATSAPP_API_URL == "http://127.0.0.1:8792"
        assert whatsapp_photos_configured()
        assert isinstance(_build_whatsapp(), WhatsAppLink)
    else:
        assert getattr(config.config, missing) is None
        assert not whatsapp_photos_configured()
        assert _build_whatsapp() is None
    monkeypatch.undo()
    config.refresh_runtime_config_from_env()
