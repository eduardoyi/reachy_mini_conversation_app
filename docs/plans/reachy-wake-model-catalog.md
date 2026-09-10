# Reachy wake model catalog

- Status: `evidence-only; Official33 selected for the supervised simulator MVP`
- Last updated: 2026-09-09
- Related records: [custom training plan](custom-reachy-openwakeword-training.md), [benchmark](../../HYBRID_BENCHMARK.md), [wake-word feature](../features/wake-word.md)

## Scope

This catalog records the retained private model and evaluation receipts. `official33` is chosen for the user-supervised simulator MVP and remains off by default. The catalog records existing evidence; it is not a new training plan. There is no production acceptance and no new training is planned.

## Retained artifacts

### Official33 — chosen supervised simulator MVP

- Model: `/Users/eduardoyi/Documents/Reachy/private-wakeword/official33/reachy-official33.onnx`
  - SHA-256: `f94693ce16d970d8ab0524f36854491d7f6067ae0f46b23cea624e86e5928427`
- Training receipt: `/Users/eduardoyi/Documents/Reachy/private-wakeword/official33/training-results.json`
  - SHA-256: `eaf649d63e65a985501acc253752922fa097606894aad225300dfa0043aca8c2`
  - Its `export_sha256` matches the model hash.
- Simulator bundle: `/Users/eduardoyi/Documents/Reachy/private-wakeword/simulator-official33/`
  - `/Users/eduardoyi/Documents/Reachy/private-wakeword/simulator-official33/melspectrogram.onnx`: `ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f`
  - `/Users/eduardoyi/Documents/Reachy/private-wakeword/simulator-official33/reachy-official33.onnx`: `f94693ce16d970d8ab0524f36854491d7f6067ae0f46b23cea624e86e5928427`
  - `/Users/eduardoyi/Documents/Reachy/private-wakeword/simulator-official33/embedding_model.onnx`: `70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f`
  - `/Users/eduardoyi/Documents/Reachy/private-wakeword/simulator-official33/provenance.json`: `a9715ef6154377b4665ce349c42e7d2a89006b135455824f7104cddc171c700f`
- Evidence: `17/20` fresh calls and `1` false wake in `49.50` minutes. This is a supervised simulator choice, not a quality or hardware sign-off.

### Baseline27 + verifier32 — promising higher-recall reference

- Baseline model: `/Users/eduardoyi/Documents/Reachy/private-wakeword/household-adaptation21/candidate-run-27/reachy-candidate.onnx`
  - SHA-256: `24f51c4c8ab06ba018f9427eed9643084ad76f50939d442d6b4a5e3b3d4b7dad`
- Verifier fit receipt: `/Users/eduardoyi/Documents/Reachy/private-wakeword/event-verifier32/fit.json`
  - SHA-256: `338c8f325096ae0e77ace5639c3ba77eecb655ebc26d23546cc9e6e697a498d5`
- Verifier coefficients: `/Users/eduardoyi/Documents/Reachy/private-wakeword/event-verifier32/probe.npz`
  - SHA-256: `0bb11b604b1927524a6bef4225e6c58546eb691200c34898bba362e8719f01c4`
- Evidence: `18/20` calls and `3` false wakes in `49.50` minutes. The verifier is paired with baseline27 and remains a reference only.

### Parallel35 — diagnostic only

- Receipts: `/Users/eduardoyi/Documents/Reachy/private-wakeword/parallel35/protocol.json`, `/Users/eduardoyi/Documents/Reachy/private-wakeword/parallel35/official33.json`, `/Users/eduardoyi/Documents/Reachy/private-wakeword/parallel35/official34.json`, `/Users/eduardoyi/Documents/Reachy/private-wakeword/parallel35/verifier32.json`, and `/Users/eduardoyi/Documents/Reachy/private-wakeword/parallel35/results.json`.
- `results.json` SHA-256: `9d0aee5a7127639425d0f5d43687a67917c5e12a8be9a143117741180f32aa8d`.
- Event-level OR of official33 and (baseline27 confirmed by verifier32): `19/20` calls and `4` false wakes in `49.50` minutes. It was a replay diagnostic with no fit, threshold change, runtime integration, or new recording.

### Official34 — rejected

- Model: `/Users/eduardoyi/Documents/Reachy/private-wakeword/official34/reachy-official34.onnx`
  - SHA-256: `70d2b832bb7b4bc0d98c1fa22491027abf80298856ce79220bed2b3b6946e6c0`
- Training receipt: `/Users/eduardoyi/Documents/Reachy/private-wakeword/official34/training-results.json`
  - SHA-256: `7026d8712a6d007a4cadb76b81ac0978aa1c989e2a04033c872d3705fbda6d59`
  - Its `export_sha256` matches the model hash.
- Evidence: `16/20` calls and `7` false wakes in `49.50` minutes. Rejected for runtime use.

## Assumptions and boundaries

- Training used the previously authorized `73` household training sources. Later holdouts were excluded from training but informed design and replay decisions, so they are not independent production acceptance.
- These paths are private local artifacts. No private audio or credentials are committed here. All listed hashes were checked read-only.
- Official33 may be used only in the supervised, off-by-default simulator path. Production remains unaccepted; hardware microphone/AEC, latency, resource, and broader independent validation are still open.

## Acceptance

The retained-model decision is accepted only as a bounded simulator MVP choice: use the frozen Official33 bundle, keep activation off by default, and preserve manual supervision and the existing privacy barrier. It does not accept any model for production.

## Tests

- `shasum -a 256` was run read-only against the listed models, receipts, verifier fit, simulator assets, and Parallel35 results; the hashes above matched the receipts/checksum record.
- Existing local replay receipts supplied the counts above under network denial. Catalog checksum verification did not open private audio. Separate native replay, headless simulator and microphone checks are recorded in the wake-word feature document; human live and hardware acceptance remain pending.

## Documentation

The custom training plan, benchmark, and wake-word feature document remain the source of truth for phase status, simulator commands, and implementation details. This file preserves the current retained-model decision and artifact identities for later review.
