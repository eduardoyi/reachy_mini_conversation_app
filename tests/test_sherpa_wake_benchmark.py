"""Tests for the standalone fixture-only sherpa wake benchmark."""

import sys
import json
import time
import wave
import types
import struct
import importlib
from typing import Literal
from pathlib import Path
from collections.abc import Callable, Sequence

import pytest

import reachy_mini_conversation_app.sherpa_wake_benchmark as benchmark


MODEL_STEMS = (
    "encoder-epoch-20-avg-2-chunk-16-left-64",
    "decoder-epoch-20-avg-2-chunk-16-left-64",
    "joiner-epoch-20-avg-2-chunk-16-left-64",
)


def _write_model_directory(directory: Path, *, int8: bool = False) -> None:
    """Create caller-owned model placeholders for setup tests."""
    directory.mkdir(parents=True, exist_ok=True)
    suffix = ".int8.onnx" if int8 else ".onnx"
    for stem in MODEL_STEMS:
        (directory / f"{stem}{suffix}").write_bytes(b"model")
    (directory / "tokens.txt").write_text(
        "▁ti 1\nto 2\n▁rea 3\nchy 4\n▁reachy 5\n▁richie 6\n▁ritchie 7\n▁richy 8\n▁riccy 9\n▁same 10\ntoken 11\n",
        encoding="utf-8",
    )
    (directory / "bpe.model").write_bytes(b"tokenizer")


def _write_pcm16(path: Path, sample_count: int = 320) -> None:
    """Write deterministic mono PCM16 samples."""
    path.write_bytes(struct.pack(f"<{sample_count}h", *([1_024] * sample_count)))


def _write_wav(path: Path, sample_count: int = 320, *, channels: int = 1, sample_rate: int = 16_000) -> None:
    """Write deterministic PCM WAV samples."""
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(struct.pack(f"<{sample_count * channels}h", *([1_024] * (sample_count * channels))))


def _write_empty_wav_payload(path: Path) -> None:
    """Write a WAV header whose declared payload has been removed."""
    _write_wav(path, sample_count=1)
    path.write_bytes(path.read_bytes()[:44])


def _write_truncated_wav_payload(path: Path) -> None:
    """Write a WAV whose payload is shorter than its declared frame count."""
    _write_wav(path, sample_count=2)
    path.write_bytes(path.read_bytes()[:-2])


class FakeStream:
    """Minimal stream that exposes scripted result events."""

    def __init__(self, results: Sequence[str]) -> None:
        """Initialize scripted results and input observations."""
        self.results = list(results)
        self.accepted: list[tuple[int, list[float]]] = []
        self.finished = False

    def accept_waveform(self, sample_rate: int, samples: Sequence[float]) -> None:
        """Record normalized input samples."""
        self.accepted.append((sample_rate, list(samples)))

    def input_finished(self) -> None:
        """Mark the stream as finished."""
        self.finished = True


class FakeSpotter:
    """Deterministic spotter with one result script per fixture."""

    def __init__(self, result_scripts: Sequence[Sequence[str]]) -> None:
        """Initialize one result script for each fixture stream."""
        self.result_scripts = [list(script) for script in result_scripts]
        self.streams: list[FakeStream] = []
        self.reset_count = 0

    def create_stream(self) -> FakeStream:
        """Create the next fixture stream."""
        stream = FakeStream(self.result_scripts[len(self.streams)])
        self.streams.append(stream)
        return stream

    def is_ready(self, stream: benchmark.SpotterStream) -> bool:
        """Report whether a scripted result remains."""
        if not isinstance(stream, FakeStream):
            raise TypeError("unexpected stream")
        return bool(stream.results)

    def decode_stream(self, _stream: benchmark.SpotterStream) -> None:
        """Consume no state because results are scripted."""
        return None

    def get_result(self, stream: benchmark.SpotterStream) -> str:
        """Return the next scripted result."""
        if not isinstance(stream, FakeStream):
            raise TypeError("unexpected stream")
        return stream.results.pop(0)

    def reset_stream(self, _stream: benchmark.SpotterStream) -> None:
        """Record the benchmark reset after a non-empty result."""
        self.reset_count += 1


def _config(
    model_directory: Path,
    fixture_specs: tuple[benchmark.FixtureSpec, ...],
    **kwargs: str | float | int | bool | tuple[benchmark.FixtureSpec, ...] | tuple[str, ...],
) -> benchmark.BenchmarkConfig:
    """Build a benchmark config with deterministic fixture arguments."""
    configured_fixtures = kwargs.get("fixtures", fixture_specs)
    if not isinstance(configured_fixtures, tuple):
        raise TypeError("fixtures must be a tuple")
    keyword_candidates_value = kwargs.get("keyword_candidates", ())
    if not isinstance(keyword_candidates_value, tuple):
        raise TypeError("keyword_candidates must be a tuple")
    keyword_candidates_list: list[str] = []
    for value in keyword_candidates_value:
        if not isinstance(value, str):
            raise TypeError("keyword_candidates must contain strings")
        keyword_candidates_list.append(value)
    keyword_candidates = tuple(keyword_candidates_list)
    keyword = kwargs.get("keyword", benchmark.DEFAULT_KEYWORD)
    threshold = kwargs.get("threshold", benchmark.DEFAULT_THRESHOLD)
    boost = kwargs.get("boost", benchmark.DEFAULT_BOOST)
    chunk_ms = kwargs.get("chunk_ms", benchmark.DEFAULT_CHUNK_MS)
    num_threads = kwargs.get("num_threads", benchmark.DEFAULT_NUM_THREADS)
    num_trailing_blanks = kwargs.get("num_trailing_blanks", benchmark.DEFAULT_NUM_TRAILING_BLANKS)
    tail_padding_seconds = kwargs.get("tail_padding_seconds", benchmark.DEFAULT_TAIL_PADDING_SECONDS)
    max_input_seconds = kwargs.get("max_input_seconds", benchmark.DEFAULT_MAX_INPUT_SECONDS)
    max_detections_per_input = kwargs.get("max_detections_per_input", benchmark.DEFAULT_MAX_DETECTIONS)
    max_decode_steps_per_input = kwargs.get("max_decode_steps_per_input", benchmark.DEFAULT_MAX_DECODE_STEPS_PER_INPUT)
    prefer_int8 = kwargs.get("prefer_int8", False)
    if not isinstance(keyword, str):
        raise TypeError("keyword must be a string")
    if not isinstance(threshold, (float, int)) or isinstance(threshold, bool):
        raise TypeError("threshold must be numeric")
    if not isinstance(boost, (float, int)) or isinstance(boost, bool):
        raise TypeError("boost must be numeric")
    if not isinstance(chunk_ms, int) or isinstance(chunk_ms, bool):
        raise TypeError("chunk_ms must be an integer")
    if not isinstance(num_threads, int) or isinstance(num_threads, bool):
        raise TypeError("num_threads must be an integer")
    if not isinstance(num_trailing_blanks, int) or isinstance(num_trailing_blanks, bool):
        raise TypeError("num_trailing_blanks must be an integer")
    if not isinstance(tail_padding_seconds, (float, int)) or isinstance(tail_padding_seconds, bool):
        raise TypeError("tail_padding_seconds must be numeric")
    if not isinstance(max_input_seconds, (float, int)) or isinstance(max_input_seconds, bool):
        raise TypeError("max_input_seconds must be numeric")
    if not isinstance(max_detections_per_input, int) or isinstance(max_detections_per_input, bool):
        raise TypeError("max_detections_per_input must be an integer")
    if not isinstance(max_decode_steps_per_input, int) or isinstance(max_decode_steps_per_input, bool):
        raise TypeError("max_decode_steps_per_input must be an integer")
    if not isinstance(prefer_int8, bool):
        raise TypeError("prefer_int8 must be a boolean")
    return benchmark.BenchmarkConfig(
        model_directory=model_directory,
        fixtures=configured_fixtures,
        keyword=keyword,
        keyword_candidates=keyword_candidates,
        threshold=float(threshold),
        boost=float(boost),
        chunk_ms=chunk_ms,
        num_threads=num_threads,
        num_trailing_blanks=num_trailing_blanks,
        tail_padding_seconds=float(tail_padding_seconds),
        max_input_seconds=float(max_input_seconds),
        max_detections_per_input=max_detections_per_input,
        max_decode_steps_per_input=max_decode_steps_per_input,
        prefer_int8=prefer_int8,
    )


def _factory(
    spotter: benchmark.KeywordSpotter,
) -> Callable[[benchmark.ModelFiles, Path, benchmark.BenchmarkConfig], benchmark.KeywordSpotter]:
    """Adapt a fake spotter to the benchmark factory contract."""

    def create(
        _model: benchmark.ModelFiles, _keyword_file: Path, _config: benchmark.BenchmarkConfig
    ) -> benchmark.KeywordSpotter:
        return spotter

    return create


def test_model_files_select_fp32_or_int8_assets(tmp_path: Path) -> None:
    """Model resolution uses only existing caller-owned files."""
    _write_model_directory(tmp_path)
    (tmp_path / f"{MODEL_STEMS[0]}.int8.onnx").write_bytes(b"int8")

    fp32 = benchmark.ModelFiles.from_directory(tmp_path)
    int8 = benchmark.ModelFiles.from_directory(tmp_path, prefer_int8=True)

    assert fp32.encoder.name.endswith(".onnx")
    assert not fp32.encoder.name.endswith(".int8.onnx")
    assert int8.encoder.name.endswith(".int8.onnx")
    assert int8.tokens.parent == tmp_path


def test_model_files_reject_missing_assets(tmp_path: Path) -> None:
    """Missing model files fail without attempting a download."""
    with pytest.raises(benchmark.ModelUnavailableError, match="model directory"):
        benchmark.ModelFiles.from_directory(tmp_path / "missing")

    _write_model_directory(tmp_path)
    (tmp_path / "tokens.txt").unlink()
    with pytest.raises(benchmark.ModelUnavailableError, match="tokenizer"):
        benchmark.ModelFiles.from_directory(tmp_path)


def test_model_fingerprint_is_path_free_and_changes_when_asset_bytes_change(tmp_path: Path) -> None:
    """The reproducibility fingerprint hashes stable roles and exact asset bytes."""
    _write_model_directory(tmp_path)
    model = benchmark.ModelFiles.from_directory(tmp_path)

    first = benchmark.fingerprint_model(model)
    model.tokens.write_bytes(model.tokens.read_bytes() + b"changed")
    second = benchmark.fingerprint_model(model)

    assert len(first) == 64
    assert first != second
    assert str(tmp_path) not in first
    assert str(tmp_path) not in second


@pytest.mark.parametrize(
    "changes",
    [
        {"fixtures": ()},
        {"keyword": "@reachy"},
        {"threshold": -0.1},
        {"threshold": 1.1},
        {"boost": 0},
        {"chunk_ms": 19},
        {"chunk_ms": 2_001},
        {"num_threads": 0},
        {"num_trailing_blanks": -1},
        {"num_trailing_blanks": benchmark.MAX_NUM_TRAILING_BLANKS + 1},
        {"tail_padding_seconds": 0.09},
        {"max_input_seconds": 0},
        {"max_input_seconds": benchmark.MAX_INPUT_SECONDS + 1},
        {"max_detections_per_input": 0},
        {"max_detections_per_input": benchmark.DEFAULT_MAX_DETECTIONS + 1},
        {"max_decode_steps_per_input": 0},
        {"max_decode_steps_per_input": benchmark.MAX_DECODE_STEPS_PER_INPUT + 1},
    ],
)
def test_config_rejects_invalid_bounds(
    tmp_path: Path, changes: dict[str, str | float | int | bool | tuple[benchmark.FixtureSpec, ...]]
) -> None:
    """Configuration bounds prevent unbounded fixture or decoder work."""
    if "fixtures" in changes:
        config = _config(tmp_path, (benchmark.FixtureSpec(tmp_path / "fixture.raw", "positive"),), **changes)
        with pytest.raises(ValueError):
            config.validate_inputs()
    else:
        with pytest.raises(ValueError):
            _config(tmp_path, (benchmark.FixtureSpec(tmp_path / "fixture.raw", "positive"),), **changes)


def test_keyword_file_uses_canonical_at_tag(tmp_path: Path) -> None:
    """The generated file contains one normalized keyword and canonical tag."""
    _write_model_directory(tmp_path)
    model = benchmark.ModelFiles.from_directory(tmp_path)
    output = tmp_path / "keywords.txt"
    calls: list[tuple[object, ...]] = []

    def text2token(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return [["▁rea", "chy"]]

    benchmark.generate_keyword_file(output, model, "  ReAcHy  ", text2token=text2token)

    assert output.read_text(encoding="utf-8") == "▁rea chy @reachy\n"
    assert calls == [
        ((["reachy"],), {"tokens": str(model.tokens), "tokens_type": "bpe", "bpe_model": str(model.bpe_model)})
    ]


def test_keyword_candidates_normalize_deduplicate_and_keep_bounded_order() -> None:
    """Configured spellings normalize case/space while preserving first-seen order."""
    assert benchmark.normalize_keyword_candidates((" reachy ", "RICHIE", "Reachy", "Ritchie")) == (
        "Reachy",
        "Richie",
        "Ritchie",
    )


@pytest.mark.parametrize("candidate", ["Tito", "Reechy", "Reachy now", "@reachy", ""])
def test_keyword_candidates_reject_unapproved_spellings(candidate: str) -> None:
    """The benchmark accepts only the explicit pronunciation candidate set."""
    with pytest.raises(ValueError, match="Keyword"):
        benchmark.normalize_keyword_candidates(candidate)


def test_keyword_candidates_reject_more_than_bounded_set() -> None:
    """Candidate configuration cannot expand decoder setup without a bound."""
    too_many = tuple("Reachy" for _ in range(benchmark.MAX_KEYWORD_CANDIDATES * 2 + 1))

    with pytest.raises(ValueError, match="At most"):
        benchmark.normalize_keyword_candidates(too_many)


def test_keyword_file_maps_all_candidate_spellings_to_one_canonical_tag(tmp_path: Path) -> None:
    """Every configured spelling gets a deduplicated entry labelled @reachy."""
    _write_model_directory(tmp_path)
    model = benchmark.ModelFiles.from_directory(tmp_path)
    output = tmp_path / "keywords.txt"
    calls: list[str] = []
    token_by_keyword = {
        "reachy": "reachy",
        "richie": "richie",
        "ritchie": "ritchie",
        "richi": "richy",
        "ricci": "riccy",
    }

    def text2token(keywords: Sequence[str], **_kwargs: object) -> object:
        calls.extend(keywords)
        token = token_by_keyword[keywords[0]]
        return [[f"▁{token}"]]

    benchmark.generate_keyword_file(
        output,
        model,
        ("Reachy", " Richie ", "Ritchie", "Richi", "Ricci", "reachy"),
        text2token=text2token,
    )

    assert output.read_text(encoding="utf-8") == (
        "▁reachy @reachy\n▁richie @reachy\n▁ritchie @reachy\n▁richy @reachy\n▁riccy @reachy\n"
    )
    assert calls == ["reachy", "richie", "ritchie", "richi", "ricci"]
    assert benchmark.CANONICAL_KEYWORD_TAG == "@reachy"


def test_keyword_file_deduplicates_distinct_spellings_with_same_token_sequence(tmp_path: Path) -> None:
    """Distinct allowed spellings that tokenize identically produce one entry."""
    _write_model_directory(tmp_path)
    model = benchmark.ModelFiles.from_directory(tmp_path)
    output = tmp_path / "keywords.txt"
    calls: list[str] = []

    def text2token(keywords: Sequence[str], **_kwargs: object) -> object:
        calls.extend(keywords)
        return [["▁same", "token"]]

    benchmark.generate_keyword_file(output, model, ("Reachy", "Richie"), text2token=text2token)

    assert output.read_text(encoding="utf-8") == "▁same token @reachy\n"
    assert calls == ["reachy", "richie"]


@pytest.mark.parametrize("result", ["reachy", " @REACHY "])
def test_canonical_tag_filter_accepts_only_reachy_tag(result: str) -> None:
    """Decoder output is counted only for the canonical bare or tagged result."""
    assert benchmark._matches_keyword_result(result, benchmark.CANONICAL_KEYWORD_TAG.removeprefix("@"))
    assert not benchmark._matches_keyword_result("@richie", benchmark.CANONICAL_KEYWORD_TAG)
    assert not benchmark._matches_keyword_result("reachyo", benchmark.CANONICAL_KEYWORD_TAG)


@pytest.mark.parametrize("encoded", [[], [[]], [["▁rea", 2]], "reachy"])
def test_keyword_file_rejects_invalid_tokenizer_output(tmp_path: Path, encoded: object) -> None:
    """Unexpected tokenizer output cannot produce an unsafe keyword file."""
    _write_model_directory(tmp_path)
    model = benchmark.ModelFiles.from_directory(tmp_path)

    with pytest.raises(benchmark.ModelUnavailableError):
        benchmark.generate_keyword_file(
            tmp_path / "keywords.txt", model, "Reachy", text2token=lambda *_args, **_kwargs: encoded
        )


@pytest.mark.parametrize("unsafe_token", ["", "▁rea chy", "▁rea\u2003chy", "▁rea\nchy", "\x1b", "\u200b", "\ud800"])
def test_keyword_file_rejects_unsafe_tokenizer_tokens(tmp_path: Path, unsafe_token: str) -> None:
    """Whitespace, control, and format characters cannot enter keyword grammar."""
    _write_model_directory(tmp_path)
    model = benchmark.ModelFiles.from_directory(tmp_path)
    output = tmp_path / "keywords.txt"

    with pytest.raises(benchmark.ModelUnavailableError, match="unsafe keyword tokens") as error:
        benchmark.generate_keyword_file(
            output,
            model,
            "Reachy",
            text2token=lambda *_args, **_kwargs: [[unsafe_token]],
        )

    assert str(error.value) == "The model tokenizer returned unsafe keyword tokens"
    assert not output.exists()


def test_keyword_file_rejects_canonical_tag_vocabulary_collision(tmp_path: Path) -> None:
    """A normal vocabulary token cannot collide with sherpa's result marker."""
    _write_model_directory(tmp_path)
    model = benchmark.ModelFiles.from_directory(tmp_path)
    model.tokens.write_text("@reachy 99\n", encoding="utf-8")
    output = tmp_path / "keywords.txt"

    with pytest.raises(benchmark.ModelUnavailableError, match="vocabulary") as error:
        benchmark.generate_keyword_file(output, model, "Reachy", text2token=lambda *_args, **_kwargs: [["token"]])

    assert str(error.value) == "The model token vocabulary conflicts with the canonical keyword tag"
    assert not output.exists()


def test_keyword_file_rejects_canonical_tag_tokenizer_collision(tmp_path: Path) -> None:
    """Tokenizer output cannot use the canonical result marker as a normal token."""
    _write_model_directory(tmp_path)
    model = benchmark.ModelFiles.from_directory(tmp_path)
    output = tmp_path / "keywords.txt"

    with pytest.raises(benchmark.ModelUnavailableError, match="conflicting") as error:
        benchmark.generate_keyword_file(
            output,
            model,
            "Reachy",
            text2token=lambda *_args, **_kwargs: [["@reachy"]],
        )

    assert str(error.value) == "The model tokenizer returned a token conflicting with the canonical keyword tag"
    assert not output.exists()


@pytest.mark.parametrize("oov_token", ["not-in-vocabulary", ":missing", "#missing", "@missing"])
def test_keyword_file_rejects_tokens_absent_from_vocabulary(tmp_path: Path, oov_token: str) -> None:
    """Unknown and marker-like tokenizer symbols cannot enter keyword grammar."""
    _write_model_directory(tmp_path)
    model = benchmark.ModelFiles.from_directory(tmp_path)
    output = tmp_path / "keywords.txt"

    with pytest.raises(benchmark.ModelUnavailableError, match="absent") as error:
        benchmark.generate_keyword_file(
            output,
            model,
            "Reachy",
            text2token=lambda *_args, **_kwargs: [[oov_token]],
        )

    assert str(error.value) == "The model tokenizer returned a token absent from the model vocabulary"
    assert not output.exists()


def test_keyword_file_wraps_unicode_tokenizer_error_without_partial_output(tmp_path: Path) -> None:
    """Unicode tokenizer failures do not expose payloads or leave a report behind."""
    _write_model_directory(tmp_path)
    model = benchmark.ModelFiles.from_directory(tmp_path)
    output = tmp_path / "keywords.txt"

    def failing_tokenizer(*_args: object, **_kwargs: object) -> object:
        raise UnicodeError("private token payload")

    with pytest.raises(benchmark.ModelUnavailableError, match="could not generate") as error:
        benchmark.generate_keyword_file(output, model, "Reachy", text2token=failing_tokenizer)

    assert str(error.value) == "The model tokenizer could not generate the keyword file"
    assert not output.exists()


def test_keyword_file_reports_missing_tokenizer_dependency(tmp_path: Path) -> None:
    """A tokenizer import failure is reported as an optional dependency error."""
    _write_model_directory(tmp_path)
    model = benchmark.ModelFiles.from_directory(tmp_path)

    def missing_dependency(*_args: object, **_kwargs: object) -> object:
        raise ModuleNotFoundError("sentencepiece")

    with pytest.raises(benchmark.DependencyUnavailableError, match="tokenizer dependency"):
        benchmark.generate_keyword_file(tmp_path / "keywords.txt", model, "Reachy", text2token=missing_dependency)


def test_iter_pcm16_chunks_reads_raw_and_wav_with_normalized_samples(tmp_path: Path) -> None:
    """Both supported fixture formats become bounded normalized float chunks."""
    raw_path = tmp_path / "fixture.raw"
    wav_path = tmp_path / "fixture.wav"
    _write_pcm16(raw_path, sample_count=5)
    _write_wav(wav_path, sample_count=5)

    raw_chunks = list(benchmark.iter_pcm16_chunks(benchmark.FixtureSpec(raw_path, "positive"), 3, 1.0))
    wav_chunks = list(benchmark.iter_pcm16_chunks(benchmark.FixtureSpec(wav_path, "positive"), 3, 1.0))

    assert [count for _, count in raw_chunks] == [3, 2]
    assert [count for _, count in wav_chunks] == [3, 2]
    assert raw_chunks[0][0] == pytest.approx([1_024 / 32_768] * 3)
    assert wav_chunks[1][0] == pytest.approx([1_024 / 32_768] * 2)


@pytest.mark.parametrize(
    "writer, input_format",
    [
        (lambda path: path.write_bytes(b""), "pcm16"),
        (lambda path: path.write_bytes(b"\x00"), "pcm16"),
        (lambda path: _write_wav(path, channels=2), "wav"),
        (lambda path: _write_wav(path, sample_rate=8_000), "wav"),
        (_write_empty_wav_payload, "wav"),
        (_write_truncated_wav_payload, "wav"),
    ],
)
def test_iter_pcm16_chunks_rejects_empty_malformed_and_unsupported_audio(
    tmp_path: Path, writer: Callable[[Path], None], input_format: benchmark.InputFormat
) -> None:
    """Input validation rejects malformed or unsafe fixture shapes."""
    path = tmp_path / ("fixture.wav" if input_format == "wav" else "fixture.raw")
    writer(path)

    with pytest.raises(benchmark.AudioInputError):
        list(benchmark.iter_pcm16_chunks(benchmark.FixtureSpec(path, "negative", input_format), 320, 1.0))


def test_run_benchmark_classifies_exact_tags_and_reports_metadata_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only exact canonical tags count toward TP/FN/TN/FP and metadata has no paths."""
    _write_model_directory(tmp_path / "model")
    fixtures: list[benchmark.FixtureSpec] = []
    expected_labels: tuple[Literal["positive", "negative"], ...] = ("positive", "positive", "negative", "negative")
    for index, expected in enumerate(expected_labels):
        path = tmp_path / f"fixture-{index}.raw"
        _write_pcm16(path)
        fixtures.append(benchmark.FixtureSpec(path, expected))
    spotter = FakeSpotter((("@REACHY",), ("reachyo",), ("",), ("other",)))
    monkeypatch.setattr(benchmark, "generate_keyword_file", lambda *_args, **_kwargs: None)

    monkeypatch.setattr(benchmark, "_installed_sherpa_onnx_version", lambda: None)
    monkeypatch.setattr(benchmark, "_installed_package_version", lambda _name: None)
    monkeypatch.setattr(benchmark, "_utc_timestamp", lambda: "2026-09-01T12:34:56+00:00")
    result = benchmark.run_benchmark(
        _config(tmp_path / "model", tuple(fixtures)),
        spotter_factory=_factory(spotter),
    )

    assert result.true_positive_count == 1
    assert result.false_negative_count == 1
    assert result.true_negative_count == 2
    assert result.false_positive_count == 0
    assert result.detection_count == 1
    assert spotter.reset_count == 3
    metadata = result.to_metadata()
    encoded = json.dumps(metadata)
    assert str(tmp_path) not in encoded
    assert "fixture-0.raw" not in encoded
    assert "audio" not in metadata
    assert "transcript" not in encoded
    assert metadata["num_trailing_blanks"] == benchmark.DEFAULT_NUM_TRAILING_BLANKS
    assert metadata["keyword"] == "Reachy"
    assert metadata["keyword_candidates"] == ["Reachy"]
    assert metadata["keyword_tag"] == "@reachy"
    assert metadata["real_time_factor"] is not None
    fingerprint = metadata["model_fingerprint"]
    assert isinstance(fingerprint, str)
    assert len(fingerprint) == 64
    assert metadata["sherpa_onnx_version"] is None
    assert metadata["sentencepiece_version"] is None
    assert metadata["pypinyin_version"] is None
    assert metadata["benchmark_started_at_utc"] == "2026-09-01T12:34:56+00:00"
    assert metadata["sample_rate_hz"] == 16_000
    assert metadata["positive_fixture_start_to_result_p50_seconds"] is not None
    assert metadata["max_decode_steps_per_input"] == benchmark.DEFAULT_MAX_DECODE_STEPS_PER_INPUT
    assert metadata["negative_detection_count"] == 0
    assert metadata["negative_audio_duration_seconds"] == pytest.approx(0.04)
    assert metadata["recall"] == pytest.approx(0.5)
    assert metadata["false_positives_per_hour"] == pytest.approx(0.0)
    assert all(
        isinstance(metadata[key], str) and metadata[key]
        for key in ("platform_system", "machine_architecture", "python_version")
    )
    assert isinstance(metadata["platform_release"], str)
    assert metadata["platform_release"]
    assert "hostname" not in metadata
    assert "username" not in metadata
    assert "node" not in metadata
    assert "processor" not in metadata
    assert "cpu_model" not in metadata
    assert "queue" not in encoded
    assert "drop" not in encoded


def test_run_benchmark_accepts_explicit_alias_candidates_with_one_metadata_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opt-in spellings share one canonical detection tag and remain metadata-only."""
    _write_model_directory(tmp_path / "model")
    path = tmp_path / "positive.raw"
    _write_pcm16(path)
    generated: list[tuple[str, ...]] = []

    def generate(_output: Path, _model: benchmark.ModelFiles, candidates: object) -> None:
        assert isinstance(candidates, tuple)
        generated.append(candidates)

    monkeypatch.setattr(benchmark, "generate_keyword_file", generate)
    config = _config(
        tmp_path / "model",
        (benchmark.FixtureSpec(path, "positive"),),
        keyword_candidates=("Reachy", "Richie", "Ritchie", "Richi", "Ricci"),
    )

    result = benchmark.run_benchmark(config, spotter_factory=_factory(FakeSpotter((("@reachy",),))))

    assert generated == [("Reachy", "Richie", "Ritchie", "Richi", "Ricci")]
    assert result.keyword_candidates == config.keyword_candidates
    assert result.keyword_tag == "@reachy"
    assert result.to_metadata()["keyword_tag"] == "@reachy"


def test_run_benchmark_reports_negative_event_rate_and_empty_positive_recall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """False-positive rate counts detection events, while empty recall is explicit."""
    _write_model_directory(tmp_path / "model")
    path = tmp_path / "negative.raw"
    _write_pcm16(path, sample_count=16_000)
    monkeypatch.setattr(benchmark, "generate_keyword_file", lambda *_args, **_kwargs: None)

    result = benchmark.run_benchmark(
        _config(tmp_path / "model", (benchmark.FixtureSpec(path, "negative"),)),
        spotter_factory=_factory(FakeSpotter((("reachy", "@reachy"),))),
    )

    assert result.false_positive_count == 1
    assert result.negative_detection_count == 2
    assert result.negative_audio_duration_seconds == pytest.approx(1.0)
    assert result.false_positives_per_hour == pytest.approx(7_200.0)
    assert result.recall is None
    assert result.positive_fixture_start_to_result_p50_seconds is None
    assert result.positive_fixture_start_to_result_p95_seconds is None


def test_run_benchmark_uses_first_exact_detection_for_fixture_latency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Duplicate exact events remain counted but do not weight first-detection timing."""
    _write_model_directory(tmp_path / "model")
    path = tmp_path / "positive.raw"
    _write_pcm16(path)
    monkeypatch.setattr(benchmark, "generate_keyword_file", lambda *_args, **_kwargs: None)
    perf_counter_values = iter((0.0, 10.0, 11.0, 12.0, 13.0))
    monkeypatch.setattr(time, "perf_counter", lambda: next(perf_counter_values))
    monkeypatch.setattr(benchmark, "_resource_snapshot", lambda: (0.0, None))

    result = benchmark.run_benchmark(
        _config(tmp_path / "model", (benchmark.FixtureSpec(path, "positive"),)),
        spotter_factory=_factory(FakeSpotter((("reachy", "@reachy"),))),
    )

    fixture = result.fixture_results[0]
    assert fixture.detection_count == 2
    assert fixture.fixture_start_to_result_p50_seconds == pytest.approx(1.0)
    assert fixture.fixture_start_to_result_p95_seconds == pytest.approx(1.0)
    assert result.positive_fixture_start_to_result_p50_seconds == pytest.approx(1.0)
    assert result.positive_fixture_start_to_result_p95_seconds == pytest.approx(1.0)


def test_run_benchmark_uses_each_fixture_start_for_offline_result_latency_and_reports_rtf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Offline fixture-start-to-result timing is measured per fixture, not from batch start."""
    _write_model_directory(tmp_path / "model")
    paths = [tmp_path / "first.raw", tmp_path / "second.raw"]
    for path in paths:
        _write_pcm16(path, sample_count=16_000)
    fixtures = tuple(benchmark.FixtureSpec(path, "positive") for path in paths)
    spotter = FakeSpotter((("reachy",), ("@reachy",)))
    perf_counter_values = iter((0.0, 100.0, 101.0, 102.0, 200.0, 201.0, 202.0, 300.0))
    monkeypatch.setattr(time, "perf_counter", lambda: next(perf_counter_values))
    monkeypatch.setattr(benchmark, "_resource_snapshot", lambda: (0.0, None))
    monkeypatch.setattr(benchmark, "generate_keyword_file", lambda *_args, **_kwargs: None)

    result = benchmark.run_benchmark(
        _config(tmp_path / "model", fixtures),
        spotter_factory=_factory(spotter),
    )

    assert [fixture.fixture_start_to_result_p50_seconds for fixture in result.fixture_results] == [1.0, 1.0]
    assert [fixture.processing_duration_seconds for fixture in result.fixture_results] == [2.0, 2.0]
    assert [fixture.real_time_factor for fixture in result.fixture_results] == [pytest.approx(2.0), pytest.approx(2.0)]
    assert result.positive_fixture_start_to_result_p50_seconds == pytest.approx(1.0)
    assert result.positive_fixture_start_to_result_p95_seconds == pytest.approx(1.0)
    assert result.real_time_factor == pytest.approx(2.0)


def test_run_benchmark_cleans_temporary_keyword_file_and_forwards_trailing_blanks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The generated keyword file is temporary and trailing blanks reach the factory."""
    _write_model_directory(tmp_path / "model")
    fixture_path = tmp_path / "fixture.raw"
    _write_pcm16(fixture_path)
    captured: dict[str, object] = {}

    def factory(
        model: benchmark.ModelFiles, keyword_file: Path, config: benchmark.BenchmarkConfig
    ) -> benchmark.KeywordSpotter:
        captured.update({"model": model, "keyword_file": keyword_file, "config": config})
        assert keyword_file.read_text(encoding="utf-8") == "token @reachy\n"
        return FakeSpotter((("",),))

    monkeypatch.setattr(
        benchmark, "generate_keyword_file", lambda output, *_args, **_kwargs: output.write_text("token @reachy\n")
    )
    config = _config(
        tmp_path / "model",
        (benchmark.FixtureSpec(fixture_path, "negative"),),
        num_trailing_blanks=7,
    )
    benchmark.run_benchmark(config, spotter_factory=factory)

    assert captured["config"] == config
    keyword_file = captured["keyword_file"]
    assert isinstance(keyword_file, Path)
    assert not keyword_file.exists()


def test_run_benchmark_rejects_input_and_detection_bounds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixture duration and exact-detection limits fail before unbounded work."""
    _write_model_directory(tmp_path / "model")
    path = tmp_path / "fixture.raw"
    _write_pcm16(path, sample_count=1_000)
    monkeypatch.setattr(benchmark, "generate_keyword_file", lambda *_args, **_kwargs: None)
    config = _config(tmp_path / "model", (benchmark.FixtureSpec(path, "positive"),), max_input_seconds=0.01)
    with pytest.raises(benchmark.AudioInputError, match="duration"):
        benchmark.run_benchmark(config, spotter_factory=_factory(FakeSpotter((("",),))))

    bounded_config = _config(
        tmp_path / "model", (benchmark.FixtureSpec(path, "positive"),), max_detections_per_input=1
    )
    with pytest.raises(benchmark.AudioInputError, match="detection"):
        benchmark.run_benchmark(bounded_config, spotter_factory=_factory(FakeSpotter((("reachy", "@reachy"),))))


def test_run_benchmark_enforces_detection_bound_inside_input_decode_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A permanently ready matching fake cannot bypass the input detection bound."""
    _write_model_directory(tmp_path / "model")
    path = tmp_path / "fixture.raw"
    _write_pcm16(path)
    monkeypatch.setattr(benchmark, "generate_keyword_file", lambda *_args, **_kwargs: None)

    class MatchingLoopSpotter:
        def create_stream(self) -> FakeStream:
            return FakeStream(())

        def is_ready(self, _stream: benchmark.SpotterStream) -> bool:
            return True

        def decode_stream(self, _stream: benchmark.SpotterStream) -> None:
            return None

        def get_result(self, _stream: benchmark.SpotterStream) -> str:
            return "reachy"

        def reset_stream(self, _stream: benchmark.SpotterStream) -> None:
            return None

    config = _config(
        tmp_path / "model",
        (benchmark.FixtureSpec(path, "positive"),),
        max_detections_per_input=1,
    )
    with pytest.raises(benchmark.AudioInputError, match="detection"):
        benchmark.run_benchmark(config, spotter_factory=_factory(MatchingLoopSpotter()))


def test_run_benchmark_bounds_total_decode_steps_across_input_and_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A permanently ready fake cannot hang input or tail draining."""
    _write_model_directory(tmp_path / "model")
    path = tmp_path / "fixture.raw"
    _write_pcm16(path)
    monkeypatch.setattr(benchmark, "generate_keyword_file", lambda *_args, **_kwargs: None)

    class TailLoopSpotter:
        def __init__(self) -> None:
            self.stream = FakeStream(("",))

        def create_stream(self) -> FakeStream:
            return self.stream

        def is_ready(self, stream: benchmark.SpotterStream) -> bool:
            if not isinstance(stream, FakeStream):
                raise TypeError("unexpected stream")
            return stream.finished or bool(stream.results)

        def decode_stream(self, _stream: benchmark.SpotterStream) -> None:
            return None

        def get_result(self, stream: benchmark.SpotterStream) -> str:
            if not isinstance(stream, FakeStream):
                raise TypeError("unexpected stream")
            return stream.results.pop(0) if stream.results else ""

        def reset_stream(self, _stream: benchmark.SpotterStream) -> None:
            return None

    config = _config(
        tmp_path / "model",
        (benchmark.FixtureSpec(path, "negative"),),
        max_decode_steps_per_input=2,
    )
    with pytest.raises(benchmark.AudioInputError, match="decode-step"):
        benchmark.run_benchmark(config, spotter_factory=_factory(TailLoopSpotter()))


def test_run_benchmark_wraps_spotter_errors_without_payloads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Model and decoder failures become bounded benchmark errors."""
    _write_model_directory(tmp_path / "model")
    path = tmp_path / "private-household.raw"
    _write_pcm16(path)
    monkeypatch.setattr(benchmark, "generate_keyword_file", lambda *_args, **_kwargs: None)

    def failing_factory(*_args: object) -> benchmark.KeywordSpotter:
        raise AssertionError("private model path and transcript")

    with pytest.raises(benchmark.ModelUnavailableError, match="initialize"):
        benchmark.run_benchmark(
            _config(tmp_path / "model", (benchmark.FixtureSpec(path, "negative"),)),
            spotter_factory=failing_factory,
        )


def test_model_setup_assertions_are_wrapped_without_asset_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tokenizer and sherpa setup assertions become bounded public errors."""
    _write_model_directory(tmp_path)
    model = benchmark.ModelFiles.from_directory(tmp_path)
    output = tmp_path / "keywords.txt"

    def failing_tokenizer(*_args: object, **_kwargs: object) -> object:
        raise AssertionError(f"private tokenizer asset: {model.bpe_model}")

    with pytest.raises(benchmark.ModelUnavailableError, match="tokenizer") as tokenizer_error:
        benchmark.generate_keyword_file(output, model, "Reachy", text2token=failing_tokenizer)
    assert str(model.bpe_model) not in str(tokenizer_error.value)

    class FailingKeywordSpotter:
        def __init__(self, **_kwargs: object) -> None:
            raise AssertionError(f"private model asset: {model.encoder}")

    module = types.ModuleType("sherpa_onnx")
    setattr(module, "KeywordSpotter", FailingKeywordSpotter)
    monkeypatch.setitem(sys.modules, "sherpa_onnx", module)
    config = _config(tmp_path, (benchmark.FixtureSpec(tmp_path / "tokens.txt", "negative"),))
    with pytest.raises(benchmark.ModelUnavailableError, match="initialize") as spotter_error:
        benchmark.create_sherpa_spotter(model, output, config)
    assert str(model.encoder) not in str(spotter_error.value)


def test_public_integer_knobs_reject_booleans(tmp_path: Path) -> None:
    """Boolean values do not silently act as integer benchmark settings."""
    with pytest.raises(ValueError, match="booleans"):
        benchmark.BenchmarkConfig(model_directory=tmp_path, chunk_ms=True)
    with pytest.raises(ValueError, match="booleans"):
        benchmark.BenchmarkConfig(model_directory=tmp_path, num_threads=True)
    with pytest.raises(ValueError, match="booleans"):
        benchmark.BenchmarkConfig(model_directory=tmp_path, num_trailing_blanks=True)
    with pytest.raises(ValueError, match="booleans"):
        benchmark.BenchmarkConfig(model_directory=tmp_path, max_detections_per_input=True)
    with pytest.raises(ValueError, match="booleans"):
        benchmark.BenchmarkConfig(model_directory=tmp_path, max_decode_steps_per_input=True)
    for invalid_integer_setting in (
        {"chunk_ms": 320.0},
        {"num_threads": 2.0},
        {"num_trailing_blanks": 1.0},
        {"max_detections_per_input": 10_000.0},
        {"max_decode_steps_per_input": 1_000_000.0},
    ):
        with pytest.raises(ValueError, match="must be integers"):
            benchmark.BenchmarkConfig(model_directory=tmp_path, **invalid_integer_setting)

    path = tmp_path / "fixture.raw"
    _write_pcm16(path)
    with pytest.raises(ValueError, match="positive"):
        list(benchmark.iter_pcm16_chunks(benchmark.FixtureSpec(path, "negative"), True, 1.0))
    with pytest.raises(ValueError, match="positive integer"):
        list(benchmark.iter_pcm16_chunks(benchmark.FixtureSpec(path, "negative"), 1.0, 1.0))


def test_create_spotter_passes_cpu_and_trailing_blank_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The optional sherpa adapter receives only local model/config paths and knobs."""
    _write_model_directory(tmp_path)
    model = benchmark.ModelFiles.from_directory(tmp_path)
    captured: dict[str, object] = {}

    class FakeKeywordSpotter:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def create_stream(self) -> object:
            return object()

    module = types.ModuleType("sherpa_onnx")
    setattr(module, "KeywordSpotter", FakeKeywordSpotter)
    monkeypatch.setitem(sys.modules, "sherpa_onnx", module)
    config = _config(tmp_path, (benchmark.FixtureSpec(tmp_path / "tokens.txt", "negative"),), num_trailing_blanks=9)

    benchmark.create_sherpa_spotter(model, tmp_path / "keywords.txt", config)

    assert captured["provider"] == "cpu"
    assert captured["num_threads"] == config.num_threads
    assert captured["num_trailing_blanks"] == 9
    assert captured["keywords_file"] == str(tmp_path / "keywords.txt")


def test_load_sherpa_reports_optional_dependency_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """The optional engine is loaded only on benchmark execution."""
    monkeypatch.delitem(sys.modules, "sherpa_onnx", raising=False)
    real_import = importlib.import_module

    def fail_import(name: str) -> types.ModuleType:
        if name == "sherpa_onnx":
            raise ImportError("not installed")
        return real_import(name)

    monkeypatch.setattr(importlib, "import_module", fail_import)
    with pytest.raises(benchmark.DependencyUnavailableError, match="optional"):
        benchmark.load_sherpa_onnx()


def test_installed_sherpa_version_is_reported_when_distribution_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The metadata seam preserves the installed optional engine version."""
    monkeypatch.setattr(benchmark, "_installed_package_version", lambda _name: "1.13.6")

    assert benchmark._installed_sherpa_onnx_version() == "1.13.6"


def test_cli_is_fixture_only_and_emits_metadata_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI accepts local fixtures, writes metadata, and exposes no capture mode."""
    parser = benchmark._parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--model-dir", str(tmp_path), "--live-mic"])

    _write_model_directory(tmp_path / "model")
    path = tmp_path / "fixture.raw"
    _write_pcm16(path)
    output = tmp_path / "result.json"
    monkeypatch.setattr(benchmark, "generate_keyword_file", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        benchmark,
        "run_benchmark",
        lambda config: benchmark.BenchmarkResult(
            mode="fixtures",
            keyword=config.keyword,
            threshold=config.threshold,
            boost=config.boost,
            chunk_ms=config.chunk_ms,
            num_threads=config.num_threads,
            num_trailing_blanks=config.num_trailing_blanks,
            input_count=1,
            positive_count=1,
            negative_count=0,
            true_positive_count=1,
            false_negative_count=0,
            true_negative_count=0,
            false_positive_count=0,
            detection_count=1,
            negative_detection_count=0,
            model_fingerprint="0" * 64,
            sherpa_onnx_version=None,
            sentencepiece_version=None,
            pypinyin_version=None,
            benchmark_started_at_utc="2026-09-01T12:34:56+00:00",
            sample_rate_hz=16_000,
            platform_system="test",
            platform_release="test",
            machine_architecture="test",
            python_version="3.12.0",
            positive_fixture_start_to_result_p50_seconds=0.1,
            positive_fixture_start_to_result_p95_seconds=0.1,
            audio_duration_seconds=1.0,
            negative_audio_duration_seconds=0.0,
            processing_duration_seconds=0.2,
            real_time_factor=0.2,
            recall=1.0,
            false_positives_per_hour=None,
            max_decode_steps_per_input=benchmark.DEFAULT_MAX_DECODE_STEPS_PER_INPUT,
            resources=benchmark.ResourceMetrics(None, None, None),
            fixture_results=(benchmark.FixtureResult(0, "positive", "true_positive", 1, 1.0, 0.2, 0.2, 0.1, 0.1),),
        ),
    )

    assert (
        benchmark.main(
            ["--model-dir", str(tmp_path / "model"), "--positive", str(path), "--json", "--output", str(output)]
        )
        == 0
    )
    printed = capsys.readouterr().out
    assert json.loads(printed)["mode"] == "fixtures"
    assert json.loads(output.read_text(encoding="utf-8"))["real_time_factor"] == 0.2
