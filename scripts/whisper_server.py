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

Run it with the Python of the ``transformers`` uv tool (it has torch, librosa and FastAPI):

    ~/.local/share/uv/tools/transformers/bin/python scripts/whisper_server.py --port 8766 openai/whisper-small
"""

import io
import os
import math
import time
import asyncio
import logging
import argparse
import contextlib
from typing import Any
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


class Transcriber:
    """One Whisper model; one clip at a time."""

    def __init__(self, model_id: str, languages: tuple[str, ...]) -> None:
        """Load ``model_id`` on the best available device."""
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
        import librosa

        audio, _ = librosa.load(io.BytesIO(audio_bytes), sr=SAMPLE_RATE, mono=True)
        duration = audio.size / SAMPLE_RATE
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


def build_app(transcriber: Transcriber, request_transcriber: Transcriber | None = None) -> Any:
    """Return the FastAPI app; a request naming ``request_transcriber``'s model is transcribed with it."""
    from fastapi import FastAPI, Request, HTTPException
    from fastapi.responses import JSONResponse

    app = FastAPI()
    lock = TurnLock()

    @app.get("/v1/models")
    async def models() -> JSONResponse:
        loaded = [transcriber, *([request_transcriber] if request_transcriber else [])]
        return JSONResponse({"object": "list", "data": [{"id": t.model_id, "object": "model"} for t in loaded]})

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
            if request_transcriber is not None and requested_model == request_transcriber.model_id
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
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    languages = parse_languages(os.getenv("WHISPER_LANGUAGES"))
    transcriber = Transcriber(args.model, languages)
    logger.info("Whisper %s on %s; languages: %s", args.model, transcriber.device, ", ".join(languages) or "any")
    request_transcriber = None
    if args.request_model and args.request_model != args.model:
        request_transcriber = Transcriber(args.request_model, languages)
        logger.info("Requests to Reachy use %s", args.request_model)
    import uvicorn

    uvicorn.run(build_app(transcriber, request_transcriber), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
