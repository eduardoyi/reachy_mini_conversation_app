# Spotify playback control

## Behavior and integration

The optional `spotify_control` tool resumes existing playback, pauses, skips one
track or reports current playback on one configured Spotify Connect device.
It is included in the household companion profile; missing configuration returns
an error without networking. It does not transfer playback,
change volume or run background polling. The simulator and real app share this
standard tool path; no Reachy SDK internals change.

An active conversation's explicit tool call enters the shared tool registry,
then this tool uses Spotify HTTPS endpoints. The existing hybrid runtime keeps
cloud tool execution out of local ambient mode. The tool itself does not own or
inspect microphone audio or runtime state. Direct Python callers can invoke it
outside a conversation, so they must enforce their own authorization boundary.

## Configuration and authentication

- `SPOTIFY_CREDENTIALS_FILE`: private JSON file outside the repository containing
  `client_id`, `access_token`, `refresh_token` and numeric `expires_at`.
- `SPOTIFY_DEVICE_ID`: exact authorized device ID. No fallback to another device.
- Tool action: `resume`, `pause`, `next`, `status`, `search`, `play`, `queue`; returns status or error.

This Mac completed a one-time Authorization Code with PKCE flow using the
registered `http://127.0.0.1:8765/callback` and scopes
`user-read-playback-state user-modify-playback-state`. The setup helper and tokens
live in `~/Documents/Reachy/private-spotify/`, not the repository. Credentials
were saved with owner-only permissions. The private `connect.py` helper is a
local setup artifact, not a shipped cross-platform authentication UI.

Expired access tokens refresh before requests. Refreshed credentials replace the
old file atomically using a private temporary file. Refresh-token expiry/revocation
requires authorization again. Do not run multiple app processes sharing this file;
command/refresh serialization covers one tool instance, not separate processes.

## Boundaries and failures

Each operation checks the pinned device is available and unrestricted. Mutations
send its ID explicitly. Status does not expose another device's track. Spotify
can report unavailable playback until a song has been opened on the Mac. Device
IDs may change; reselect intentionally rather than following a different player.

Requests use a ten-second HTTP timeout, no redirects, and no automatic retries.
A timeout/cancellation after transmission cannot undo a command Spotify already
received. Errors contain no credential or provider response payloads. Requests
are serialized. Rate limiting returns an error for the user to try later.

Track title/status returned by the tool can enter the active conversation provider
so it can answer the user's question. No music audio is downloaded, trained on or
sent to the model. The household profile instructs against retaining track metadata
in memory. Tokens never enter tool results. There is no automatic music ducking;
headphones are useful for the first simulator test.

## Key files and extension points

`tools/spotify_control.py` implements the tool, `profiles/household_companion/profile.md`
enables it, and the existing profile-toolset store may override enabled tools.
Use the existing bounded request/error behavior when adding actions; do not add
arbitrary URLs, device selection by model argument, or automatic skip retries.
Existing `httpx` is reused; no dependencies added. No architecture diagram change
is required beyond the existing application-tool boundary.

## Validation

`tests/tools/test_spotify_control.py` covers explicit device targeting for three
mutations, other-device status exclusion, 401/403/404/429/500 failures without
retries or secret leakage, refresh persistence, timeout, cancellation and disabled
configuration. Focused run: 13 passed. Full gate result is recorded in the plan.
Live account authorization and device discovery passed. Human voice playback
acceptance passed on 2026-09-10 for pause, resume, next and status (user report
and successful API responses in the supplied log); no new robot movement or media pipeline was introduced.

Official references: [PKCE](https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow),
[player API](https://developer.spotify.com/documentation/web-api/reference/start-a-users-playback),
[refresh tokens](https://developer.spotify.com/documentation/web-api/tutorials/refreshing-tokens).

## Search and selection extension — 2026-09-10

Search takes query (1–250 characters) and search_type (track by default; artist,
album, episode, playlist supported), returns at most five Spotify labels and URIs.
No device needs to be online for search, though configuration is required.
Null/unplayable/malformed results are omitted. Search queries are suppressed from
httpx request logs. Returned labels are untrusted and enter the active provider.

Play/queue require an exact URI from this tool instance's recent search. A maximum
of 50 URI entries expire after ten minutes; no search content is saved to disk.
Tracks and episodes play using uris, artists/albums/playlists via context_uri.
Queue supports individual tracks/episodes and appends to Spotify's queue; it does
not promise immediate next position. No mutation retries or device fallback.
Existing playback scopes suffice; private-library browsing is not added. Podcast
show browsing/latest-episode selection and personalized recommendation engines
are outside this extension. Year filters can express 2000–2009 requests.

Validation of search extension: 24 focused tests passed. Full Ruff/format/mypy,
pytest (916 passed, 2 opt-in skips in 21.26s), uv lock check and diff check passed.
Live read-only searches returned 5 tracks, 5 artists, 5 albums, 5 episodes and
2 playlists. No live play/queue mutation was performed; spoken selection and
queue acceptance remain pending. No new dependencies or authorization scopes.

2026-09-10: user reports all suggested live voice tests passed, including song
and artist playback, podcast search, queue addition and a 2000s request. The
search/play/queue milestone is accepted on the Mac; preceding pending live-test
notes describe the state before this user acceptance.
