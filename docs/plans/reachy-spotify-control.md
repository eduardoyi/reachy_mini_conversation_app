# Reachy Spotify playback control

- Status: `complete: Mac playback and search/play/queue accepted`
- Owner: Reachy project
- Created: 2026-09-10
- Last updated: 2026-09-10

## Scope and assumptions

User has Spotify Premium and selected the Mac for real playback. Add optional
voice control after the accepted headless companion MVP. Initial scope: resume,
pause, next track and current playback status; song/playlist selection follows.
No new wake training. Spotify authorization and the optional tool are now implemented; live voice playback remains pending.

## Implementation order

1. Sign into Spotify developer dashboard and verify app creation/access.
2. Configure a local OAuth flow with minimal playback scopes; retain credentials
   privately, never expose them to the model or logs.
3. Implement an optional tool using existing tool/provider contracts, explicitly
   targeting the selected Mac and returning useful errors for unavailable devices.
4. Test fake API success, denied/expired authorization, timeout, rate limiting,
   cancellation and device targeting; run the repository gate.
5. User-supervised real Mac playback test and simulator voice test.

## Acceptance and risks

A spoken request controls the selected Mac only. No playback calls in ambient
mode. No automatic device switching or unexpected retries of skip actions.
The Spotify account/device may be unavailable; fail gracefully. Check current
Spotify API access and applicable policy before finalizing implementation.
The user's login and any required developer agreement are setup dependencies.

## Documentation

Update README, .env.example for any new configuration, a durable feature document,
this plan and the index when implementation and live verification proceed.

## Progress

2026-09-10: inspected existing tool setup; no Spotify integration found. Opened
Spotify developer sign-in in the in-app browser. Awaiting user login to verify
account access. No playback changed, credentials created or dependencies added.

2026-09-10 account check: login succeeded. The rendered dashboard shows no apps
and no Create App button (accessibility and screenshot checked). No credentials
were created and no playback was changed. Official documentation describes a
Create App button, but its absence here leaves the Web API setup blocked; the
account-specific cause is unconfirmed. A Mac-only local desktop-control option
can be considered separately; it would not supply a portable Pi integration.

Correction: the user supplied a screenshot showing Create app and Spotify
Soloist API Key controls. The earlier blocked conclusion was premature and is
superseded. Compare official Soloist capabilities before choosing authentication.

2026-09-10 implementation: Reachy Home created by the user; PKCE authorization
completed with playback-read/control scopes and a private credential file. The
exact MacBook Pro (2) Connect device is pinned in ignored .env settings; no client
secret was needed. The household tool selection includes spotify_control. No
new dependencies, SDK patches, music download, training or playback changes.

Implemented resume/pause/next/status with pre-request refresh, atomic private token
replacement, serialized requests, explicit device IDs, bounded network waits,
no mutation retry, and safe errors. Self-review checked token exposure, cancellation
and cross-device behavior. Authentication setup remains a private local helper;
a general account-connection UI and multi-process credential locking are deferred.

Validation: 13 focused tests passed. Full gate commands:
`.venv/bin/ruff check . --fix && .venv/bin/ruff format . &&
.venv/bin/mypy --pretty --show-error-codes && .venv/bin/pytest tests/ -q && uv lock --check`
passed: 905 tests, 2 native opt-in skips, mypy 62 files; no new dependency changes.
Real tool status request succeeded with No current playback. No audible play/pause
or skip verification is claimed. Next: user starts a song in Mac Spotify and
restarts the conversation app, then asks pause/resume/next/what is playing.
Feature contract and test inventory: ../features/spotify-control.md.

2026-09-10 live acceptance: user reports the commands worked great. Supplied
log confirms Gemini-dispatched spotify_control calls and HTTP 200 for pause,
resume, next and playback status between 18:05:46 and 18:06:19, targeting the
configured Mac. This is the expected active-cloud conversation tool path, not
a local speech-intent implementation. Initial playback MVP accepted; song search
and local-only speech command routing remain outside scope. Documentation-only
receipt update; no code changes or test rerun.

2026-09-10 authorized extension: search/play/queue, reusing the authenticated
client and pinned Mac. Initial playback milestone remains accepted. Add bounded
search, recent-result URI validation, context playback and track/episode queue
append. No new dependencies/scopes or changes to the wake/runtime boundary.
Tests cover request bodies, invented selection rejection, null results, queue
restrictions and search-log privacy, alongside existing failure/cancellation tests.

Validation of search extension: 24 focused tests passed. Full Ruff/format/mypy,
pytest (916 passed, 2 opt-in skips in 21.26s), uv lock check and diff check passed.
Live read-only searches returned 5 tracks, 5 artists, 5 albums, 5 episodes and
2 playlists. No live play/queue mutation was performed; spoken selection and
queue acceptance remain pending. No new dependencies or authorization scopes.

2026-09-10 live acceptance: user reports all suggested spoken tests worked:
specific song playback, artist playback, podcast search, queue addition and an
open-ended 2000s request. Search/play/queue extension accepted for the supervised
Mac setup. This receipt is user-reported; no additional log or quantitative
benchmark was supplied. Earlier pending statements are superseded for this
milestone. No code changes or test rerun for this documentation update.
