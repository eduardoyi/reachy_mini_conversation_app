# Find out why Reachy answers nonsense in Gemini conversations

- Status: `in-progress`
- Last updated: 2026-11-05
- Feature: [Conversation record](../features/conversation-record.md)

## Problem

Eduardo reports that in Gemini conversations Reachy sometimes answers something nobody said, mixes two
topics, changes topic mid-sentence, or switches language (Spanish to English or Italian) for no reason.
The logs cannot say why: they keep counts and timings, never what Gemini heard or said.

What the 2026-11-05 logs do show:

- **Request clips that never end.** 98 of 411 request clips (2026-11-01 to 11-05) ran to the 10.6 s cap
  because the room never went quiet; their local transcript averaged 68 characters against 18. Gemini gets
  the whole clip, and the hint quotes all of it as "the request".
- **Replies cut off at once.** In the two longest conversations (18:11 and 18:20) Gemini's own speech detector
  stopped Reachy 14 times, most 0.1–0.8 s into a reply: too soon for a person meaning to interrupt. The
  room (children, the TV) or his own voice in the microphone are the likely causes.
- **The room transcript.** Up to 2,000 characters of whisper's transcript of the room before the wake are
  sent as context. Whisper mishears (437 non-Latin lines were dropped on 11-05; other errors pass).
- **Language.** The household profile never says which language to answer in, and everything Gemini reads
  (instructions, context header, request hint) is English.

## Plan

1. **Record each Gemini conversation on the Mac mini** (opt-in `CONVERSATION_RECORD_ENABLED`, kept
   `CONVERSATION_RECORD_KEEP_DAYS`, default 14): how it started (wake kind, the request as local speech
   recognition heard it, clip length, whether the clip was cut off, whether the request went as audio or
   text), the room context Gemini was given, each user turn as Gemini transcribed it, each reply with whether
   it was cut off and after how long, tool names, how it ended, and the audio Gemini was sent.
2. **Deploy and collect** a few days of conversations. When Reachy says something odd, Eduardo gives the
   time; the record says which cause it was.
3. **Fix what the record shows**, one cause at a time, each measured on the record before and after.
   Candidates: a shorter or smarter clip end, ignoring the hint when the clip was cut off, a stricter
   echo/noise rule for barge-in, a language rule in the profile.

## Privacy

The record holds transcripts and audio of conversations with Reachy, plus the room context already sent to
Gemini. It stays on the Mac mini in owner-only folders, is deleted after the keep period, is never uploaded,
and is off unless enabled. Eduardo agreed to it on 2026-11-05. Findings are reported as counts and patterns,
never quoted.

## Progress

| Date | Step | Result |
|------|------|--------|
| 2026-11-05 | 1 | Log analysis above; record built and deployed (see the feature document). |
| 2026-11-05 | 3 | Language rule added to the household profile at Eduardo's request: answer in the language just spoken, Spanish when unsure, another language only when asked. The record will show whether switches stop. |
