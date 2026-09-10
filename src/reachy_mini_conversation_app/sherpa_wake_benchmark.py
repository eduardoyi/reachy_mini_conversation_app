"""Standalone, metadata-only benchmark for a caller-selected sherpa-onnx wake keyword."""

import sys
import json
import math
import time
import wave
import struct
import hashlib
import logging
import argparse
import platform
import tempfile
import importlib
import unicodedata
from types import ModuleType
from typing import Literal, Protocol, TypeAlias
from pathlib import Path
from datetime import datetime, timezone
from importlib import metadata
from dataclasses import asdict, dataclass
from collections.abc import Callable, Iterator, Sequence


resource_module: ModuleType | None = None
try:
    resource_module = importlib.import_module("resource")
except ImportError:  # pragma: no cover - exercised by Windows users
    resource_module = None


logger = logging.getLogger(__name__)

DEFAULT_KEYWORD = "Reachy"
CANONICAL_KEYWORD_TAG = "@reachy"
SUPPORTED_KEYWORD_SPELLINGS = ("Reachy", "Richie", "Ritchie", "Richi", "Ricci")
MAX_KEYWORD_CANDIDATES = len(SUPPORTED_KEYWORD_SPELLINGS)
SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH_BYTES = 2
DEFAULT_CHUNK_MS = 320
DEFAULT_THRESHOLD = 0.20
DEFAULT_BOOST = 1.0
DEFAULT_NUM_THREADS = 2
DEFAULT_NUM_TRAILING_BLANKS = 1
DEFAULT_TAIL_PADDING_SECONDS = 0.66
DEFAULT_MAX_INPUT_SECONDS = 3_600.0
DEFAULT_MAX_DETECTIONS = 10_000
DEFAULT_MAX_DECODE_STEPS_PER_INPUT = 1_000_000
MIN_CHUNK_MS = 20
MAX_CHUNK_MS = 2_000
MAX_INPUT_SECONDS = 3_600.0
MAX_NUM_TRAILING_BLANKS = 128
MAX_DECODE_STEPS_PER_INPUT = 10_000_000
MODEL_HASH_CHUNK_BYTES = 1_048_576
_CANONICAL_KEYWORD_TAG_CASEFOLDED = CANONICAL_KEYWORD_TAG.casefold()

ExpectedLabel: TypeAlias = Literal["positive", "negative"]
InputFormat: TypeAlias = Literal["auto", "wav", "pcm16"]
Classification: TypeAlias = Literal["true_positive", "false_negative", "true_negative", "false_positive"]
KeywordCandidates: TypeAlias = str | Sequence[str]


def _validate_token_vocabulary(tokens_path: Path) -> frozenset[str]:
    """Load model symbols while rejecting collisions with the canonical result tag."""
    symbols: set[str] = set()
    try:
        with tokens_path.open("r", encoding="utf-8") as token_file:
            for line in token_file:
                line_without_newline = line.rstrip("\r\n")
                if not line_without_newline:
                    continue
                fields = line_without_newline.rsplit(maxsplit=1)
                if len(fields) != 2 or not fields[0]:
                    raise ModelUnavailableError("The model token vocabulary could not be validated")
                symbol = fields[0]
                if symbol.casefold() == _CANONICAL_KEYWORD_TAG_CASEFOLDED:
                    raise ModelUnavailableError("The model token vocabulary conflicts with the canonical keyword tag")
                symbols.add(symbol)
    except UnicodeError as error:
        raise ModelUnavailableError("The model token vocabulary could not be validated") from error
    except OSError as error:
        raise ModelUnavailableError("The model token vocabulary could not be validated") from error
    if not symbols:
        raise ModelUnavailableError("The model token vocabulary could not be validated")
    return frozenset(symbols)


class BenchmarkError(RuntimeError):
    """Base class for recoverable benchmark setup and execution errors."""


class DependencyUnavailableError(BenchmarkError):
    """Raised when the optional sherpa dependency is unavailable."""


class ModelUnavailableError(BenchmarkError):
    """Raised when caller-owned model assets are missing or cannot initialize."""


class AudioInputError(BenchmarkError):
    """Raised when an input fixture is not bounded, mono, 16 kHz PCM16 audio."""


@dataclass(frozen=True, slots=True)
class ModelFiles:
    """Identify caller-owned model assets without downloading or copying them."""

    encoder: Path
    decoder: Path
    joiner: Path
    tokens: Path
    bpe_model: Path

    @classmethod
    def from_directory(cls, directory: Path, *, prefer_int8: bool = False) -> "ModelFiles":
        """Resolve the published Spanish Zipformer files from a local directory."""
        if not directory.is_dir():
            raise ModelUnavailableError("The supplied model directory is unavailable")

        def select(stem: str) -> Path:
            fp32 = directory / f"{stem}.onnx"
            int8 = directory / f"{stem}.int8.onnx"
            candidates = (int8, fp32) if prefer_int8 else (fp32, int8)
            for candidate in candidates:
                if candidate.is_file():
                    return candidate
            raise ModelUnavailableError("The supplied model directory is missing required ONNX assets")

        required = {
            "tokens": directory / "tokens.txt",
            "bpe_model": directory / "bpe.model",
        }
        if not all(path.is_file() for path in required.values()):
            raise ModelUnavailableError("The supplied model directory is missing tokenizer assets")
        _validate_token_vocabulary(required["tokens"])
        return cls(
            encoder=select("encoder-epoch-20-avg-2-chunk-16-left-64"),
            decoder=select("decoder-epoch-20-avg-2-chunk-16-left-64"),
            joiner=select("joiner-epoch-20-avg-2-chunk-16-left-64"),
            tokens=required["tokens"],
            bpe_model=required["bpe_model"],
        )


def fingerprint_model(model: ModelFiles) -> str:
    """Return a path-free SHA-256 fingerprint of the exact model assets."""
    digest = hashlib.sha256(b"reachy-wake-model-assets-v1\0")
    assets = (
        ("encoder", model.encoder),
        ("decoder", model.decoder),
        ("joiner", model.joiner),
        ("tokens", model.tokens),
        ("bpe_model", model.bpe_model),
    )
    for role, path in assets:
        role_bytes = role.encode("ascii")
        digest.update(len(role_bytes).to_bytes(2, "big"))
        digest.update(role_bytes)
        asset_size = 0
        try:
            with path.open("rb") as asset:
                while chunk := asset.read(MODEL_HASH_CHUNK_BYTES):
                    digest.update(chunk)
                    asset_size += len(chunk)
        except OSError as error:
            raise ModelUnavailableError("The supplied model assets could not be fingerprinted") from error
        digest.update(asset_size.to_bytes(8, "big"))
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class FixtureSpec:
    """Describe one caller-owned fixture and its human-supplied expectation."""

    path: Path
    expected: ExpectedLabel
    input_format: InputFormat = "auto"

    def __post_init__(self) -> None:
        """Validate the fixture label and format."""
        if self.expected not in ("positive", "negative"):
            raise ValueError("Fixture expectation must be positive or negative")
        if self.input_format not in ("auto", "wav", "pcm16"):
            raise ValueError("Fixture format must be auto, wav, or pcm16")


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    """Configure one standalone fixture run."""

    model_directory: Path
    fixtures: tuple[FixtureSpec, ...] = ()
    keyword: str = DEFAULT_KEYWORD
    keyword_candidates: tuple[str, ...] = ()
    threshold: float = DEFAULT_THRESHOLD
    boost: float = DEFAULT_BOOST
    chunk_ms: int = DEFAULT_CHUNK_MS
    num_threads: int = DEFAULT_NUM_THREADS
    num_trailing_blanks: int = DEFAULT_NUM_TRAILING_BLANKS
    prefer_int8: bool = False
    tail_padding_seconds: float = DEFAULT_TAIL_PADDING_SECONDS
    max_input_seconds: float = DEFAULT_MAX_INPUT_SECONDS
    max_detections_per_input: int = DEFAULT_MAX_DETECTIONS
    max_decode_steps_per_input: int = DEFAULT_MAX_DECODE_STEPS_PER_INPUT

    def __post_init__(self) -> None:
        """Validate bounded benchmark settings."""
        raw_candidates = self.keyword_candidates if self.keyword_candidates else (self.keyword,)
        candidates = normalize_keyword_candidates(raw_candidates)
        object.__setattr__(self, "keyword", candidates[0])
        object.__setattr__(self, "keyword_candidates", candidates)
        if not math.isfinite(self.threshold) or not 0 <= self.threshold <= 1:
            raise ValueError("Keyword threshold must be finite and between 0 and 1")
        if not math.isfinite(self.boost) or self.boost <= 0:
            raise ValueError("Keyword boost must be a finite positive number")
        if any(
            isinstance(value, bool)
            for value in (
                self.chunk_ms,
                self.num_threads,
                self.num_trailing_blanks,
                self.max_detections_per_input,
                self.max_decode_steps_per_input,
            )
        ):
            raise ValueError("Integer benchmark settings must not be booleans")
        if any(
            type(value) is not int
            for value in (
                self.chunk_ms,
                self.num_threads,
                self.num_trailing_blanks,
                self.max_detections_per_input,
                self.max_decode_steps_per_input,
            )
        ):
            raise ValueError("Integer benchmark settings must be integers")
        if not MIN_CHUNK_MS <= self.chunk_ms <= MAX_CHUNK_MS:
            raise ValueError(f"Chunk size must be between {MIN_CHUNK_MS} and {MAX_CHUNK_MS} ms")
        if self.num_threads < 1 or self.num_threads > 64:
            raise ValueError("Thread count must be between 1 and 64")
        if not 0 <= self.num_trailing_blanks <= MAX_NUM_TRAILING_BLANKS:
            raise ValueError(f"Trailing blank count must be between 0 and {MAX_NUM_TRAILING_BLANKS}")
        if not math.isfinite(self.tail_padding_seconds) or not 0.1 <= self.tail_padding_seconds <= 2:
            raise ValueError("Tail padding must be finite and between 0.1 and 2 seconds")
        if not math.isfinite(self.max_input_seconds) or not 0 < self.max_input_seconds <= MAX_INPUT_SECONDS:
            raise ValueError("Input duration limit must be finite and between 0 and 3600 seconds")
        if self.max_detections_per_input < 1 or self.max_detections_per_input > DEFAULT_MAX_DETECTIONS:
            raise ValueError("Detection limit must be between 1 and 10000")
        if self.max_decode_steps_per_input < 1 or self.max_decode_steps_per_input > MAX_DECODE_STEPS_PER_INPUT:
            raise ValueError("Decode-step limit must be between 1 and 10000000")

    @property
    def chunk_samples(self) -> int:
        """Return the bounded number of samples accepted per decoder step."""
        return max(1, round(SAMPLE_RATE * self.chunk_ms / 1_000))

    @property
    def keyword_tag(self) -> str:
        """Return the canonical bare tag used to filter sherpa results."""
        return CANONICAL_KEYWORD_TAG.removeprefix("@")

    def validate_inputs(self) -> None:
        """Reject empty runs and missing fixture paths before loading the model."""
        if not self.fixtures:
            raise ValueError("At least one positive or negative fixture is required")
        if any(not fixture.path.is_file() for fixture in self.fixtures):
            raise AudioInputError("One or more caller-owned fixtures are unavailable")


class SpotterStream(Protocol):
    """Subset of sherpa's stream API used by the benchmark."""

    def accept_waveform(self, sample_rate: int, samples: Sequence[float]) -> None:
        """Accept normalized float samples."""

    def input_finished(self) -> None:
        """Signal end of the current input."""


class KeywordSpotter(Protocol):
    """Subset of sherpa's keyword spotter API used by the benchmark."""

    def create_stream(self) -> SpotterStream:
        """Create one independent stream."""

    def is_ready(self, stream: SpotterStream) -> bool:
        """Return whether decoding can proceed."""

    def decode_stream(self, stream: SpotterStream) -> None:
        """Decode one ready step."""

    def get_result(self, stream: SpotterStream) -> str:
        """Return a keyword result, which the benchmark never stores."""

    def reset_stream(self, stream: SpotterStream) -> None:
        """Reset a stream after a result."""


SpotterFactory: TypeAlias = Callable[[ModelFiles, Path, BenchmarkConfig], KeywordSpotter]
Text2Token: TypeAlias = Callable[..., object]


@dataclass(frozen=True, slots=True)
class FixtureResult:
    """Record one input outcome and its first exact-detection timing only."""

    index: int
    expected: ExpectedLabel
    classification: Classification
    detection_count: int
    audio_duration_seconds: float
    processing_duration_seconds: float
    real_time_factor: float | None
    fixture_start_to_result_p50_seconds: float | None
    fixture_start_to_result_p95_seconds: float | None


@dataclass(frozen=True, slots=True)
class _FixtureRun:
    """Keep the first-detection latency sample internal to aggregation."""

    result: FixtureResult
    latency_samples: tuple[float, ...]


@dataclass(slots=True)
class _DecodeState:
    """Track total decoder steps across one fixture's input and drain phases."""

    steps: int = 0


@dataclass(frozen=True, slots=True)
class ResourceMetrics:
    """Capture process metrics only when the host exposes a trustworthy value."""

    cpu_seconds: float | None
    cpu_percent: float | None
    peak_rss_bytes: int | None


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    """Aggregate metadata-only benchmark outcomes."""

    mode: Literal["fixtures"]
    keyword: str
    model_fingerprint: str
    sherpa_onnx_version: str | None
    sentencepiece_version: str | None
    pypinyin_version: str | None
    benchmark_started_at_utc: str
    sample_rate_hz: int
    platform_system: str
    platform_release: str
    machine_architecture: str
    python_version: str
    threshold: float
    boost: float
    chunk_ms: int
    num_threads: int
    num_trailing_blanks: int
    input_count: int
    positive_count: int
    negative_count: int
    true_positive_count: int
    false_negative_count: int
    true_negative_count: int
    false_positive_count: int
    detection_count: int
    negative_detection_count: int
    positive_fixture_start_to_result_p50_seconds: float | None
    positive_fixture_start_to_result_p95_seconds: float | None
    audio_duration_seconds: float
    negative_audio_duration_seconds: float
    processing_duration_seconds: float
    real_time_factor: float | None
    recall: float | None
    false_positives_per_hour: float | None
    max_decode_steps_per_input: int
    resources: ResourceMetrics
    fixture_results: tuple[FixtureResult, ...]
    keyword_candidates: tuple[str, ...] = (DEFAULT_KEYWORD,)
    keyword_tag: str = CANONICAL_KEYWORD_TAG

    def to_metadata(self) -> dict[str, object]:
        """Return a JSON-safe result with no input paths or payloads."""
        return {
            "schema_version": 1,
            "mode": self.mode,
            "keyword": self.keyword,
            "keyword_candidates": list(self.keyword_candidates),
            "keyword_tag": self.keyword_tag,
            "model_fingerprint": self.model_fingerprint,
            "sherpa_onnx_version": self.sherpa_onnx_version,
            "sentencepiece_version": self.sentencepiece_version,
            "pypinyin_version": self.pypinyin_version,
            "benchmark_started_at_utc": self.benchmark_started_at_utc,
            "sample_rate_hz": self.sample_rate_hz,
            "platform_system": self.platform_system,
            "platform_release": self.platform_release,
            "machine_architecture": self.machine_architecture,
            "python_version": self.python_version,
            "threshold": self.threshold,
            "boost": self.boost,
            "chunk_ms": self.chunk_ms,
            "num_threads": self.num_threads,
            "num_trailing_blanks": self.num_trailing_blanks,
            "input_count": self.input_count,
            "positive_count": self.positive_count,
            "negative_count": self.negative_count,
            "true_positive_count": self.true_positive_count,
            "false_negative_count": self.false_negative_count,
            "true_negative_count": self.true_negative_count,
            "false_positive_count": self.false_positive_count,
            "detection_count": self.detection_count,
            "negative_detection_count": self.negative_detection_count,
            "positive_fixture_start_to_result_p50_seconds": self.positive_fixture_start_to_result_p50_seconds,
            "positive_fixture_start_to_result_p95_seconds": self.positive_fixture_start_to_result_p95_seconds,
            "audio_duration_seconds": self.audio_duration_seconds,
            "negative_audio_duration_seconds": self.negative_audio_duration_seconds,
            "processing_duration_seconds": self.processing_duration_seconds,
            "real_time_factor": self.real_time_factor,
            "recall": self.recall,
            "false_positives_per_hour": self.false_positives_per_hour,
            "max_decode_steps_per_input": self.max_decode_steps_per_input,
            "cpu_seconds": self.resources.cpu_seconds,
            "cpu_percent": self.resources.cpu_percent,
            "peak_rss_bytes": self.resources.peak_rss_bytes,
            "fixtures": [asdict(result) for result in self.fixture_results],
        }


def load_sherpa_onnx() -> ModuleType:
    """Load the optional sherpa-onnx package without importing it at app startup."""
    try:
        return importlib.import_module("sherpa_onnx")
    except ImportError as error:
        raise DependencyUnavailableError(
            "sherpa-onnx is unavailable; install the optional wake-benchmark group"
        ) from error


def _installed_package_version(distribution: str) -> str | None:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None


def _installed_sherpa_onnx_version() -> str | None:
    version = _installed_package_version("sherpa-onnx")
    if version is None:
        module = sys.modules.get("sherpa_onnx")
        module_version = getattr(module, "__version__", None)
        return module_version if isinstance(module_version, str) and module_version else None
    return version


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _environment_identity() -> tuple[str, str, str, str]:
    return (
        platform.system() or "unknown",
        platform.release() or "unknown",
        platform.machine() or "unknown",
        platform.python_version() or "unknown",
    )


def _attribute(instance: object, name: str) -> object:
    value = getattr(instance, name, None)
    if value is None:
        raise ModelUnavailableError("The installed sherpa-onnx API is missing a required operation")
    return value


def _invoke(operation: object, *args: object, **kwargs: object) -> object:
    if not callable(operation):
        raise ModelUnavailableError("The installed sherpa-onnx API contains a non-callable operation")
    return operation(*args, **kwargs)


class _ModuleKeywordSpotter:
    """Adapt the dynamically imported sherpa object to the typed benchmark seam."""

    def __init__(self, implementation: object) -> None:
        self._implementation = implementation

    def create_stream(self) -> SpotterStream:
        stream = _invoke(_attribute(self._implementation, "create_stream"))
        return _Stream(stream)

    def is_ready(self, stream: SpotterStream) -> bool:
        return bool(_invoke(_attribute(self._implementation, "is_ready"), _raw_stream(stream)))

    def decode_stream(self, stream: SpotterStream) -> None:
        _invoke(_attribute(self._implementation, "decode_stream"), _raw_stream(stream))

    def get_result(self, stream: SpotterStream) -> str:
        result = _invoke(_attribute(self._implementation, "get_result"), _raw_stream(stream))
        if not isinstance(result, str):
            raise ModelUnavailableError("The installed sherpa-onnx API returned an invalid keyword result")
        return result

    def reset_stream(self, stream: SpotterStream) -> None:
        _invoke(_attribute(self._implementation, "reset_stream"), _raw_stream(stream))


class _Stream:
    """Adapt a dynamic sherpa stream while keeping its raw value private."""

    def __init__(self, raw: object) -> None:
        self.raw = raw

    def accept_waveform(self, sample_rate: int, samples: Sequence[float]) -> None:
        _invoke(_attribute(self.raw, "accept_waveform"), sample_rate, samples)

    def input_finished(self) -> None:
        _invoke(_attribute(self.raw, "input_finished"))


def _raw_stream(stream: SpotterStream) -> object:
    if not isinstance(stream, _Stream):
        raise ModelUnavailableError("The installed sherpa-onnx API returned an invalid stream")
    return stream.raw


def create_sherpa_spotter(model: ModelFiles, keyword_file: Path, config: BenchmarkConfig) -> KeywordSpotter:
    """Construct sherpa's CPU keyword spotter from caller-owned model assets."""
    module = load_sherpa_onnx()
    constructor = _attribute(module, "KeywordSpotter")
    try:
        implementation = _invoke(
            constructor,
            tokens=str(model.tokens),
            encoder=str(model.encoder),
            decoder=str(model.decoder),
            joiner=str(model.joiner),
            keywords_file=str(keyword_file),
            num_threads=config.num_threads,
            sample_rate=SAMPLE_RATE,
            feature_dim=80,
            keywords_score=config.boost,
            keywords_threshold=config.threshold,
            num_trailing_blanks=config.num_trailing_blanks,
            provider="cpu",
        )
    except (AssertionError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise ModelUnavailableError("The supplied model assets could not initialize sherpa-onnx") from error
    return _ModuleKeywordSpotter(implementation)


_SUPPORTED_KEYWORD_BY_NORMALIZED = {
    unicodedata.normalize("NFKC", spelling).casefold(): spelling for spelling in SUPPORTED_KEYWORD_SPELLINGS
}


def _normalize_keyword_candidate(keyword: str) -> str:
    if not isinstance(keyword, str):
        raise TypeError("Keyword candidates must be strings")
    normalized = " ".join(unicodedata.normalize("NFKC", keyword).casefold().split())
    candidate = _SUPPORTED_KEYWORD_BY_NORMALIZED.get(normalized)
    if candidate is None:
        allowed = ", ".join(SUPPORTED_KEYWORD_SPELLINGS)
        raise ValueError(f"Keyword must be one of: {allowed}")
    return candidate


def normalize_keyword_candidates(keywords: KeywordCandidates) -> tuple[str, ...]:
    """Normalize and bound the explicit acoustic keyword spellings."""
    if isinstance(keywords, str):
        iterator = iter((keywords,))
    else:
        try:
            iterator = iter(keywords)
        except TypeError as error:
            raise TypeError("Keyword candidates must be a string or sequence of strings") from error
    normalized: list[str] = []
    seen: set[str] = set()
    inspected = 0
    for value in iterator:
        inspected += 1
        if inspected > MAX_KEYWORD_CANDIDATES * 2:
            raise ValueError(f"At most {MAX_KEYWORD_CANDIDATES} keyword candidates are supported")
        if not isinstance(value, str):
            raise TypeError("Keyword candidates must be strings")
        candidate = _normalize_keyword_candidate(value)
        key = candidate.casefold()
        if key not in seen:
            if len(normalized) >= MAX_KEYWORD_CANDIDATES:
                raise ValueError(f"At most {MAX_KEYWORD_CANDIDATES} keyword candidates are supported")
            seen.add(key)
            normalized.append(candidate)
    if not normalized:
        raise ValueError("At least one keyword candidate is required")
    return tuple(normalized)


def _matches_keyword_result(result: str, keyword_tag: str) -> bool:
    normalized_result = result.strip().casefold()
    normalized_tag = keyword_tag.strip().casefold().removeprefix("@")
    if normalized_tag != CANONICAL_KEYWORD_TAG.removeprefix("@"):
        return False
    return normalized_result in (normalized_tag, CANONICAL_KEYWORD_TAG)


def generate_keyword_file(
    output: Path,
    model: ModelFiles,
    keywords: KeywordCandidates,
    *,
    text2token: Text2Token | None = None,
) -> None:
    """Generate one canonical-tagged BPE entry for each configured spelling."""
    vocabulary_symbols = _validate_token_vocabulary(model.tokens)
    normalized_keywords = normalize_keyword_candidates(keywords)
    if text2token is None:
        module = load_sherpa_onnx()
        candidate = _attribute(module, "text2token")
        if not callable(candidate):
            raise DependencyUnavailableError("The installed sherpa-onnx package lacks text2token")
        text2token = candidate
    lines: list[str] = []
    seen_lines: set[str] = set()
    for keyword in normalized_keywords:
        try:
            encoded = text2token(
                [keyword.casefold()],
                tokens=str(model.tokens),
                tokens_type="bpe",
                bpe_model=str(model.bpe_model),
            )
        except ImportError as error:
            raise DependencyUnavailableError("The sherpa tokenizer dependency is unavailable") from error
        except AssertionError as error:
            raise ModelUnavailableError("The model tokenizer could not generate the keyword file") from error
        except UnicodeError as error:
            raise ModelUnavailableError("The model tokenizer could not generate the keyword file") from error
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise ModelUnavailableError("The model tokenizer could not generate the keyword file") from error
        if not isinstance(encoded, Sequence) or isinstance(encoded, (str, bytes)) or len(encoded) != 1:
            raise ModelUnavailableError("The model tokenizer returned an invalid keyword encoding")
        encoded_line = encoded[0]
        if not isinstance(encoded_line, Sequence) or isinstance(encoded_line, (str, bytes)):
            raise ModelUnavailableError("The model tokenizer returned an invalid keyword encoding")
        tokens = [token for token in encoded_line if isinstance(token, str)]
        if len(tokens) != len(encoded_line):
            raise ModelUnavailableError("The model tokenizer returned an invalid keyword encoding")
        if not tokens:
            raise ModelUnavailableError("The model tokenizer returned an empty keyword encoding")
        if any(
            not token
            or any(character.isspace() or unicodedata.category(character).startswith("C") for character in token)
            for token in tokens
        ):
            raise ModelUnavailableError("The model tokenizer returned unsafe keyword tokens")
        if any(token.casefold() == _CANONICAL_KEYWORD_TAG_CASEFOLDED for token in tokens):
            raise ModelUnavailableError(
                "The model tokenizer returned a token conflicting with the canonical keyword tag"
            )
        if any(token not in vocabulary_symbols for token in tokens):
            raise ModelUnavailableError("The model tokenizer returned a token absent from the model vocabulary")
        line = f"{' '.join(tokens)} {CANONICAL_KEYWORD_TAG}"
        if line not in seen_lines:
            seen_lines.add(line)
            lines.append(line)
    try:
        output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as error:
        raise ModelUnavailableError("The temporary keyword file could not be written") from error


def _format_for_path(path: Path, requested: InputFormat) -> InputFormat:
    if requested != "auto":
        return requested
    return "wav" if path.suffix.casefold() == ".wav" else "pcm16"


def _float_samples(pcm16: bytes) -> list[float]:
    if len(pcm16) % SAMPLE_WIDTH_BYTES:
        raise AudioInputError("PCM16 input ended on an incomplete sample")
    values = struct.unpack(f"<{len(pcm16) // SAMPLE_WIDTH_BYTES}h", pcm16)
    return [sample / 32_768.0 for sample in values]


def iter_pcm16_chunks(
    fixture: FixtureSpec,
    chunk_samples: int,
    max_seconds: float,
) -> Iterator[tuple[list[float], int]]:
    """Yield bounded normalized samples from one WAV or raw PCM16 fixture."""
    if type(chunk_samples) is not int or chunk_samples < 1:
        raise ValueError("Chunk size must be a positive integer")
    if not math.isfinite(max_seconds) or not 0 < max_seconds <= MAX_INPUT_SECONDS:
        raise ValueError("Input duration limit must be finite and between 0 and 3600 seconds")
    input_format = _format_for_path(fixture.path, fixture.input_format)
    max_bytes = int(max_seconds * SAMPLE_RATE * SAMPLE_WIDTH_BYTES)
    if input_format == "wav":
        try:
            source = wave.open(str(fixture.path), "rb")
        except (OSError, EOFError, wave.Error) as error:
            raise AudioInputError("WAV fixture could not be opened") from error
        with source:
            if (
                source.getnchannels() != CHANNELS
                or source.getsampwidth() != SAMPLE_WIDTH_BYTES
                or source.getframerate() != SAMPLE_RATE
                or source.getcomptype() != "NONE"
            ):
                raise AudioInputError("WAV fixture must be mono, 16 kHz, uncompressed PCM16")
            expected_frames = source.getnframes()
            if expected_frames <= 0:
                raise AudioInputError("Audio fixture is empty")
            bytes_read = 0
            decoded_frames = 0
            while True:
                pcm16 = source.readframes(chunk_samples)
                if not pcm16:
                    break
                bytes_read += len(pcm16)
                if bytes_read > max_bytes:
                    raise AudioInputError("Audio fixture exceeds the configured duration bound")
                samples = _float_samples(pcm16)
                if not samples:
                    raise AudioInputError("WAV fixture payload is empty")
                decoded_frames += len(samples)
                yield samples, len(samples)
            if decoded_frames != expected_frames:
                raise AudioInputError("WAV fixture payload is truncated")
    else:
        try:
            file_size = fixture.path.stat().st_size
        except OSError as error:
            raise AudioInputError("PCM16 fixture could not be inspected") from error
        if file_size <= 0:
            raise AudioInputError("Audio fixture is empty")
        if file_size % SAMPLE_WIDTH_BYTES or file_size > max_bytes:
            raise AudioInputError("Raw PCM16 fixture exceeds the configured format or duration bound")
        try:
            source_file = fixture.path.open("rb")
        except OSError as error:
            raise AudioInputError("PCM16 fixture could not be opened") from error
        with source_file:
            while True:
                pcm16 = source_file.read(chunk_samples * SAMPLE_WIDTH_BYTES)
                if not pcm16:
                    break
                samples = _float_samples(pcm16)
                yield samples, len(samples)


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _resource_snapshot() -> tuple[float, int | None]:
    if resource_module is None:
        return time.process_time(), None
    usage = resource_module.getrusage(resource_module.RUSAGE_SELF)
    rss = int(usage.ru_maxrss)
    if sys.platform == "darwin":
        return time.process_time(), rss
    return time.process_time(), rss * 1_024


def _resource_metrics(start_cpu: float, start_wall: float, start_rss: int | None) -> ResourceMetrics:
    end_cpu, end_rss = _resource_snapshot()
    elapsed_wall = time.perf_counter() - start_wall
    cpu_seconds = max(0.0, end_cpu - start_cpu)
    cpu_percent = cpu_seconds / elapsed_wall * 100 if elapsed_wall > 0 else None
    peak_rss = end_rss if end_rss is not None else start_rss
    return ResourceMetrics(cpu_seconds, cpu_percent, peak_rss)


def _classification(expected: ExpectedLabel, detection_count: int) -> Classification:
    detected = detection_count > 0
    if expected == "positive":
        return "true_positive" if detected else "false_negative"
    return "false_positive" if detected else "true_negative"


def _decode_one_step(
    spotter: KeywordSpotter,
    stream: SpotterStream,
    decode_state: _DecodeState,
    max_decode_steps: int,
) -> None:
    if decode_state.steps >= max_decode_steps:
        raise AudioInputError("Fixture exceeded the configured decode-step bound")
    decode_state.steps += 1
    spotter.decode_stream(stream)


def _decode_stream(
    spotter: KeywordSpotter,
    stream: SpotterStream,
    samples: Sequence[float],
    fixture_started_at: float,
    latency_samples: list[float],
    max_detections: int,
    keyword_tag: str,
    detection_count: int,
    decode_state: _DecodeState,
    max_decode_steps: int,
) -> int:
    stream.accept_waveform(SAMPLE_RATE, samples)
    while spotter.is_ready(stream):
        _decode_one_step(spotter, stream, decode_state, max_decode_steps)
        result = spotter.get_result(stream)
        if not isinstance(result, str):
            raise ModelUnavailableError("The installed sherpa-onnx API returned an invalid keyword result")
        if not result.strip():
            continue
        matched = _matches_keyword_result(result, keyword_tag)
        spotter.reset_stream(stream)
        if matched:
            detection_count += 1
            if detection_count > max_detections:
                raise AudioInputError("Fixture exceeded the configured detection bound")
            if not latency_samples:
                latency_samples.append(max(0.0, time.perf_counter() - fixture_started_at))
    return detection_count


def _run_fixture(
    fixture: FixtureSpec,
    index: int,
    spotter: KeywordSpotter,
    config: BenchmarkConfig,
) -> _FixtureRun:
    fixture_started_at = time.perf_counter()
    stream = spotter.create_stream()
    audio_samples = 0
    detection_count = 0
    latencies: list[float] = []
    decode_state = _DecodeState()
    try:
        for samples, sample_count in iter_pcm16_chunks(fixture, config.chunk_samples, config.max_input_seconds):
            audio_samples += sample_count
            detection_count = _decode_stream(
                spotter,
                stream,
                samples,
                fixture_started_at,
                latencies,
                config.max_detections_per_input,
                config.keyword_tag,
                detection_count,
                decode_state,
                config.max_decode_steps_per_input,
            )
        tail_padding = [0.0] * round(config.tail_padding_seconds * SAMPLE_RATE)
        stream.accept_waveform(SAMPLE_RATE, tail_padding)
        stream.input_finished()
        while spotter.is_ready(stream):
            _decode_one_step(spotter, stream, decode_state, config.max_decode_steps_per_input)
            result = spotter.get_result(stream)
            if not isinstance(result, str):
                raise ModelUnavailableError("The installed sherpa-onnx API returned an invalid keyword result")
            if not result.strip():
                continue
            matched = _matches_keyword_result(result, config.keyword_tag)
            spotter.reset_stream(stream)
            if matched:
                detection_count += 1
                if detection_count > config.max_detections_per_input:
                    raise AudioInputError("Fixture exceeded the configured detection bound")
                if not latencies:
                    latencies.append(max(0.0, time.perf_counter() - fixture_started_at))
    except (AudioInputError, BenchmarkError):
        raise
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise BenchmarkError("sherpa-onnx failed while processing an audio fixture") from error
    processing_duration = time.perf_counter() - fixture_started_at
    audio_duration = audio_samples / SAMPLE_RATE
    return _FixtureRun(
        result=FixtureResult(
            index=index,
            expected=fixture.expected,
            classification=_classification(fixture.expected, detection_count),
            detection_count=detection_count,
            audio_duration_seconds=audio_duration,
            processing_duration_seconds=processing_duration,
            real_time_factor=processing_duration / audio_duration if audio_duration > 0 else None,
            fixture_start_to_result_p50_seconds=_percentile(latencies, 0.50),
            fixture_start_to_result_p95_seconds=_percentile(latencies, 0.95),
        ),
        latency_samples=tuple(latencies),
    )


def run_benchmark(
    config: BenchmarkConfig, *, spotter_factory: SpotterFactory = create_sherpa_spotter
) -> BenchmarkResult:
    """Run the benchmark against caller-owned local audio fixtures."""
    benchmark_started_at_utc = _utc_timestamp()
    config.validate_inputs()
    try:
        model = ModelFiles.from_directory(config.model_directory, prefer_int8=config.prefer_int8)
        model_fingerprint = fingerprint_model(model)
    except AssertionError as error:
        raise ModelUnavailableError("The supplied model assets could not be prepared") from error
    sherpa_onnx_version = _installed_sherpa_onnx_version()
    sentencepiece_version = _installed_package_version("sentencepiece")
    pypinyin_version = _installed_package_version("pypinyin")
    start_wall = time.perf_counter()
    start_cpu, start_rss = _resource_snapshot()
    with tempfile.TemporaryDirectory(prefix="wake-kws-") as temporary_directory:
        keyword_file = Path(temporary_directory) / "keywords.txt"
        try:
            generate_keyword_file(keyword_file, model, config.keyword_candidates)
        except BenchmarkError:
            raise
        except AssertionError as error:
            raise ModelUnavailableError("The model tokenizer could not generate the keyword file") from error
        try:
            spotter = spotter_factory(model, keyword_file, config)
        except BenchmarkError:
            raise
        except (AssertionError, OSError, RuntimeError, TypeError, ValueError) as error:
            raise ModelUnavailableError("The supplied model assets could not initialize sherpa-onnx") from error
        fixture_runs = tuple(
            _run_fixture(fixture, index, spotter, config) for index, fixture in enumerate(config.fixtures)
        )
    resources = _resource_metrics(start_cpu, start_wall, start_rss)
    fixture_results = tuple(run.result for run in fixture_runs)
    latencies = [
        latency for run in fixture_runs if run.result.expected == "positive" for latency in run.latency_samples
    ]
    audio_duration = sum(result.audio_duration_seconds for result in fixture_results)
    negative_audio_duration = sum(
        result.audio_duration_seconds for result in fixture_results if result.expected == "negative"
    )
    processing_duration = sum(result.processing_duration_seconds for result in fixture_results)
    negative_detection_count = sum(
        result.detection_count for result in fixture_results if result.expected == "negative"
    )
    true_positive_count = sum(result.classification == "true_positive" for result in fixture_results)
    positive_count = sum(result.expected == "positive" for result in fixture_results)
    platform_system, platform_release, machine_architecture, python_version = _environment_identity()
    return BenchmarkResult(
        mode="fixtures",
        keyword=config.keyword.strip(),
        keyword_candidates=config.keyword_candidates,
        keyword_tag=CANONICAL_KEYWORD_TAG,
        model_fingerprint=model_fingerprint,
        sherpa_onnx_version=sherpa_onnx_version,
        sentencepiece_version=sentencepiece_version,
        pypinyin_version=pypinyin_version,
        benchmark_started_at_utc=benchmark_started_at_utc,
        sample_rate_hz=SAMPLE_RATE,
        platform_system=platform_system,
        platform_release=platform_release,
        machine_architecture=machine_architecture,
        python_version=python_version,
        threshold=config.threshold,
        boost=config.boost,
        chunk_ms=config.chunk_ms,
        num_threads=config.num_threads,
        num_trailing_blanks=config.num_trailing_blanks,
        input_count=len(fixture_results),
        positive_count=positive_count,
        negative_count=sum(result.expected == "negative" for result in fixture_results),
        true_positive_count=true_positive_count,
        false_negative_count=sum(result.classification == "false_negative" for result in fixture_results),
        true_negative_count=sum(result.classification == "true_negative" for result in fixture_results),
        false_positive_count=sum(result.classification == "false_positive" for result in fixture_results),
        detection_count=sum(result.detection_count for result in fixture_results),
        negative_detection_count=negative_detection_count,
        positive_fixture_start_to_result_p50_seconds=_percentile(latencies, 0.50),
        positive_fixture_start_to_result_p95_seconds=_percentile(latencies, 0.95),
        audio_duration_seconds=audio_duration,
        negative_audio_duration_seconds=negative_audio_duration,
        processing_duration_seconds=processing_duration,
        real_time_factor=processing_duration / audio_duration if audio_duration > 0 else None,
        recall=true_positive_count / positive_count if positive_count > 0 else None,
        false_positives_per_hour=negative_detection_count / negative_audio_duration * 3_600
        if negative_audio_duration > 0
        else None,
        max_decode_steps_per_input=config.max_decode_steps_per_input,
        resources=resources,
        fixture_results=fixture_results,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark a local sherpa-onnx wake keyword")
    parser.add_argument("--model-dir", type=Path, required=True, help="Caller-owned extracted checkpoint directory")
    parser.add_argument(
        "--positive", type=Path, action="append", default=[], help="Expected-wake WAV or PCM16 fixture"
    )
    parser.add_argument(
        "--negative", type=Path, action="append", default=[], help="Expected-negative WAV or PCM16 fixture"
    )
    parser.add_argument("--input-format", choices=("auto", "wav", "pcm16"), default="auto")
    parser.add_argument(
        "--keyword",
        action="append",
        help="Allowed keyword spelling; repeat to benchmark a bounded candidate set",
    )
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--boost", type=float, default=DEFAULT_BOOST)
    parser.add_argument("--chunk-ms", type=int, default=DEFAULT_CHUNK_MS)
    parser.add_argument("--num-threads", type=int, default=DEFAULT_NUM_THREADS)
    parser.add_argument("--num-trailing-blanks", type=int, default=DEFAULT_NUM_TRAILING_BLANKS)
    parser.add_argument("--max-decode-steps-per-input", type=int, default=DEFAULT_MAX_DECODE_STEPS_PER_INPUT)
    parser.add_argument("--int8", action="store_true", help="Prefer matching int8 encoder, decoder, and joiner assets")
    parser.add_argument("--json", action="store_true", help="Emit metadata-only JSON instead of the summary")
    parser.add_argument("--output", type=Path, help="Optional metadata-only JSON output path")
    return parser


def _config_from_args(args: argparse.Namespace) -> BenchmarkConfig:
    fixtures = tuple(
        [FixtureSpec(path, "positive", args.input_format) for path in args.positive]
        + [FixtureSpec(path, "negative", args.input_format) for path in args.negative]
    )
    keywords = args.keyword or (DEFAULT_KEYWORD,)
    return BenchmarkConfig(
        model_directory=args.model_dir,
        fixtures=fixtures,
        keyword_candidates=tuple(keywords),
        threshold=args.threshold,
        boost=args.boost,
        chunk_ms=args.chunk_ms,
        num_threads=args.num_threads,
        num_trailing_blanks=args.num_trailing_blanks,
        max_decode_steps_per_input=args.max_decode_steps_per_input,
        prefer_int8=args.int8,
    )


def _summary(result: BenchmarkResult) -> str:
    resources = result.resources
    metrics = [
        f"inputs={result.input_count}",
        f"true_positive={result.true_positive_count}",
        f"false_negative={result.false_negative_count}",
        f"true_negative={result.true_negative_count}",
        f"false_positive={result.false_positive_count}",
        f"detections={result.detection_count}",
        f"negative_detections={result.negative_detection_count}",
        f"recall={result.recall:.3f}" if result.recall is not None else "recall=not_measured",
        f"false_positives_per_hour={result.false_positives_per_hour:.3f}"
        if result.false_positives_per_hour is not None
        else "false_positives_per_hour=not_measured",
        f"positive_fixture_start_to_result_p50_ms={result.positive_fixture_start_to_result_p50_seconds * 1000:.1f}"
        if result.positive_fixture_start_to_result_p50_seconds is not None
        else "positive_fixture_start_to_result_p50_ms=not_measured",
        f"positive_fixture_start_to_result_p95_ms={result.positive_fixture_start_to_result_p95_seconds * 1000:.1f}"
        if result.positive_fixture_start_to_result_p95_seconds is not None
        else "positive_fixture_start_to_result_p95_ms=not_measured",
        f"real_time_factor={result.real_time_factor:.3f}"
        if result.real_time_factor is not None
        else "real_time_factor=not_measured",
        f"cpu_percent={resources.cpu_percent:.1f}"
        if resources.cpu_percent is not None
        else "cpu_percent=not_measured",
        f"peak_rss_mb={resources.peak_rss_bytes / 1_000_000:.1f}"
        if resources.peak_rss_bytes is not None
        else "peak_rss_mb=not_measured",
    ]
    return "Reachy wake benchmark: " + " ".join(metrics)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the standalone benchmark CLI and return a process exit code."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        config = _config_from_args(args)
        result = run_benchmark(config)
    except (BenchmarkError, ValueError) as error:
        logger.error("Wake benchmark unavailable: %s", error)
        return 2
    metadata = json.dumps(result.to_metadata(), ensure_ascii=True, sort_keys=True)
    if args.json:
        print(metadata)
    else:
        print(_summary(result))
    if args.output is not None:
        try:
            args.output.write_text(metadata + "\n", encoding="utf-8")
        except OSError:
            logger.error("Wake benchmark metadata could not be written")
            return 2
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the console entry point
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    raise SystemExit(main())


__all__ = [
    "AudioInputError",
    "BenchmarkConfig",
    "BenchmarkError",
    "BenchmarkResult",
    "CANONICAL_KEYWORD_TAG",
    "DEFAULT_KEYWORD",
    "DependencyUnavailableError",
    "FixtureResult",
    "FixtureSpec",
    "InputFormat",
    "MAX_KEYWORD_CANDIDATES",
    "ModelFiles",
    "ModelUnavailableError",
    "ResourceMetrics",
    "SUPPORTED_KEYWORD_SPELLINGS",
    "create_sherpa_spotter",
    "fingerprint_model",
    "generate_keyword_file",
    "iter_pcm16_chunks",
    "load_sherpa_onnx",
    "normalize_keyword_candidates",
    "run_benchmark",
]
