# Plans

Every design, implementation, migration, benchmark, rollout, and operational
plan for this project lives in this directory. A plan is not complete merely
because code has landed: its status and evidence must be updated until the
acceptance criteria are met.

## Plan index

| Plan | Status | Last updated | Scope |
|------|--------|--------------|-------|
| [Phone admin app](phone-admin-app.md) | `in-progress` | 2026-10-31 | A PWA for Eduardo and Adriana: `reachy-admin` on loopback behind Tailscale Serve (9443, never Funnel's 443) and a two-login allowlist; people cards (groups, call numbers, faces), live camera with head turns (lifts the head while asleep), privacy switch (mic and camera cut at capture, only the app turns it off), quiet hours, status, activity, settings, Web Push; running on the Mac mini, redesigned after first phone use; phone checks pending |
| [Games on Reachy](games.md) | `approved` | 2026-10-31 | A shared game host (names in es/en, a yes before every start, Gemini through `play_game` rules-then-start, games own lights/music/screen, pause and quit by voice, name or antenna, mid-game requests offer to end the game) and the color hunt first (Jev checks the color held up to the camera while a tune speeds up); writing practice moves onto it; Jev spike done (0.16 s per frame; blue, yellow, green, white, black, pale pink right; red and hot pink read as orange, so rojo accepts orange), nothing built |
| [Writing practice on the TRMNL](writing-practice.md) | `in-progress` | 2026-10-30 | "Enséñame a escribir PAPÁ": one big capital at a time with the word filling in above, the letter's name and sound spoken (cached), the next on a head pat or "¡ya!" heard without the name; started locally, Gemini only for long or vague requests; built and tested, live check pending |
| [Drawing lessons on the TRMNL](drawing-lessons.md) | `in-progress` | 2026-10-30 | "Enséñame a dibujar una jirafa": four numbered panels cut from one image-model sheet (new lines black, earlier grey), captions from Gemini 3.8 Flash reading the sheet; faces only on things with faces; cached per subject; built and tested, live check pending |
| [WhatsApp voice calls through Reachy](whatsapp-calls.md) | `draft` | 2026-10-30 | "Richie, call Abuela" rings her WhatsApp from the spare number (a second linked device running meowcaller, unofficial: ban risk accepted); her voice through Reachy with his head moving; approved contacts only (Eduardo first); only Eduardo or Adriana start calls (face check); incoming calls announced with caller id on the TRMNL, a silent ring while asleep; staged, nothing built |
| [Voice messages and photos to family groups](whatsapp-family-messages.md) | `in-progress` | 2026-10-29 | "Send a message / a picture to Sergio": a beep, a recording until 3 s of silence, a WhatsApp voice note (or the photo) to the "Reachy <> Sergio" group from a contacts file; "who?" when no name is known; message and photo checked live on the Mac mini; face framing for photos added |
| [Photos to the family WhatsApp group](whatsapp-photos.md) | `in-progress` | 2026-10-29 | "Take a picture" counts three-two-one, keeps the best of a short burst, shows it on the e-ink screen and sends it to the "Reachy photos" group from Reachy's own WhatsApp (GOWA on the spare number); first step toward the kids messaging family; checked live on the Mac mini (9.7 s request to sent); Gemini's `take_photo` not yet tried live |
| [Catch "Ri-chi" calls the wake model misses](missed-calls-spanish-richi.md) | `in-progress` | 2026-10-29 | official33 scores 0.00 on a Spanish "Ri-chi"; open room segments are checked for the name every 1.5 s instead of waiting up to 20 s for a pause, Spanish spellings added; a wake model retrained with her pronunciation is in progress |
| [Qwen3-ASR as the local speech engine](qwen-speech-engine.md) | `in-progress` | 2026-10-29 | `LOCAL_STT_ENGINE=qwen` runs Qwen3-ASR 0.6B (room) and 1.7B (requests) on MLX in place of Whisper, with the names as a hint and an echo guard; Whisper loads if Qwen cannot; benchmarked on the mini, live switch pending |
| [Hand voice requests to Maribel](maribel-handoff.md) | `in-progress` | 2026-10-28 | "Ask Maribel…" (quick command or `ask_maribel` tool) goes to her Grok Bot webhook; her replies come back through a Tailscale Funnel callback and Reachy speaks them; questions open a 15 s answer window; built and tested, live check pending |
| [Bend an antenna to wake Reachy](antenna-touch-wake.md) | `in-progress` | 2026-10-28 | A held antenna bend (14° from every recent target, 0.2 s) wakes him like his name, even from sleep, no name check; Pollen's daemon version only works with no app running; robot check pending |
| [Whisper checks the name before every wake](wake-name-check.md) | `in-progress` | 2026-10-28 | Every acoustic wake waits for whisper-small (then turbo) to hear the name in the 2.5 s it fired on; threshold 0.95 → 0.3 on the mini; offline 27 → 34 of 38 calls, 59 → 0 false wakes; live check pending |
| [Gentler sleep mode, then no name check](sleep-wake-check.md) | `in-progress` | 2026-10-27 | Restarts start awake; asleep, the accurate model rechecks the name in the wake audio; drop the check once labelled clips show few false wakes |
| [Faster local commands](local-command-speed.md) | `in-progress` | 2026-10-27 | Whisper server encodes each clip once (turbo 3.19 → 1.64 s on the mini), request clips skip the queue, dated logs kept 7 days; MLX benchmarked and not adopted |
| [Drawings with GPT Image 2.5 Flare](openai-drawings.md) | `in-progress` | 2026-10-27 | TRMNL drawings and photo edits move from Gemini to `gpt-image-2.5-flare` (medium, 1024x768); Gemini stays configurable; live check pending |
| [Wake clip capture](wake-clip-capture.md) | `in-progress` | 2026-10-27 | Short local clips around wakes, near misses, names only local STT heard, and random background, to retrain the wake word (music); running on the Mac mini since 2026-10-27 |
| [Wake antenna cue](wake-antenna-cue.md) | `in-progress` | 2026-10-25 | Antennas perk out and hold while Reachy listens, release when he answers; head lift replaces the nod; built and tested, robot check pending |
| [E-ink display (TRMNL)](eink-display.md) | `in-progress` | 2026-10-25 | Drawings, web pictures (Jev check), word cards, text and v3 visual explanations live on the TRMNL; device server is its own process; no daily caps; stories, morning page and calendar deferred |
| [Skill tools](skill-tools.md) | `in-progress` | 2026-10-25 | Rare tools behind use_skill/skill_help; fixed block −25% measured live; household test pending |
| [Now playing on the e-ink screen](now-playing-display.md) | `in-progress` | 2026-10-25 | Spotify song card on the TRMNL; built, tested and checked live with music (card per song with cover) |
| [Camera photos on the e-ink screen](camera-photos-display.md) | `in-progress` | 2026-10-25 | Photos and photo edits from Reachy's camera; built and tested; live voice check pending |
| [Local music controls and a local command model](local-music-controls.md) | `in-progress` | 2026-10-14 | V1 music lowering, volume and wind-down accepted on hardware; V2 local music commands and V3 local command model planned |
| [Reachy Mini Lite hardware bring-up](reachy-mini-lite-bringup.md) | `in-progress` | 2026-10-13 | Stepwise first power-on, robot audio, interruption, hybrid/wake re-tuning; SDK target 1.11 |
| [TypeSafe judgment exploration](typesafe-judgment-exploration.md) | `draft` | 2026-09-17 | Synthetic evaluation complete; background memory verification proposed, no runtime integration |
| [Gemini 3.8 Live Extended Thinking migration](gemini-38-live-extended-thinking-migration.md) | `in-progress` | 2026-09-21 | SDK crash fix user-confirmed; wake acknowledgment and opt-in interruption assist implemented; 1,263 tests passed; speaker retest and live transcript parity pending |
| [Local audio dropout comparison](audio-dropout-comparison.md) | `GPT speaker path accepted; Gemini tiny-chunk fix native-tested, live pending` | 2026-09-13 | Audio diagnostics, SDK flush candidate and live audio acceptance |
| [Household memory lifecycle](household-memory-lifecycle.md) | `in-progress` | 2026-09-13 | M1–M2 user accepted; M3 daily tidy-up implemented; synthetic Luna review passed; live follow-up pending |
| [GPT Live migration](openai-live-migration.md) | `Mac speech movement and speakers accepted; hardware pending` | 2026-09-13 | GPT Live speech, Luna delegation, local movement repair; simulator echo/hearing follow-up |
| [Spotify playback control](reachy-spotify-control.md) | `complete: Mac playback and search/play/queue accepted` | 2026-09-10 | Optional Mac playback voice controls |
| [Reachy hybrid household companion](reachy-hybrid-household-companion.md) | `in-progress: headless MVP accepted; hardware validation pending` | 2026-09-10 | Phases 0–5 |
| [Custom multilingual Reachy openWakeWord training](custom-reachy-openwakeword-training.md) | `Official33 supervised headless MVP accepted; training paused` | 2026-09-10 | Phase 4 custom acoustic wake detector |
| [Reachy wake detector model/training comparison](reachy-wake-detector-comparison.md) | `Training paused; architecture comparison deferred` | 2026-09-09 | Controlled data and architecture comparison |
| [Retained Reachy wake models](reachy-wake-model-catalog.md) | `Official33 chosen for supervised MVP` | 2026-09-09 | Private artifacts, frozen metrics and alternatives |

The required report is [HYBRID_BENCHMARK.md](../../HYBRID_BENCHMARK.md).
The companion loop is implemented in the local checkout; phases 0–3 and functional
phase 5 have prior simulator acceptance with documented limits. Official33 acoustic
wake is now integrated behind an off-by-default setting. Native 20-call adapter
parity and headless SDK/movement integration passed; human-spoken graphical
acceptance, actual robot/Pi measurements, speaker echo outside the accepted Mac
SDK-candidate configuration and some latency/resource
measurements remain open. Training is paused. See the main plan's current-status
table and retained-model catalog before reading the historical execution notes.

## Status vocabulary

Use one of these statuses in both the plan header and this index:

| Status | Meaning |
|--------|---------|
| `draft` | Exploratory; not yet approved for implementation. |
| `approved` | The approach is agreed and ready to implement. |
| `in-progress` | Work is active or required validation remains. |
| `blocked` | A concrete blocker is recorded in the plan. |
| `complete` | Scope, acceptance criteria, and required tests have passed. |
| `superseded` | Replaced by another plan; link the replacement. |

## Plan naming and update rules

- Use one descriptive `kebab-case.md` file per plan.
- Start with a title and metadata for `Status`, `Owner`, `Created`, and `Last
  updated`.
- Record goals, non-goals, decisions, phases, acceptance criteria, tests,
  benchmarks, risks, and next steps.
- Maintain a dated execution log and changelog. Record material deviations
  rather than silently rewriting an approved decision.
- Add or update the index row in the same change as the plan.
- When a plan is `complete`, link the implementation/feature documentation and
  the evidence that supports completion.

## Historical execution notes

Custom-training status at 2026-09-07: sounds **1, 2 and 5** accepted;
GPU setup fixed and four full candidates completed. Candidate 3 remains best
on synthetic tests (400/400 positives, 36/3,168 negative activations, 1.136%).
Under the user-authorized exploratory exception, it detected **19/30 private
positives** (17/25 and 2/5 by speaker), below 27/30. Mac/Linux numerical
agreement passed; the synthetic failure remains recorded. No audio upload,
threshold sweep, negative collection or app integration occurred. All rental
resources were removed: about $1.16 spent, $8.84 remaining, $0/hour; the local
screen added no provider cost. Further model training is not authorized.

Local diagnosis: all 11 misses are name-first phrases (6/17 detected versus
13/13 other placements). Level ranges overlap and quiet-background proxies
match. Paired synthetic concatenation detects 59/60 name-first examples, so
simple placement alone does not reproduce the real gap. Next design focus is
naturally spoken connected phrases; no fifth training attempt is authorized.

Name-isolation diagnostic: 3/11 misses activate after the estimated name-only
cut, versus 0/11 full sentences. Nearby cuts/fade yield 2–4/11, with no clip
detected in every variant. Automatic boundary uncertainty prevents a clear
causal conclusion. Original private recall remains 19/30; no retraining.
