"""Local Whisper speech-to-text server for Reachy's ambient listening.

A small OpenAI-compatible ``/v1/audio/transcriptions`` endpoint that replaces ``transformers serve``
for continuous listening. On noise or several people talking at once Whisper tends to invent long
text, and ``transformers serve`` lets it generate up to 448 tokens (26 s for 8 s of noise, measured
2026-10-18) while every other clip waits. With kids playing nearby the queue jammed and clips
saying "Reachy" were dropped or timed out. Real speech is still transcribed in full; this server only
bounds the work per clip:

- generation stops at a token budget proportional to the clip length (fast speech stays inside it);
- a clip that hits the budget (Whisper stuck repeating one voice) is decoded once more with a
  repetition penalty, which recovers what another voice said underneath ("I want the red one. I want
  the red one..." became "I want the red one. Can you turn on lights in a dining area, please?");
- the language is picked from the household's languages (``WHISPER_LANGUAGES``, default en,es,it,pt),
  so chatter is not decoded as Chinese or Japanese;
- a clip whose client already gave up is skipped instead of transcribed;
- each clip is encoded once: the language pick and the text share the encoder output (encoding twice
  doubled the time; measured 2026-10-27 on the M1 Mac mini over 61 saved clips, whisper-small 0.74 s ->
  0.46 s and whisper-large-v3-turbo 3.19 s -> 1.64 s, with identical text);
- with ``--request-model``, a second, more accurate model transcribes what is said to Reachy right after
  his name (the app names it per request), while background speech keeps the fast model. Measured
  2026-10-18: whisper-small heard "Reachy, ¿qué hora es?" as "¿Real si que hora es?" in 0.34 s;
  whisper-large-v3-turbo got it right in 1.24 s. Those clips go ahead of waiting background clips, so a
  command never queues behind the room's chatter.

With ``--engine qwen``, Qwen3-ASR on MLX stands in for both Whisper models and answers to their names, so
the app is unchanged. Measured 2026-10-29 on the mini: the 0.6B model took 0.17 s per room clip
(whisper-small 0.44 s) with a third of its errors on noisy requests; the 1.7B model matched turbo's
accuracy in 0.36 s (turbo 1.62 s). Both get Reachy's and Maribel's names as a hint, which lets them hear
"Richie" where Whisper heard "Reggie", but on unclear audio they sometimes return the hint itself; a run
of two or more different hint words is removed. A Qwen model that cannot load (``mlx-qwen3-asr`` not
installed, say) is replaced by its Whisper model.

Run it with the Python of the ``transformers`` uv tool (it has torch, librosa and FastAPI; Qwen also needs
``uv pip install --python <that python> mlx-qwen3-asr==0.4.4``):

    ~/.local/share/uv/tools/transformers/bin/python scripts/whisper_server.py --port 8766 openai/whisper-small
"""

import io
import os
import re
import math
import time
import asyncio
import logging
import argparse
import contextlib
import unicodedata
from typing import Any, Protocol
from collections.abc import AsyncIterator


logger = logging.getLogger("whisper_server")

SAMPLE_RATE = 16000
# Whisper's own decoder limit (448 positions minus the start tokens).
MAX_NEW_TOKENS = 440
# Generous for real speech: a fast talker produces about 4-6 tokens a second, Spanish and Italian a
# little more than English.
TOKENS_PER_SECOND = 10.0
BASE_TOKENS = 16
DEFAULT_LANGUAGES = ("en", "es", "it", "pt")
LOOP_RETRY_REPETITION_PENALTY = 1.3
DEFAULT_QWEN_MODEL = "moona3k/mlx-qwen3-asr-0.6b-8bit"
DEFAULT_QWEN_REQUEST_MODEL = "moona3k/mlx-qwen3-asr-1.7b-8bit"
DEFAULT_NAME_HINT = "Reachy, Richie, Maribel"
# Qwen raises on clips this short (0.01 s background clips happen).
MIN_CLIP_S = 0.1
QWEN_LANGUAGE_NAMES = {"en": "English", "es": "Spanish", "it": "Italian", "pt": "Portuguese", "fr": "French"}


def token_budget(duration_s: float) -> int:
    """Return the most tokens a clip of ``duration_s`` seconds may produce."""
    if not math.isfinite(duration_s) or duration_s <= 0:
        return BASE_TOKENS
    return min(MAX_NEW_TOKENS, BASE_TOKENS + math.ceil(duration_s * TOKENS_PER_SECOND))


def parse_languages(value: str | None) -> tuple[str, ...]:
    """Parse ``en,es`` into language codes; empty means the defaults, ``auto`` means any language."""
    if value is None or not value.strip():
        return DEFAULT_LANGUAGES
    if value.strip().lower() == "auto":
        return ()
    return tuple(dict.fromkeys(code.strip().lower() for code in value.split(",") if code.strip()))


def _fold(word: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", word.lower()) if not unicodedata.combining(ch))


def strip_hint_echo(text: str, hint: str) -> tuple[str, bool]:
    """Remove each run of two or more different hint words; return the text and whether anything was removed."""
    hint_words = {_fold(word) for word in re.findall(r"\w+", hint)}
    words = list(re.finditer(r"\w+", text))
    spans: list[tuple[int, int]] = []
    start = 0
    while start < len(words):
        end = start
        while end < len(words) and _fold(words[end].group()) in hint_words:
            end += 1
        if len({_fold(word.group()) for word in words[start:end]}) >= 2:
            spans.append((words[start].start(), words[end].start() if end < len(words) else len(text)))
        start = max(end, start + 1)
    if not spans:
        return text, False
    for begin, finish in reversed(spans):
        text = text[:begin] + text[finish:]
    return text.strip(" ,;:-"), True


def load_audio(audio_bytes: bytes) -> tuple[Any, float]:
    """Decode a clip to 16 kHz mono float32; return it and its length in seconds."""
    import librosa

    audio, _ = librosa.load(io.BytesIO(audio_bytes), sr=SAMPLE_RATE, mono=True)
    return audio, audio.size / SAMPLE_RATE


class Engine(Protocol):
    """A loaded speech model the server can route clips to."""

    name: str  # the model name the app asks for
    model_id: str  # the model actually loaded
    device: str

    def transcribe(self, audio_bytes: bytes, language: str | None = None) -> tuple[str, dict[str, Any]]:
        """Transcribe one clip; return the text and timing details for the log."""
        ...


class Transcriber:
    """One Whisper model; one clip at a time."""

    def __init__(self, model_id: str, languages: tuple[str, ...]) -> None:
        """Load ``model_id`` on the best available device."""
        self.name = model_id
        import torch
        from transformers import WhisperProcessor, WhisperForConditionalGeneration

        self._torch = torch
        if torch.backends.mps.is_available():
            self.device, dtype = "mps", torch.float16
        elif torch.cuda.is_available():
            self.device, dtype = "cuda", torch.float16
        else:
            self.device, dtype = "cpu", torch.float32
        self.model_id = model_id
        self.processor = WhisperProcessor.from_pretrained(model_id)
        self.model = WhisperForConditionalGeneration.from_pretrained(model_id, torch_dtype=dtype).to(self.device)
        self.model.eval()
        self.dtype = dtype
        tokenizer = self.processor.tokenizer
        multilingual = bool(getattr(self.model.generation_config, "is_multilingual", False))
        self._language_ids: dict[str, int] = {}
        if multilingual:
            for code in languages:
                token_id = tokenizer.convert_tokens_to_ids(f"<|{code}|>")
                if isinstance(token_id, int) and token_id != tokenizer.unk_token_id:
                    self._language_ids[code] = token_id
                else:
                    logger.warning("Unknown Whisper language %r ignored", code)
        self._start_id = self.model.generation_config.decoder_start_token_id

    def _features(self, audio_bytes: bytes) -> tuple[Any, float]:
        audio, duration = load_audio(audio_bytes)
        features = self.processor(audio, sampling_rate=SAMPLE_RATE, return_tensors="pt").input_features
        return features.to(self.device, self.dtype), duration

    def _pick_language(self, encoder_outputs: Any) -> str | None:
        """Return the most likely of the household's languages."""
        if not self._language_ids:
            return None
        torch = self._torch
        decoder_input = torch.tensor([[self._start_id]], device=self.device)
        logits = self.model(encoder_outputs=encoder_outputs, decoder_input_ids=decoder_input).logits[0, -1]
        codes = list(self._language_ids)
        scores = logits[[self._language_ids[code] for code in codes]]
        return codes[int(torch.argmax(scores))]

    def transcribe(self, audio_bytes: bytes, language: str | None = None) -> tuple[str, dict[str, Any]]:
        """Transcribe one clip; returns the text and timing details for the log."""
        torch = self._torch
        started = time.perf_counter()
        features, duration = self._features(audio_bytes)
        budget = token_budget(duration)
        with torch.inference_mode():
            encoder_outputs = self.model.get_encoder()(features)
            if language is None:
                language = self._pick_language(encoder_outputs)
            kwargs: dict[str, Any] = {"max_new_tokens": budget, "task": "transcribe"}
            if language:
                kwargs["language"] = language
            generated = self.model.generate(features, encoder_outputs=encoder_outputs, **kwargs)
            # Whisper's generate returns the text tokens only, without the forced start tokens.
            new_tokens = int(generated.shape[-1])
            capped = new_tokens >= budget
            if capped:
                generated = self.model.generate(
                    features,
                    encoder_outputs=encoder_outputs,
                    repetition_penalty=LOOP_RETRY_REPETITION_PENALTY,
                    **kwargs,
                )
        text = self.processor.batch_decode(generated, skip_special_tokens=True)[0].strip()
        return text, {
            "audio_s": round(duration, 1),
            "language": language or "auto",
            "tokens": new_tokens,
            "capped": capped,
            "took_s": round(time.perf_counter() - started, 2),
        }


class QwenTranscriber:
    """One Qwen3-ASR model on MLX, answering to the name of the Whisper model it replaces; one clip at a time."""

    device = "mlx"

    def __init__(self, model_id: str, name: str, hint: str) -> None:
        """Load ``model_id`` and run it once, so the first real clip does not pay for the warm-up."""
        import numpy as np
        from mlx_qwen3_asr import Session

        self.model_id = model_id
        self.name = name
        self.hint = hint
        self.session = Session(model=model_id)
        self.session.transcribe(np.zeros(SAMPLE_RATE // 2, dtype=np.float32), context=hint)

    def transcribe(self, audio_bytes: bytes, language: str | None = None) -> tuple[str, dict[str, Any]]:
        """Transcribe one clip; returns the text and timing details for the log."""
        started = time.perf_counter()
        audio, duration = load_audio(audio_bytes)
        details: dict[str, Any] = {"audio_s": round(duration, 1)}
        if duration < MIN_CLIP_S:
            return "", {**details, "language": "none", "took_s": round(time.perf_counter() - started, 2)}
        # Qwen takes one forced language or none; it cannot choose among the household's.
        forced = QWEN_LANGUAGE_NAMES.get(language or "")
        result = self.session.transcribe(audio, context=self.hint, language=forced)
        text, echoed = strip_hint_echo(result.text.strip(), self.hint) if self.hint else (result.text.strip(), False)
        return text, {
            **details,
            "language": str(result.language or "auto").lower(),
            "echo": echoed,
            "took_s": round(time.perf_counter() - started, 2),
        }


def load_engine(engine: str, whisper_model: str, qwen_model: str, languages: tuple[str, ...], hint: str) -> Engine:
    """Load Qwen in place of ``whisper_model`` when asked, else (or if Qwen cannot load) the Whisper model."""
    if engine == "qwen":
        try:
            qwen = QwenTranscriber(qwen_model, whisper_model, hint)
        except Exception as error:
            logger.warning("Qwen %s could not load (%s: %s); using Whisper", qwen_model, type(error).__name__, error)
        else:
            logger.info("Qwen %s answers for %s; name hint: %s", qwen_model, whisper_model, hint or "none")
            return qwen
    transcriber = Transcriber(whisper_model, languages)
    logger.info("Whisper %s on %s; languages: %s", whisper_model, transcriber.device, ", ".join(languages) or "any")
    return transcriber


class TurnLock:
    """Lets one clip use the model at a time; urgent clips go before every waiting background clip."""

    def __init__(self) -> None:
        """Start free, with nobody waiting."""
        self._busy = False
        self._urgent_waiting = 0
        self._changed = asyncio.Condition()

    @contextlib.asynccontextmanager
    async def turn(self, *, urgent: bool) -> AsyncIterator[None]:
        """Wait for this clip's turn and hold it until the block ends."""
        async with self._changed:
            if urgent:
                self._urgent_waiting += 1
            try:
                await self._changed.wait_for(lambda: not self._busy and (urgent or not self._urgent_waiting))
            finally:
                if urgent:
                    self._urgent_waiting -= 1
                    self._changed.notify_all()
            self._busy = True
        try:
            yield
        finally:
            async with self._changed:
                self._busy = False
                self._changed.notify_all()


def build_app(transcriber: Engine, request_transcriber: Engine | None = None) -> Any:
    """Return the FastAPI app; a request naming ``request_transcriber``'s model is transcribed with it."""
    from fastapi import FastAPI, Request, HTTPException
    from fastapi.responses import JSONResponse

    app = FastAPI()
    lock = TurnLock()

    @app.get("/v1/models")
    async def models() -> JSONResponse:
        loaded = [transcriber, *([request_transcriber] if request_transcriber else [])]
        return JSONResponse({"object": "list", "data": [{"id": t.name, "object": "model"} for t in loaded]})

    @app.post("/v1/audio/transcriptions")
    async def transcriptions(request: Request) -> JSONResponse:
        async with request.form() as form:
            upload = form.get("file")
            if upload is None or isinstance(upload, str):
                raise HTTPException(status_code=422, detail="Expected an audio file upload")
            audio_bytes = await upload.read()
            language = str(form.get("language") or "").strip().lower() or None
            requested_model = str(form.get("model") or "").strip()
        chosen = (
            request_transcriber
            if request_transcriber is not None and requested_model == request_transcriber.name
            else transcriber
        )
        queued_at = time.perf_counter()
        async with lock.turn(urgent=chosen is request_transcriber):
            if await request.is_disconnected():
                logger.info("Skipped a clip: the client stopped waiting after %.1fs", time.perf_counter() - queued_at)
                return JSONResponse({"text": ""}, status_code=499)
            try:
                text, details = await asyncio.to_thread(chosen.transcribe, audio_bytes, language)
            except Exception as error:
                logger.warning("Transcription failed: %s", type(error).__name__)
                raise HTTPException(status_code=400, detail="Could not transcribe the audio") from error
        details["model"] = chosen.model_id.rsplit("/", 1)[-1]
        details["waited_s"] = round(max(0.0, time.perf_counter() - queued_at - details["took_s"]), 2)
        logger.info("Transcribed %s", " ".join(f"{key}={value}" for key, value in details.items()))
        return JSONResponse({"text": text})

    return app


def main() -> None:
    """Run the server."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("model", nargs="?", default="openai/whisper-small")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument(
        "--request-model",
        default=None,
        help="also load this more accurate model, used when a request names it (Reachy's requests)",
    )
    parser.add_argument("--engine", default="whisper", help="whisper, or qwen (Whisper loads if Qwen cannot)")
    parser.add_argument("--qwen-model", default=DEFAULT_QWEN_MODEL, help="Qwen3-ASR model in place of MODEL")
    parser.add_argument(
        "--qwen-request-model", default=DEFAULT_QWEN_REQUEST_MODEL, help="Qwen3-ASR model in place of --request-model"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    engine = args.engine.strip().lower()
    if engine not in ("whisper", "qwen"):
        logger.warning("Unknown speech engine %r; using Whisper", args.engine)
        engine = "whisper"
    languages = parse_languages(os.getenv("WHISPER_LANGUAGES"))
    hint = os.getenv("STT_NAME_HINT", DEFAULT_NAME_HINT).strip()
    transcriber = load_engine(engine, args.model, args.qwen_model, languages, hint)
    request_transcriber = None
    if args.request_model and args.request_model != args.model:
        request_transcriber = load_engine(engine, args.request_model, args.qwen_request_model, languages, hint)
        logger.info("Requests to Reachy use %s", request_transcriber.model_id)
    import uvicorn

    uvicorn.run(build_app(transcriber, request_transcriber), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
