# Plan: Hand voice requests to Maribel and speak her replies

- Status: `in-progress` (deployed with Funnel on the Mac mini; first spoken test done, fixes from it deployed)
- Last updated: 2026-10-28
- Feature doc: [Maribel hand-off](../features/maribel-handoff.md)

## Goal

Maribel is the household's Grok Bot assistant, running as a Cursor automation. The household wants to say
"Richie, ask Maribel to…" and hear her short answer from Reachy. Long tasks report back in the Grok Bot
app, never through Reachy.

Her webhook only accepts a request (HTTP 200 with a run id) and starts her in the background. She answers
by POSTing to a callback on Reachy. A test on 2026-10-28 was accepted in 0.65 s; her reply (in the app,
since no callback was set up yet) came 25-85 s later.

## Decisions (with the household, 2026-10-28)

1. **Only when Maribel is named.** Two paths, both explicit:
   - A woken request that starts with "ask/tell Maribel…" (or the Spanish, Italian or Portuguese
     equivalent) is caught locally as a quick command. Gemini never sees it.
   - In a Gemini conversation, the `ask_maribel` tool, which the model may only use when the person
     names Maribel or answers her question.
2. **Tailscale Funnel** publishes only `/maribel/reply` on the Mac mini's tailnet name (in its `.env` as
   `MARIBEL_CALLBACK_URL`). The server listens on loopback only.
3. **Follow-ups.** After a question from Maribel, Reachy listens about 15 s without his name, with the
   antennas perked: what he hears goes to her as the answer. For 2 minutes after her question, a woken
   "tell Maribel…" is also sent as the answer (same conversation, `in_reply_to` set). After that, a new
   conversation.
4. **Timing.** Reachy says "Asking Maribel", POSTs, plays a thinking move and waits up to 10 s. With no
   reply by then: "I've passed that to Maribel. She'll get back to you." A reply arriving later, up to
   2 minutes after the request, is spoken once Reachy is free (after a conversation or command ends);
   later ones are dropped and she follows up in the app. Only the latest request's late reply is spoken.

## Design

- `maribel.py`
  - `MaribelLink.send(text)`: POST `{version, request_id, conversation_id, in_reply_to, text, sent_at
    (Europe/Lisbon), callback_url}` with `Authorization: Bearer`; retry once after 2 s on non-2xx or a
    network error; returns the request id or None.
  - `MaribelLink.first_reply(request_id, timeout)`: the first callback within the timeout, or None. A
    lock decides whether a callback is first (handed to the waiter) or late (`on_late_reply`).
  - `MaribelLink.receive(authorization, body)`: checks the bearer secret (constant time), validates the
    body, drops unknown request ids, records an open question or closes the conversation, and returns
    the HTTP status. Called from the server thread; it never blocks on speech.
  - `serve_replies(link, port)`: a standard-library `ThreadingHTTPServer` on 127.0.0.1 (like the e-ink
    server). Only `POST /maribel/reply`, bodies up to 16 KB.
  - `python -m reachy_mini_conversation_app.maribel fake-reply`: the test script.
- `quick_commands.py`: `maribel_request(text)` recognizes the trigger; `QuickCommands` answers with
  "Asking Maribel" and an `after` step that sends, waits and returns her `speak` text.
  `QuickReply.after_timeout_s` lets that step outlast the usual 8 s.
- `hybrid_runtime.py`
  - `expect_answer(handler)`: after the current quick command, listen once without the name (reason
    `awaited_answer`: listening path, request clip waiting up to 15 s for speech, no Gemini) and hand
    the text to `handler`.
  - `request_announcement(text, language, answer=None)`: thread-safe. Queued until Reachy is free, then
    spoken as its own short activation; a question then listens for the answer.
- `tools/ask_maribel.py`: the Gemini tool, enabled when Maribel is configured.
- `main.py`: builds the link and server when configured and wires late replies to announcements.

## Assumptions

- Maribel sends one callback per request and keeps `speak` to one or two sentences.
- Callbacks during a restart of the app (about 20 s) fail; she follows up in the app.
- The wake still needs Reachy's name or an antenna touch; "Maribel" alone does not wake him.

## Risks

- **Her first reply is usually slower than 10 s**, so most requests will get "passed that to Maribel"
  and then her answer a little later. Logs record the delay; the 10 s and 2 minutes get tuned from them.
- **The 15 s answer window** sends whatever speech it hears (TV, another person) to Maribel.
- **The public endpoint.** Only one path is published; it needs the secret and a request id Reachy sent.
  Bodies are capped and never logged.
- **Privacy.** The request text goes to Maribel's webhook by the household's explicit choice. Logs keep
  ids, types and delays only; secrets and texts are never logged.

## Acceptance criteria

- [x] A woken "ask Maribel…" says the acknowledgement, POSTs the right body and speaks her reply within 10 s.
- [x] Non-2xx or network error: one retry after 2 s, then "I couldn't reach Maribel right now."
- [x] No reply in 10 s: the passed-on line; a reply within 2 minutes is spoken once Reachy is free.
- [x] A question opens the 15 s answer window; the answer goes with the same conversation id and
      `in_reply_to`; silence closes it without opening Gemini.
- [x] Callback: wrong or missing secret 401, unknown request id 404, bad body 400, good one 200 at once.
- [x] The tool sends the person's words and returns only her `speak` text.
- [x] Full gate passes.
- [x] Funnel publishes only `/maribel/reply`; `fake-reply` from outside gets 401 without the secret.
- [x] Live: a spoken request reaches Maribel and Reachy speaks her answer.
- [ ] Live: a spoken request caught locally ("Richie, ask Maribel…") speaks her answer.

## Test strategy

`tests/test_maribel.py` (link, callback server over loopback, fake webhook), `tests/test_quick_commands.py`
(trigger phrases, the after step), `tests/test_hybrid_runtime.py` (answer window, announcements),
`tests/tools/test_ask_maribel.py`. Live: `fake-reply` through the Funnel URL, then a spoken request.

## Documentation

`docs/features/maribel-handoff.md` (new), `docs/features/hybrid-runtime.md`, README configuration table,
`.env.example`, this plan and the plans index.

## Progress log

| Date | Update |
|------|--------|
| 2026-10-28 | Webhook tested (Bearer, 200 in 0.65 s, reply 25-85 s later in the app). Secret generated into the mini's `.env` and given to Maribel by the household. Plan written and built. |
| 2026-10-28 | Deployed (4439182); callback server on 127.0.0.1:8791. Funnel needed HTTPS and the funnel node attribute in the tailnet (the Mac App Store CLI only runs from a Terminal window, not over SSH). Its first HTTPS request waited for the certificate; the three public relays started answering over about 5 minutes. From outside: no or wrong secret 401, unknown request 404, other paths 404. |
| 2026-10-28 | First spoken test, in Spanish: "le puedes preguntar a Maribel…" missed the local catch and went to Gemini. The person kept talking, so Gemini's first call was cancelled and a second sent (two requests); the latest one's answer came 17 s later and was spoken once the conversation closed. The 8 s clip cut the request off ("…para el"), and httpx logged the webhook address. Fixes: more phrasings, `QuickReply.whole_request` sends cut-off clips to the conversation, and an `httpx` log filter hides the address. |
| 2026-10-28 | Second spoken test (18:10, through Gemini again: the antenna touch came while the microphone was still starting and Whisper misheard the clip). Her answer came 27 s later and was spoken after the conversation; the webhook address no longer appears in the logs. Gemini's tool call was again cancelled by the person's speech and repeated, so she got the request twice. Fix: the tool's repeat within 20 s that adds no words waits on the request already accepted. |
