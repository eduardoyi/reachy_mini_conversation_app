# Plan: Skill tools to cut Gemini Live's per-turn cost

- Status: `in-progress`
- Last updated: 2026-10-25
- Feature doc: [skill-tools.md](../features/skill-tools.md)

## Why

On 2026-10-25 Gemini Live cost $3.38 over 141 turns. A measured session showed each turn's prompt
starts with a fixed ~10.4k tokens (instructions ~4.5k incl. household memory ~1.5k; 26 tool
declarations ~5.3k; Google Search ~70), about $1.05 of the day. Live has no context caching (pricing
page lists none; a forum reply says it is unsupported for 3.8 Live), compression only trims history,
and the configuration cannot change mid-session.

## Decision

A light progressive disclosure: rarely used tools become one menu line in `use_skill`, with full
instructions from `skill_help` on demand. Behind `SKILL_TOOLS_ENABLED` (default on) so the household
can switch back if it feels clunky. The `dance` move list no longer repeats every move's description
(the enum already names them).

## Acceptance

- Fixed block measurably smaller on the live model; skills used correctly on the live model.
- Switching off restores the previous declarations; full gate clean.
- Household test: no noticeable clunkiness with lights, TV, music and memory requests.

## Execution log

- 2026-10-25: Implemented `tools/skills.py` and the fold/dispatch in `core_tools`. Live measurement:
  fixed block 10,444 → 7,830 tokens (−25%; dance description −510, skills −2,104). About $0.28/day
  at 141 turns, minus one extra generation (~8–10k prompt tokens) for the first use of each skill in
  a session. Live behaviour: lights, TV and remember went `skill_help` → valid `use_skill`; weather
  stayed direct. Household test pending.
- 2026-10-27: Household test on the Mac mini: "play Spotify" went `skill_help` → `use_skill` search →
  `skill_help` again, then a spoken reply; the follow-up window closed with nothing playing. Spotify
  needs search and play in a row, so `spotify_control` is a direct tool again (about +350 tokens per
  turn); the other skills stay.
