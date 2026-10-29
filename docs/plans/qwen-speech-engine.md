# Plan: Qwen3-ASR as the local speech engine, Whisper as fallback

- Status: `in-progress` (built and checked on the Mac mini; not yet switched on for Reachy)
- Last updated: 2026-10-29
- Feature doc: [Hybrid runtime](../features/hybrid-runtime.md) (local STT server)

## Goal

Hear the room and requests to Reachy more accurately and faster on the M1 Mac mini by running Qwen3-ASR
(on MLX) in the local speech server, behind one setting, with Whisper loading instead whenever Qwen cannot.

## Findings (2026-10-29, M1 Mac mini, Reachy running)

Benchmark in `~/stt-bench` (not in the repo): 25 real calls and 272 false wakes labelled from the logs (3
of those were real calls Whisper misheard, so 28 calls), 60 random room clips, and 36 requests in
en/es/it/pt spoken by macOS voices, each clean and over real room noise.

| Model | 4 s clip | Memory | Request word errors clean / noisy | "Maribel" (noisy) | Calls with the name heard |
|-------|----------|--------|-----------------------------------|-------------------|---------------------------|
| whisper-small (room) | 0.44 s | 1.3 GB | 5.6% / 17.2% | 5/6 | 12/28 |
| whisper-large-v3-turbo (requests) | 1.62 s | 2.2 GB | 0.9% / 3.3% | 4/6 | 8/28 |
| Parakeet TDT 0.6B v3 | 0.26 s | 2.0 GB | 1.4% / 6.0% | 5/6 | 9/28 |
| Qwen3-ASR 0.6B 8-bit + names hint | 0.17 s | 1.1 GB | 2.3% / 5.1% | 6/6 | 19/28 |
| Qwen3-ASR 1.7B 8-bit + names hint | 0.36 s | 2.4 GB | 0.5% / 3.3% | 6/6 | 15/28 |

- Without the hint ("Reachy, Richie, Maribel" as context), Qwen hears the name no more often than turbo.
- With it, unclear audio sometimes comes back as the hint itself ("Maribel, Richie, Reachy."): 6 of 439
  clips for 0.6B, 2 for 1.7B. Each would be a false wake.
- Qwen cannot be limited to the household's languages (only one forced language); it returned non-Latin
  text on some noise (the app's `LOCAL_STT_LATIN_ONLY` drops it), and a three-word Portuguese question
  came back as nonsense.
- Empty clips (0.01 s) raise in Qwen.

## Decisions

- Engines: Qwen3-ASR 0.6B 8-bit for the room and 1.7B 8-bit for requests, both with the names hint
  (`moona3k/mlx-qwen3-asr-*-8bit`, from the community `mlx-qwen3-asr` 0.4.4 package, Apache-2.0).
  Parakeet stays out: it missed as many calls as turbo.
- One setting, `LOCAL_STT_ENGINE=qwen` (default `whisper`), read by the launcher from `.env` or the
  environment. The server's Qwen models answer to the Whisper model names the app already sends, so the
  app and its `LOCAL_STT_*` settings do not change and switching back is one line.
- Fallback at load: if `mlx-qwen3-asr` is missing or a Qwen model fails to load, that slot loads its
  Whisper model as today and the log says why. No per-clip fallback: keeping Whisper loaded as well
  would double the memory (about 3.5 GB more).
- Echo guard: a run of two or more different hint words in a row ("Maribel, Richie, Reachy") is removed
  from the text; a single name ("Richie.") is kept.
- Clips under 0.1 s return no text without reaching the model.
- Dependency: `mlx-qwen3-asr` and `mlx` installed into the `transformers` uv tool's Python on the mini
  (`uv pip install --python … mlx-qwen3-asr==0.4.4`). Model weights come from Hugging Face once; audio
  never leaves the machine. A later `uv tool upgrade transformers` drops the package, and the fallback
  then loads Whisper.

## Scope

1. `scripts/whisper_server.py`: `QwenTranscriber`, `strip_hint_echo`, shared audio loading, `--engine`,
   `--qwen-model`, `--qwen-request-model`, `STT_NAME_HINT`; routing by the name the app sends.
2. `scripts/start-reachy.sh`: `LOCAL_STT_ENGINE` from the environment or `.env` (that key only; the
   file is never sourced) passed as `--engine`.
3. Docs: this plan, hybrid-runtime feature doc, README, `.env.example`.
4. Out of scope: per-clip fallback, limiting Qwen to the household's languages, Parakeet.

## Acceptance criteria

- [x] Unit tests: echo guard, hint passed and echo removed, tiny clips skipped, language code mapped,
      load failure falls back to Whisper, a Qwen request model is still chosen by the Whisper name.
- [x] Full gate passes.
- [x] On the Mac mini, a test server on another port (`--engine qwen`): both Qwen models load in 6 s and
      answer to the Whisper names; synthetic Spanish, Italian and English requests (Maribel included)
      transcribed right; a 0.01 s clip returns no text; a Qwen model that cannot load falls back to
      whisper-small, which then transcribes.
- [ ] On the Mac mini with `LOCAL_STT_ENGINE=qwen`: the server logs Qwen for both slots, `took_s` near
      the benchmark, spoken commands and "Reachy" wakes work in Spanish and English.
- [ ] A day of logs: no rise in false wakes (`Wake name check heard=True` without a request) or in
      `echo=True` lines.

## Risks

- Community MLX port; a bad update could break loading (the fallback covers it; versions are pinned).
- The hint can still be echoed as a single name on noise; the wake name check would then pass.
- Short Portuguese requests may be misheard more than with turbo.

## Test strategy

`tests/test_whisper_server.py` with fake Qwen sessions; full gate; on the mini, the server started by hand
on another port against saved synthetic requests, then the live switch.

## Progress log

| Date | Update |
|------|--------|
| 2026-10-29 | Benchmarked Whisper, Parakeet and Qwen3-ASR on the mini; chose Qwen with a Whisper fallback. |
| 2026-10-29 | Built the engine setting, echo guard and load-time fallback; gate 2046 passed, 4 skipped. Installed `mlx-qwen3-asr` 0.4.4 (adds only `mlx`, `mlx-metal`) into the mini's `transformers` tool Python; test server checks above. Per-clip times match the benchmark (1.7B 0.84-1.14 s on 2-3.5 s requests, 0.6B 0.36-0.42 s; librosa's first decode adds 0.3 s once). Reachy still runs Whisper. |
