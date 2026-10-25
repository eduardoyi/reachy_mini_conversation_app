# Plan: Now playing on the e-ink screen

- Status: `in-progress`
- Created: 2026-10-25
- Feature doc: [eink-display.md](../features/eink-display.md#now-playing)

## Goal

While Spotify plays on Reachy's speaker (`SPOTIFY_DEVICE_ID`), the TRMNL shows a music card: album
art (show art for podcasts), the song or episode title, the artist and the album. "What song is
this?" answers out loud and puts the card up.

## Decisions (agreed with the household, 2026-10-25)

| Question | Choice |
|----------|--------|
| Which music | Only playback on the configured Spotify device |
| Detecting changes | Poll `/me/player` every 10 s while playing, also right when the song should end; every 30 s when nothing plays; at once after Reachy's own play/resume/pause/next |
| Screens someone asked for | Win while the conversation is open; once it ends, the music card returns at the next song change or after at most 2 minutes |
| Music stops | The card stays 2 minutes after playback stops, then the resting card |
| Long tracks | The music card is exempt from the 30-minute idle return while music plays |
| Album covers | Shown as they are, no Jev check (household choice) |
| Explicit tracks | Shown normally (household choice) |
| Switch | `DISPLAY_NOW_PLAYING` (default on; needs `TRMNL_ENABLED` and Spotify) |
| "What song is this?" | `show_on_display kind=now_playing`: puts the card up at once and returns title, artist and album to say |

## Design

- `now_playing.py`: `fetch_playback()` (one `/me/player` call, only for our device; album art URL
  only from `i.scdn.co`), `NowPlaying` watcher (an asyncio task on the app loop via `LocalStream`'s
  new background-task hook), album-art download and cache (`display/album-art/<id>.png`).
- `eink_display.py`: `render_now_playing()`; screens record who put them up (`requested` or
  `music`) so the watcher knows what it may replace; `keep_alive()` for the music card.
- `spotify_control.py`: a `playback_changed_callback` on the ducker, called after successful play,
  resume, pause and next, so Reachy's own changes show at once.
- Logs never carry titles, artists or albums.

## Test strategy

Unit: playback parsing (track, episode, other device, nothing playing, art host), the watcher's
rules with a fake clock and fake fetch (new song, requested screen during and after a conversation,
the 2-minute returns, keep-alive, stop), the card renderer (long titles, no art), the tool's
`now_playing` kind. Live: real music on the household speaker, the TRMNL showing each song.

## Execution log

- 2026-10-25: Plan agreed; implementation started.
- 2026-10-25: Built: `now_playing.py`, the music card renderer, screen owners and keep-alive, the
  ducker hook, `LocalStream` background tasks, `kind=now_playing`, `DISPLAY_NOW_PLAYING`. Full gate:
  ruff, format, mypy (103 files) clean; 1,915 passed, 4 skipped. Live: the real `/me/player` fetch
  works (0.30 s) but nothing was playing on the speaker, so the card has not yet been seen with real
  music on the TRMNL. Pending: a live check with music playing.
- 2026-10-25: Live check passed. Reachy restarted at 16:55:57 with music on the household speaker: the
  watcher started at 16:55:58, read `/me/player`, fetched the cover from `i.scdn.co` and put the card up
  at 16:55:59 (about 0.5 s); at the next song (16:56) the card changed with the new cover. Both covers
  are cached in `display/album-art/`.
