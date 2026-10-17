# Feature: AI usage and cost tracking

- Status: `in-progress` (built and unit-tested; first live sessions pending)
- Last updated: 2026-10-18

## Overview

Every Gemini Live connection and every OpenAI call writes one line to
`usage.jsonl`, next to the household memory file
(`~/.local/share/reachy_mini_conversation_app/`). Each line holds the token
counts the provider reported, split by kind (`input_text`, `input_audio`,
`input_image`/`input_video`, `input_cached`, `output_text`, `output_audio`,
`thinking`), and an estimated cost.

The OpenAI calls are memory capture, the daily memory tidy-up and the addressee
check.

Costs are estimates: tokens times list prices. The provider bills (Google AI
Studio or Cloud Billing, and the OpenAI usage dashboard) are authoritative.

## How Gemini Live reports usage

Gemini Live sends one `usage_metadata` per turn. Its prompt count covers the whole
conversation so far, and Google bills every turn's full context, so a connection
costs the sum of its turns. Measured on 2026-10-18 with
`gemini-3.8-live-extended-thinking`: two text turns reported prompts of 2,951 and
then 3,030 tokens, and each reply was split into audio and thinking tokens.
`gemini_turn_tokens` splits each report by modality and counts prompt tokens it
cannot attribute as text. `GeminiLiveHandler._record_usage` writes one record per
connection when it ends. A rotated long conversation therefore writes one record
per connection. The log line is `Gemini session usage est_cost=$… turns=… <tokens>`.

## Prices

`usage_costs.DEFAULT_PRICES` holds USD per 1M tokens.

- **Gemini 3.8 Live:** text in 0.75, audio in 3.00, image/video in 1.00, text out
  4.50, audio out and thinking 12.00. These come from third-party reports of
  Google's paid tier; no official rate card was found on 2026-10-18.
- **gpt-5.6-luna:** in 0.20, cached in 0.02, out and reasoning 1.20 (standard tier
  after the 2026-07-30 price cut).
- **Overrides:** set `USAGE_PRICES_FILE` to a JSON file shaped
  `{"model-prefix": {"rate": price}}` to change or add prices.
- **Unknown models:** they record tokens with a `null` cost and count as
  `unpriced_records`.

## Where to see it

- **Logs:** one `Gemini session usage` line per conversation.
- **Settings → AI costs:** today, last 7 and 30 days, cost by feature, and the
  latest conversations with length, turns and cost. Backed by `GET /api/usage`,
  which only answers this computer.

## Constraints

- Tracking never breaks a feature: errors are logged at debug level and dropped.
- The ledger records counts and costs only, never transcripts.
- Tests redirect the process ledger to a temporary file (an autouse fixture in
  `tests/conftest.py`).

## Tests

- `tests/test_usage_costs.py`: Gemini and OpenAI token splitting, pricing, price
  overrides, ledger summary by period and feature, the `/api/usage` route, and
  test isolation.
- `test_turn_usage_is_summed_into_one_cost_record_per_connection` in
  `tests/test_gemini_live.py`.
