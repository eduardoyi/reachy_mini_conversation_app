import json
import asyncio
import logging
from typing import Callable
from pathlib import Path
from datetime import date, datetime, timezone

import pytest
from openai.types.responses.response import Response
from openai.types.responses.response_output_text import ResponseOutputText
from openai.types.responses.response_output_message import ResponseOutputMessage

import reachy_mini_conversation_app.household_memory_capture as capture_module
from reachy_mini_conversation_app.household_memory import MemorySnapshot, CaptureMutation, HouseholdMemoryStore
from reachy_mini_conversation_app.household_memory_capture import CaptureClocks, MemoryCapture


def _response(text: str, *, status: str = "completed") -> Response:
    output_text = ResponseOutputText.model_construct(annotations=[], text=text, type="output_text")
    message = ResponseOutputMessage.model_construct(
        id="message-1",
        content=[output_text],
        role="assistant",
        status=status,
        type="message",
    )
    return Response.model_construct(
        id="response-1",
        created_at=0.0,
        model="gpt-5.6-luna",
        object="response",
        output=[message],
        status=status,
    )


class _FakeResponses:
    def __init__(self, response_factory: Callable[[dict[str, object]], str]) -> None:
        self.response_factory = response_factory
        self.calls: list[dict[str, object]] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.block = False
        self.ignore_cancellation = False
        self.late_error: Exception | None = None
        self.response_status = "completed"

    async def create(self, **kwargs: object) -> Response:
        self.calls.append(kwargs)
        self.started.set()
        if self.block:
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                if not self.ignore_cancellation:
                    raise
                await self.release.wait()
        if self.late_error is not None:
            raise self.late_error
        return _response(self.response_factory(kwargs), status=self.response_status)


class _FakeClient:
    def __init__(self, response_factory: Callable[[dict[str, object]], str]) -> None:
        self.responses = _FakeResponses(response_factory)
        self.close_count = 0
        self.close_started = asyncio.Event()
        self.release_close = asyncio.Event()
        self.block_close = False
        self.ignore_close_cancellation = False

    async def close(self) -> None:
        self.close_count += 1
        self.close_started.set()
        if self.block_close:
            try:
                await self.release_close.wait()
            except asyncio.CancelledError:
                if not self.ignore_close_cancellation:
                    raise
                await self.release_close.wait()


class _FakeStore:
    def __init__(self, path: Path) -> None:
        self.path = path / "memory.v1.json"
        self.revision = 0
        self.applied: list[dict[str, object]] = []
        self.source_refs: list[str] = []
        self.bump_revision_on_snapshot = False

    def snapshot(self) -> MemorySnapshot:
        revision = self.revision
        if self.bump_revision_on_snapshot:
            self.revision += 1
            self.bump_revision_on_snapshot = False
        return MemorySnapshot(revision, ())

    def apply_capture(
        self,
        mutations: list[CaptureMutation],
        *,
        expected_revision: int,
    ) -> MemorySnapshot:
        assert expected_revision == self.revision
        for mutation in mutations:
            self.applied.append(
                {
                    "text": mutation.text,
                    "subject_id": mutation.subject_id,
                    "source_person_id": mutation.source_person_id,
                    "kind": mutation.kind,
                    "certainty": mutation.certainty,
                    "event_start": mutation.event_start,
                    "event_end": mutation.event_end,
                }
            )
            self.source_refs.append(str(mutation.source_ref))
        self.revision += 1
        return MemorySnapshot(self.revision, ())


def _clocks() -> CaptureClocks:
    return CaptureClocks(
        now=lambda: datetime(2026, 9, 11, 12, tzinfo=timezone.utc),
        monotonic=lambda: 100.0,
    )


def _capture(
    tmp_path: Path,
    client: _FakeClient,
    *,
    store: _FakeStore | None = None,
) -> tuple[MemoryCapture, _FakeStore]:
    memory_store = store or _FakeStore(tmp_path)
    return MemoryCapture(memory_store, "gpt-5.6-luna", client=client, clocks=_clocks()), memory_store


async def _allow_worker() -> None:
    await asyncio.sleep(0)
    await asyncio.sleep(0)


def _proposal_for_request(request: dict[str, object], *, quote: str | None = None) -> str:
    payload = json.loads(str(request["input"]))
    fragment = payload["fragments"][0]
    exact_quote = str(fragment["text"]) if quote is None else quote
    return json.dumps(
        {
            "proposals": [
                {
                    "text": "Eduardo likes jazz",
                    "subject_id": "eduardo",
                    "source_person_id": "model-guessed-speaker",
                    "kind": "fact",
                    "certainty": "stated",
                    "evidence": [{"fragment_id": fragment["id"], "quote": exact_quote}],
                }
            ]
        }
    )


@pytest.mark.asyncio
async def test_capture_is_active_only_and_uses_tool_free_bounded_luna_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Only active complete user text can trigger the tool-free request."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    monkeypatch.setattr(capture_module, "REQUEST_INTERVAL_S", 0.0)
    caplog.set_level(logging.INFO, logger=capture_module.__name__)
    client = _FakeClient(_proposal_for_request)
    capture, store = _capture(tmp_path, client)

    capture.add_fragment("user", "Eduardo likes jazz.", 0, 100)
    await _allow_worker()
    assert client.responses.calls == []

    await capture.start()
    capture.add_fragment("user", "Eduardo likes jazz.", 0, 100)
    await asyncio.wait_for(client.responses.started.wait(), timeout=1.0)
    await asyncio.sleep(0.01)

    request = client.responses.calls[0]
    assert request["model"] == "gpt-5.6-luna"
    assert request["tools"] == []
    assert request["reasoning"] == {"effort": "low"}
    assert request["store"] is False
    assert request["max_output_tokens"] == 1_000
    assert request["timeout"] == 20.0
    assert len(str(request["input"])) <= 12_000
    assert store.applied[0]["text"] == "Eduardo likes jazz"
    assert store.applied[0]["source_person_id"] is None
    assert store.source_refs[0].startswith("capture_")
    assert "extraction proposed=1 validated=1" in caplog.text
    assert "Household memory capture applied=1" in caplog.text
    assert "Eduardo likes jazz" not in caplog.text
    assert store.source_refs[0] not in caplog.text
    await capture.close()
    assert client.close_count == 1


@pytest.mark.asyncio
async def test_capture_applies_mutations_through_real_household_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid proposal uses the store's revisioned mutation API."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    monkeypatch.setattr(capture_module, "REQUEST_INTERVAL_S", 0.0)
    client = _FakeClient(_proposal_for_request)
    store = HouseholdMemoryStore(tmp_path, clock=_clocks().now)
    capture = MemoryCapture(store, "gpt-5.6-luna", client=client, clocks=_clocks())
    await capture.start()
    capture.add_fragment("user", "Eduardo likes jazz.", 0, 100)
    await asyncio.wait_for(client.responses.started.wait(), timeout=1.0)
    await asyncio.sleep(0.01)

    entries = store.list_entries(include_inactive=True)
    assert len(entries) == 1
    assert entries[0].text == "Eduardo likes jazz"
    assert entries[0].source_ref is not None
    await capture.close()


@pytest.mark.asyncio
async def test_capture_corrects_and_resolves_existing_memory_by_stable_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Grounded corrections preserve IDs and can resolve a current entry."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    monkeypatch.setattr(capture_module, "REQUEST_INTERVAL_S", 0.0)
    clocks = _clocks()
    store = HouseholdMemoryStore(tmp_path, clock=clocks.now)
    original = store.remember("Eduardo likes jazz", subject_id="eduardo", source_person_id="eduardo", kind="fact")

    def update_output(request: dict[str, object]) -> str:
        payload = json.loads(str(request["input"]))
        memory = payload["memory"][0]
        fragment = payload["fragments"][-1]
        proposal: dict[str, object] = {
            "id": memory["id"],
            "subject_id": "eduardo",
            "evidence": [{"fragment_id": fragment["id"], "quote": fragment["text"]}],
        }
        if "no longer" in str(fragment["text"]):
            proposal["status"] = "resolved"
        else:
            proposal["text"] = "Eduardo likes rock"
        return json.dumps({"proposals": [proposal]})

    update_client = _FakeClient(update_output)
    update_capture = MemoryCapture(store, "gpt-5.6-luna", client=update_client, clocks=clocks)
    await update_capture.start()
    update_capture.add_fragment("user", "Eduardo likes rock.", 0, 100)
    await asyncio.wait_for(update_client.responses.started.wait(), timeout=1.0)
    await asyncio.sleep(0.01)
    corrected = store.list_entries(include_inactive=True)
    assert corrected[0].id == original.id
    assert corrected[0].text == "Eduardo likes rock"
    assert corrected[0].source_person_id is None
    await update_capture.close()

    resolve_client = _FakeClient(update_output)
    resolve_capture = MemoryCapture(store, "gpt-5.6-luna", client=resolve_client, clocks=clocks)
    await resolve_capture.start()
    resolve_capture.add_fragment("user", "Eduardo no longer likes rock.", 0, 100)
    await asyncio.wait_for(resolve_client.responses.started.wait(), timeout=1.0)
    await asyncio.sleep(0.01)
    resolved = store.list_entries(include_inactive=True)
    assert resolved[0].id == original.id
    assert resolved[0].status == "resolved"
    assert store.list_entries() == []
    await resolve_capture.close()


@pytest.mark.asyncio
async def test_capture_preserves_unrelated_resolution_and_new_fact_for_same_subject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resolution and an unrelated fact for one person remain separate mutations."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    monkeypatch.setattr(capture_module, "REQUEST_INTERVAL_S", 0.0)
    store = HouseholdMemoryStore(tmp_path, clock=_clocks().now)
    original = store.remember("Clara needs a luggage reminder", subject_id="clara", kind="note")

    def conflicting_output(request: dict[str, object]) -> str:
        payload = json.loads(str(request["input"]))
        memory = payload["memory"][0]
        fragment = payload["fragments"][0]
        first_quote = "Clara no longer needs a luggage reminder."
        second_quote = "Clara's birthday is in June."
        assert first_quote in fragment["text"]
        assert second_quote in fragment["text"]
        return json.dumps(
            {
                "proposals": [
                    {
                        "id": memory["id"],
                        "subject_id": "clara",
                        "status": "resolved",
                        "evidence": [{"fragment_id": fragment["id"], "quote": first_quote}],
                    },
                    {
                        "subject_id": "clara",
                        "text": "Clara's birthday is in June",
                        "kind": "note",
                        "certainty": "stated",
                        "evidence": [{"fragment_id": fragment["id"], "quote": second_quote}],
                    },
                ]
            }
        )

    client = _FakeClient(conflicting_output)
    capture = MemoryCapture(store, "gpt-5.6-luna", client=client, clocks=_clocks())
    await capture.start()
    capture.add_fragment("user", "Clara no longer needs a luggage reminder. Clara's birthday is in June.", 0, 100)
    await asyncio.wait_for(client.responses.started.wait(), timeout=1.0)
    await asyncio.sleep(0.01)

    entries = store.list_entries(include_inactive=True)
    assert len(entries) == 2
    resolved = next(entry for entry in entries if entry.id == original.id)
    birthday = next(entry for entry in entries if entry.id != original.id)
    assert resolved.status == "resolved"
    assert birthday.text == "Clara's birthday is in June"
    assert birthday.status == "current"
    await capture.close()


@pytest.mark.parametrize("proposal_id,subject_id", [("m_unknown", "eduardo"), ("", "clara")])
@pytest.mark.asyncio
async def test_capture_rejects_unknown_or_wrong_subject_corrections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    proposal_id: str,
    subject_id: str,
) -> None:
    """Corrections must name an existing current memory and keep its subject."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    store = HouseholdMemoryStore(tmp_path, clock=_clocks().now)
    original = store.remember("Eduardo likes jazz", subject_id="eduardo", kind="fact")

    def invalid_correction(request: dict[str, object]) -> str:
        payload = json.loads(str(request["input"]))
        fragment = payload["fragments"][-1]
        return json.dumps(
            {
                "proposals": [
                    {
                        "id": proposal_id or original.id,
                        "subject_id": subject_id,
                        "text": "Clara likes rock",
                        "evidence": [{"fragment_id": fragment["id"], "quote": fragment["text"]}],
                    }
                ]
            }
        )

    client = _FakeClient(invalid_correction)
    capture = MemoryCapture(store, "gpt-5.6-luna", client=client, clocks=_clocks())
    await capture.start()
    capture.add_fragment("user", "Clara likes rock.", 0, 100)
    await asyncio.wait_for(client.responses.started.wait(), timeout=1.0)
    await asyncio.sleep(0.01)
    entries = store.list_entries(include_inactive=True)
    assert len(entries) == 1
    assert entries[0].id == original.id
    assert entries[0].text == "Eduardo likes jazz"
    await capture.close()


@pytest.mark.asyncio
async def test_punctuationless_complete_claim_reaches_extractor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A complete claim can reach extraction even when Live omits final punctuation."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    monkeypatch.setattr(capture_module, "REQUEST_INTERVAL_S", 0.0)
    client = _FakeClient(_proposal_for_request)
    capture, store = _capture(tmp_path, client)
    await capture.start()

    capture.add_fragment("user", "Eduardo likes jazz", 0, 100)
    await asyncio.wait_for(client.responses.started.wait(), timeout=1.0)
    for _ in range(100):
        if store.applied:
            break
        await asyncio.sleep(0.01)
    assert len(store.applied) == 1
    assert capture.stats.incomplete_batches == 0
    await capture.close()


@pytest.mark.asyncio
async def test_unfinished_punctuationless_claim_is_skipped_by_extractor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Extraction can decline an unfinished claim without mutating local memory."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    monkeypatch.setattr(capture_module, "REQUEST_INTERVAL_S", 0.0)

    def no_proposal(_request: dict[str, object]) -> str:
        return json.dumps({"proposals": []})

    client = _FakeClient(no_proposal)
    capture, store = _capture(tmp_path, client)
    await capture.start()
    capture.add_fragment("user", "Eduardo likes", 0, 100)
    await asyncio.wait_for(client.responses.started.wait(), timeout=1.0)
    await asyncio.sleep(0.02)

    assert len(client.responses.calls) == 1
    assert store.applied == []
    assert capture.stats.incomplete_batches == 0
    await capture.close()


@pytest.mark.asyncio
async def test_whitespace_only_delta_does_not_trigger_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whitespace is retained for nearby evidence but cannot start a request alone."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    client = _FakeClient(_proposal_for_request)
    capture, _ = _capture(tmp_path, client)
    await capture.start()
    capture.add_fragment("user", "   ", 0, 100)
    await asyncio.sleep(0.02)

    assert client.responses.calls == []
    await capture.close()


@pytest.mark.asyncio
async def test_noncompleted_extraction_counts_as_incomplete_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A noncompleted provider response is fenced and counted without a write."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    client = _FakeClient(_proposal_for_request)
    client.responses.response_status = "incomplete"
    capture, store = _capture(tmp_path, client)
    await capture.start()
    capture.add_fragment("user", "Eduardo likes jazz", 0, 100)
    await asyncio.wait_for(client.responses.started.wait(), timeout=1.0)
    await asyncio.sleep(0.02)

    assert store.applied == []
    assert capture.stats.incomplete_batches == 1
    assert capture.stats.requests_failed == 1
    await capture.close()


@pytest.mark.asyncio
async def test_punctuationless_old_request_is_cancelled_by_later_user_delta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A later user delta fences the old response and retries with retained context."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    monkeypatch.setattr(capture_module, "REQUEST_INTERVAL_S", 0.0)

    def latest_user_proposal(request: dict[str, object]) -> str:
        payload = json.loads(str(request["input"]))
        user_fragments = [item for item in payload["fragments"] if item["role"] == "user"]
        joined_text = "".join(str(item["text"]) for item in user_fragments).strip().removesuffix(".")
        return json.dumps(
            {
                "proposals": [
                    {
                        "text": joined_text,
                        "subject_id": "eduardo",
                        "kind": "fact",
                        "certainty": "stated",
                        "evidence": [{"fragment_id": item["id"], "quote": item["text"]} for item in user_fragments],
                    }
                ]
            }
        )

    client = _FakeClient(latest_user_proposal)
    client.responses.block = True
    client.responses.ignore_cancellation = True
    capture, store = _capture(tmp_path, client)
    await capture.start()
    capture.add_fragment("user", "Eduardo likes", 0, 100)
    await asyncio.wait_for(client.responses.started.wait(), timeout=1.0)

    capture.add_fragment("user", " tea.", 100, 200)
    await asyncio.sleep(0.01)
    assert client.responses.cancelled.is_set()
    client.responses.release.set()
    for _ in range(100):
        if store.applied:
            break
        await asyncio.sleep(0.01)
    assert len(store.applied) == 1
    assert store.applied[0]["text"] == "Eduardo likes tea"
    assert len(client.responses.calls) == 2
    await capture.close()


@pytest.mark.asyncio
async def test_cancelled_prefix_keeps_context_for_slow_streamed_user_sentence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A streamed sentence retries with both deltas after its prefix request is fenced."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.01)
    monkeypatch.setattr(capture_module, "REQUEST_INTERVAL_S", 0.01)

    def streamed_output(request: dict[str, object]) -> str:
        payload = json.loads(str(request["input"]))
        user_fragments = [item for item in payload["fragments"] if item["role"] == "user"]
        if len(user_fragments) < 2:
            return json.dumps({"proposals": []})
        return json.dumps(
            {
                "proposals": [
                    {
                        "text": "Clara prefers pistachio ice cream",
                        "subject_id": "clara",
                        "kind": "fact",
                        "certainty": "stated",
                        "evidence": [{"fragment_id": item["id"], "quote": item["text"]} for item in user_fragments],
                    }
                ]
            }
        )

    client = _FakeClient(streamed_output)
    client.responses.block = True
    client.responses.ignore_cancellation = True
    capture, store = _capture(tmp_path, client)
    await capture.start()
    capture.add_fragment("user", "mi nombre es Clara", 0, 100)
    await asyncio.wait_for(client.responses.started.wait(), timeout=1.0)

    capture.add_fragment("user", " y mi helado favorito es el pistacho", 100, 200)
    await asyncio.wait_for(client.responses.cancelled.wait(), timeout=1.0)
    client.responses.block = False
    client.responses.release.set()
    for _ in range(200):
        if store.applied:
            break
        await asyncio.sleep(0.01)

    assert len(store.applied) == 1
    assert len(client.responses.calls) == 2
    second_payload = json.loads(str(client.responses.calls[1]["input"]))
    assert [item["text"] for item in second_payload["fragments"] if item["role"] == "user"] == [
        "mi nombre es Clara",
        " y mi helado favorito es el pistacho",
    ]
    await capture.close()


@pytest.mark.asyncio
async def test_no_proposal_prefix_is_retained_until_new_user_continuation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A completed empty prefix response waits for new user evidence before retrying."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    monkeypatch.setattr(capture_module, "REQUEST_INTERVAL_S", 0.0)

    def continuation_output(request: dict[str, object]) -> str:
        payload = json.loads(str(request["input"]))
        user_fragments = [item for item in payload["fragments"] if item["role"] == "user"]
        if len(user_fragments) < 2:
            return json.dumps({"proposals": []})
        return json.dumps(
            {
                "proposals": [
                    {
                        "text": "Clara prefers pistachio ice cream",
                        "subject_id": "clara",
                        "kind": "fact",
                        "certainty": "stated",
                        "evidence": [{"fragment_id": user_fragments[-1]["id"], "quote": user_fragments[-1]["text"]}],
                    }
                ]
            }
        )

    client = _FakeClient(continuation_output)
    capture, store = _capture(tmp_path, client)
    await capture.start()
    capture.add_fragment("user", "mi nombre es Clara", 0, 100)
    await asyncio.wait_for(client.responses.started.wait(), timeout=1.0)
    await asyncio.sleep(0.02)
    assert store.applied == []
    assert len(client.responses.calls) == 1

    client.responses.started.clear()
    capture.add_fragment("assistant", "Entiendo.", 100, 200)
    await asyncio.sleep(0.02)
    assert len(client.responses.calls) == 1

    capture.add_fragment("user", "Clara's favorite ice cream is pistachio", 200, 300)
    for _ in range(100):
        if store.applied:
            break
        await asyncio.sleep(0.01)
    assert len(store.applied) == 1
    assert len(client.responses.calls) == 2
    second_payload = json.loads(str(client.responses.calls[1]["input"]))
    assert [item["text"] for item in second_payload["fragments"] if item["role"] == "user"] == [
        "mi nombre es Clara",
        "Clara's favorite ice cream is pistachio",
    ]
    await capture.close()


@pytest.mark.asyncio
async def test_successful_prefix_is_retained_for_same_sentence_continuation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A saved prefix remains available when the same user sentence continues."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    monkeypatch.setattr(capture_module, "REQUEST_INTERVAL_S", 0.0)

    def prefix_then_preference(request: dict[str, object]) -> str:
        payload = json.loads(str(request["input"]))
        user_fragments = [item for item in payload["fragments"] if item["role"] == "user"]
        if len(user_fragments) == 1:
            return json.dumps(
                {
                    "proposals": [
                        {
                            "text": "Clara is named Clara",
                            "subject_id": "clara",
                            "kind": "note",
                            "certainty": "stated",
                            "evidence": [{"fragment_id": user_fragments[0]["id"], "quote": user_fragments[0]["text"]}],
                        }
                    ]
                }
            )
        return json.dumps(
            {
                "proposals": [
                    {
                        "text": "Clara prefers pistachio ice cream",
                        "subject_id": "clara",
                        "kind": "fact",
                        "certainty": "stated",
                        "evidence": [{"fragment_id": item["id"], "quote": item["text"]} for item in user_fragments],
                    }
                ]
            }
        )

    client = _FakeClient(prefix_then_preference)
    capture, store = _capture(tmp_path, client)
    await capture.start()
    capture.add_fragment("user", "mi nombre es Clara", 0, 100)
    for _ in range(100):
        if len(store.applied) == 1:
            break
        await asyncio.sleep(0.01)
    assert len(store.applied) == 1

    capture.add_fragment("user", " y mi helado favorito es el pistacho", 100, 200)
    for _ in range(100):
        if len(store.applied) == 2:
            break
        await asyncio.sleep(0.01)
    assert len(store.applied) == 2
    assert store.applied[1]["text"] == "Clara prefers pistachio ice cream"
    second_payload = json.loads(str(client.responses.calls[1]["input"]))
    assert [item["text"] for item in second_payload["fragments"] if item["role"] == "user"] == [
        "mi nombre es Clara",
        " y mi helado favorito es el pistacho",
    ]
    await capture.close()


@pytest.mark.asyncio
async def test_assistant_context_and_first_person_unknown_subject_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Assistant text and an unassigned first-person statement cannot become evidence."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)

    def assistant_evidence(request: dict[str, object]) -> str:
        payload = json.loads(str(request["input"]))
        assistant = next(item for item in payload["fragments"] if item["role"] == "assistant")
        return json.dumps(
            {
                "proposals": [
                    {
                        "text": "Eduardo likes jazz",
                        "subject_id": "eduardo",
                        "kind": "fact",
                        "certainty": "stated",
                        "evidence": [{"fragment_id": assistant["id"], "quote": assistant["text"]}],
                    }
                ]
            }
        )

    client = _FakeClient(assistant_evidence)
    capture, store = _capture(tmp_path, client)
    await capture.start()
    capture.add_fragment("assistant", "Eduardo likes jazz.", 0, 100)
    capture.add_fragment("user", "I like jazz.", 100, 200)
    await asyncio.wait_for(client.responses.started.wait(), timeout=1.0)
    await asyncio.sleep(0.01)
    assert store.applied == []
    await capture.close()


@pytest.mark.asyncio
async def test_same_evidence_self_identification_is_allowed_but_cross_fragment_is_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A first-person note needs its name in the same quoted user evidence."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)

    def self_identifying_output(request: dict[str, object]) -> str:
        payload = json.loads(str(request["input"]))
        fragment = payload["fragments"][-1]
        return json.dumps(
            {
                "proposals": [
                    {
                        "text": "Clara likes jazz",
                        "subject_id": "clara",
                        "kind": "fact",
                        "certainty": "stated",
                        "evidence": [{"fragment_id": fragment["id"], "quote": fragment["text"]}],
                    }
                ]
            }
        )

    client = _FakeClient(self_identifying_output)
    capture, store = _capture(tmp_path, client)
    await capture.start()
    capture.add_fragment("user", "I am Clara and I like jazz.", 0, 100)
    await asyncio.wait_for(client.responses.started.wait(), timeout=1.0)
    await asyncio.sleep(0.01)
    assert len(store.applied) == 1
    await capture.close()

    def cross_fragment_output(request: dict[str, object]) -> str:
        payload = json.loads(str(request["input"]))
        fragments = payload["fragments"]
        return json.dumps(
            {
                "proposals": [
                    {
                        "text": "Clara likes tea",
                        "subject_id": "clara",
                        "kind": "note",
                        "certainty": "stated",
                        "evidence": [
                            {"fragment_id": fragments[0]["id"], "quote": fragments[0]["text"]},
                            {"fragment_id": fragments[1]["id"], "quote": fragments[1]["text"]},
                        ],
                    }
                ]
            }
        )

    second_client = _FakeClient(cross_fragment_output)
    second_capture, second_store = _capture(tmp_path, second_client)
    await second_capture.start()
    second_capture.add_fragment("user", "I am Clara.", 0, 100)
    second_capture.add_fragment("user", "I like tea.", 100, 200)
    await asyncio.wait_for(second_client.responses.started.wait(), timeout=1.0)
    await asyncio.sleep(0.01)
    assert second_store.applied == []
    await second_capture.close()


@pytest.mark.parametrize(
    ("same_evidence", "later_first_person"),
    [
        ("Soy Clara y me gusta el jazz.", "Me gusta el té."),
        ("Eu sou Clara e gosto de jazz.", "Gosto de chá."),
        ("Sono Clara e mi piace il jazz.", "Mi piace il tè."),
    ],
)
@pytest.mark.asyncio
async def test_multilingual_first_person_requires_same_evidence_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    same_evidence: str,
    later_first_person: str,
) -> None:
    """The lexical guard covers the household's common conversation languages."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    monkeypatch.setattr(capture_module, "REQUEST_INTERVAL_S", 0.0)

    def output(request: dict[str, object]) -> str:
        payload = json.loads(str(request["input"]))
        fragments = payload["fragments"]
        return json.dumps(
            {
                "proposals": [
                    {
                        "text": "Clara likes jazz",
                        "subject_id": "clara",
                        "kind": "note",
                        "certainty": "stated",
                        "evidence": [{"fragment_id": item["id"], "quote": item["text"]} for item in fragments],
                    }
                ]
            }
        )

    accepted_client = _FakeClient(output)
    accepted_capture, accepted_store = _capture(tmp_path, accepted_client)
    await accepted_capture.start()
    accepted_capture.add_fragment("user", same_evidence, 0, 100)
    await asyncio.wait_for(accepted_client.responses.started.wait(), timeout=1.0)
    await asyncio.sleep(0.01)
    assert len(accepted_store.applied) == 1
    await accepted_capture.close()

    rejected_client = _FakeClient(output)
    rejected_capture, rejected_store = _capture(tmp_path, rejected_client)
    await rejected_capture.start()
    rejected_capture.add_fragment("user", same_evidence.split(".")[0] + ".", 0, 100)
    rejected_capture.add_fragment("user", later_first_person, 100, 200)
    await asyncio.wait_for(rejected_client.responses.started.wait(), timeout=1.0)
    await asyncio.sleep(0.01)
    assert rejected_store.applied == []
    await rejected_capture.close()


@pytest.mark.parametrize(
    ("first_delta", "second_delta"),
    [
        ("mi nombre es Clara", " y mi helado favorito es el pistacho."),
        ("mi nombre es Cla", "ra y mi helado favorito es el pistacho."),
    ],
)
@pytest.mark.asyncio
async def test_split_same_sentence_self_identification_is_grounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    first_delta: str,
    second_delta: str,
) -> None:
    """Adjacent deltas can ground one sentence, including a name split at a token boundary."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    monkeypatch.setattr(capture_module, "REQUEST_INTERVAL_S", 0.0)

    def split_output(request: dict[str, object]) -> str:
        payload = json.loads(str(request["input"]))
        user_fragments = [item for item in payload["fragments"] if item["role"] == "user"]
        return json.dumps(
            {
                "proposals": [
                    {
                        "text": "Clara prefers pistachio ice cream",
                        "subject_id": "clara",
                        "kind": "fact",
                        "certainty": "stated",
                        "evidence": [{"fragment_id": item["id"], "quote": item["text"]} for item in user_fragments],
                    }
                ]
            }
        )

    client = _FakeClient(split_output)
    capture, store = _capture(tmp_path, client)
    await capture.start()
    capture.add_fragment("user", first_delta, 0, 100)
    capture.add_fragment("user", second_delta, 100, 200)
    await asyncio.wait_for(client.responses.started.wait(), timeout=1.0)
    for _ in range(100):
        if store.applied:
            break
        await asyncio.sleep(0.01)

    assert len(store.applied) == 1
    request_payload = json.loads(str(client.responses.calls[0]["input"]))
    assert [item["text"] for item in request_payload["fragments"]] == [first_delta, second_delta]
    await capture.close()


@pytest.mark.asyncio
async def test_split_sentence_keeps_whitespace_delta_exactly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Whitespace-only deltas keep the exact sentence boundary used for grounding."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    monkeypatch.setattr(capture_module, "REQUEST_INTERVAL_S", 0.0)

    def split_output(request: dict[str, object]) -> str:
        payload = json.loads(str(request["input"]))
        user_fragments = [item for item in payload["fragments"] if item["role"] == "user"]
        return json.dumps(
            {
                "proposals": [
                    {
                        "text": "Clara prefers pistachio ice cream",
                        "subject_id": "clara",
                        "kind": "fact",
                        "certainty": "stated",
                        "evidence": [{"fragment_id": item["id"], "quote": item["text"]} for item in user_fragments],
                    }
                ]
            }
        )

    client = _FakeClient(split_output)
    capture, store = _capture(tmp_path, client)
    await capture.start()
    fragments = ("mi nombre es Clara", " ", " y mi helado favorito es el pistacho.", " ")
    for index, text in enumerate(fragments):
        capture.add_fragment("user", text, index * 100, (index + 1) * 100)
    await asyncio.wait_for(client.responses.started.wait(), timeout=1.0)
    for _ in range(100):
        if store.applied:
            break
        await asyncio.sleep(0.01)

    assert len(store.applied) == 1
    request_payload = json.loads(str(client.responses.calls[0]["input"]))
    assert [item["text"] for item in request_payload["fragments"]] == list(fragments)
    await capture.close()


@pytest.mark.asyncio
async def test_split_grounding_accepts_overlapping_timestamps_and_trimmed_quotes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Grounding uses full raw fragments when Live timestamps overlap and quotes are excerpts."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    monkeypatch.setattr(capture_module, "REQUEST_INTERVAL_S", 0.0)

    def excerpt_output(request: dict[str, object]) -> str:
        payload = json.loads(str(request["input"]))
        fragments = payload["fragments"]
        return json.dumps(
            {
                "proposals": [
                    {
                        "text": "Clara prefers pistachio ice cream",
                        "subject_id": "clara",
                        "kind": "fact",
                        "certainty": "stated",
                        "evidence": [
                            {"fragment_id": fragments[0]["id"], "quote": "nombre es Clara"},
                            {"fragment_id": fragments[1]["id"], "quote": "helado favorito"},
                        ],
                    }
                ]
            }
        )

    client = _FakeClient(excerpt_output)
    capture, store = _capture(tmp_path, client)
    await capture.start()
    capture.add_fragment("user", "Mi nombre es Clara", 0, 100)
    capture.add_fragment("user", " y mi helado favorito es el pistacho.", 90, 200)
    await asyncio.wait_for(client.responses.started.wait(), timeout=1.0)
    for _ in range(100):
        if store.applied:
            break
        await asyncio.sleep(0.01)

    assert len(store.applied) == 1
    await capture.close()


@pytest.mark.asyncio
async def test_split_grounding_rejects_excerpt_that_manufactures_self_identification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Separate excerpts cannot manufacture an identity absent from their full raw context."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    monkeypatch.setattr(capture_module, "REQUEST_INTERVAL_S", 0.0)

    def manufactured_identity_output(request: dict[str, object]) -> str:
        payload = json.loads(str(request["input"]))
        fragments = payload["fragments"]
        return json.dumps(
            {
                "proposals": [
                    {
                        "text": "Clara prefers pistachio ice cream",
                        "subject_id": "clara",
                        "kind": "fact",
                        "certainty": "stated",
                        "evidence": [
                            {"fragment_id": fragments[0]["id"], "quote": "I am"},
                            {"fragment_id": fragments[1]["id"], "quote": "Clara"},
                        ],
                    }
                ]
            }
        )

    client = _FakeClient(manufactured_identity_output)
    capture, store = _capture(tmp_path, client)
    await capture.start()
    capture.add_fragment("user", "I am not ", 0, 100)
    capture.add_fragment("user", "Clara, actually.", 100, 200)
    await asyncio.wait_for(client.responses.started.wait(), timeout=1.0)
    await asyncio.sleep(0.02)

    assert store.applied == []
    await capture.close()


@pytest.mark.parametrize(
    ("segments", "evidence_indexes", "start_times"),
    [
        (
            (("user", "Mi nombre es Clara."), ("user", " y mi helado favorito es el pistacho.")),
            (0, 1),
            (0, 100),
        ),
        (
            (
                ("user", "Mi nombre es Clara"),
                ("assistant", "Entiendo."),
                ("user", " y mi helado favorito es el pistacho."),
            ),
            (0, 2),
            (0, 100, 200),
        ),
        (
            (
                ("user", "Mi nombre es Clara"),
                ("user", " hablamos de otra cosa"),
                ("user", " y mi helado favorito es el pistacho."),
            ),
            (0, 2),
            (0, 100, 200),
        ),
        (
            (("user", "Mi nombre es Clara"), ("user", " y mi helado favorito es el pistacho.")),
            (0, 1),
            (0, 4_001),
        ),
        (
            (("user", "Mi nombre es Clara"), ("user", ". Otra cosa y mi helado favorito es el pistacho.")),
            (0, 1),
            (0, 100),
        ),
        (
            (("user", "Mi nombre es Clara"), ("user", " y mi helado favorito es el pistacho.")),
            (0, 1),
            (100, 50),
        ),
    ],
)
@pytest.mark.asyncio
async def test_split_identity_does_not_cross_sentence_role_or_evidence_gap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    segments: tuple[tuple[str, str], ...],
    evidence_indexes: tuple[int, ...],
    start_times: tuple[int, ...],
) -> None:
    """Grounding never carries a speaker identity across a sentence, role, or bounded gap."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    monkeypatch.setattr(capture_module, "REQUEST_INTERVAL_S", 0.0)

    def split_output(request: dict[str, object]) -> str:
        payload = json.loads(str(request["input"]))
        fragments = payload["fragments"]
        return json.dumps(
            {
                "proposals": [
                    {
                        "text": "Clara prefers pistachio ice cream",
                        "subject_id": "clara",
                        "kind": "fact",
                        "certainty": "stated",
                        "evidence": [
                            {
                                "fragment_id": fragments[index]["id"],
                                "quote": fragments[index]["text"],
                            }
                            for index in evidence_indexes
                        ],
                    }
                ]
            }
        )

    client = _FakeClient(split_output)
    capture, store = _capture(tmp_path, client)
    await capture.start()
    for index, (role, text) in enumerate(segments):
        start_ms = start_times[index]
        capture.add_fragment(role, text, start_ms, start_ms + 50)
    await asyncio.wait_for(client.responses.started.wait(), timeout=1.0)
    await asyncio.sleep(0.02)

    assert store.applied == []
    await capture.close()


@pytest.mark.asyncio
async def test_malformed_and_unknown_proposals_are_skipped_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unknown subjects, model source references, and invalid evidence are rejected."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)

    def invalid_output(request: dict[str, object]) -> str:
        payload = json.loads(str(request["input"]))
        fragment_id = payload["fragments"][0]["id"]
        return json.dumps(
            {
                "proposals": [
                    {
                        "text": "A private detail",
                        "subject_id": "unknown",
                        "kind": "fact",
                        "certainty": "stated",
                        "source_ref": "model-chosen",
                        "evidence": [{"fragment_id": fragment_id, "quote": "wrong quote"}],
                    }
                ]
            }
        )

    client = _FakeClient(invalid_output)
    capture, store = _capture(tmp_path, client)
    await capture.start()
    capture.add_fragment("user", "Eduardo mentioned a detail.", 0, 100)
    await asyncio.wait_for(client.responses.started.wait(), timeout=1.0)
    await asyncio.sleep(0.01)
    assert store.applied == []
    await capture.close()


@pytest.mark.asyncio
async def test_stale_store_revision_discards_all_pending_fragments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A correction or deletion during extraction wins over the late response."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    client = _FakeClient(_proposal_for_request)
    store = _FakeStore(tmp_path)
    capture, _ = _capture(tmp_path, client, store=store)
    await capture.start()
    capture.add_fragment("user", "Eduardo likes jazz.", 0, 100)
    await asyncio.wait_for(client.responses.started.wait(), timeout=1.0)
    store.revision += 1
    await asyncio.sleep(0.05)
    assert store.applied == []
    assert capture.stats.stale_batches == 1
    await capture.close()


@pytest.mark.asyncio
async def test_timeout_reserves_budget_and_advances_buffer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed request is counted before transport and is not submitted again."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    monkeypatch.setattr(capture_module, "REQUEST_TIMEOUT_S", 0.01)
    client = _FakeClient(_proposal_for_request)
    client.responses.block = True
    capture, store = _capture(tmp_path, client)
    await capture.start()
    capture.add_fragment("user", "Eduardo likes jazz.", 0, 100)
    await asyncio.wait_for(client.responses.started.wait(), timeout=1.0)
    await asyncio.sleep(0.05)
    assert len(client.responses.calls) == 1
    assert store.applied == []
    budget = json.loads(capture.budget_path.read_text(encoding="utf-8"))
    assert budget["calls"] == 1
    assert budget["reserved_output_tokens"] == 1_000
    await capture.close()


@pytest.mark.asyncio
async def test_exhausted_budget_blocks_requests_and_resumes_next_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Exhausted reservations prevent transport until the local day advances."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    client = _FakeClient(_proposal_for_request)
    capture, store = _capture(tmp_path, client)
    capture.budget_path.write_text(
        json.dumps(
            {
                "version": 1,
                "local_date": "2026-09-11",
                "calls": capture_module.BUDGET_CALL_LIMIT,
                "reserved_output_tokens": capture_module.BUDGET_OUTPUT_TOKEN_LIMIT,
            }
        ),
        encoding="utf-8",
    )
    with caplog.at_level(logging.INFO):
        await capture.start()
        capture.add_fragment("user", "Eduardo likes jazz.", 0, 100)
        await asyncio.sleep(0.02)
        await capture.close()
    assert client.responses.calls == []
    assert capture.stats.budget_blocked == 1
    limit = capture_module.BUDGET_CALL_LIMIT
    assert f"reserved_calls={limit} limit={limit}" in caplog.text
    assert "resumes on the next local day" in caplog.text
    assert "Eduardo likes jazz" not in caplog.text

    next_client = _FakeClient(_proposal_for_request)
    next_capture = MemoryCapture(
        store,
        "gpt-5.6-luna",
        client=next_client,
        clocks=CaptureClocks(now=lambda: datetime(2026, 9, 12, 12, tzinfo=timezone.utc), monotonic=lambda: 100.0),
    )
    await next_capture.start()
    next_capture.add_fragment("user", "Eduardo likes jazz.", 0, 100)
    await asyncio.wait_for(next_client.responses.started.wait(), timeout=1.0)
    await asyncio.sleep(0.01)
    await next_capture.close()
    budget = json.loads(next_capture.budget_path.read_text(encoding="utf-8"))
    assert budget["calls"] == 1
    assert budget["local_date"] == "2026-09-12"


@pytest.mark.asyncio
async def test_budget_metadata_survives_restart_and_corruption_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reservations persist across workers and invalid metadata blocks cloud calls."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    monkeypatch.setattr(capture_module, "REQUEST_INTERVAL_S", 0.0)
    first_client = _FakeClient(_proposal_for_request)
    first, store = _capture(tmp_path, first_client)
    await first.start()
    first.add_fragment("user", "Eduardo likes jazz.", 0, 100)
    await asyncio.wait_for(first_client.responses.started.wait(), timeout=1.0)
    await asyncio.sleep(0.01)
    await first.close()

    second_client = _FakeClient(_proposal_for_request)
    second, _ = _capture(tmp_path, second_client, store=store)
    await second.start()
    second.add_fragment("user", "Eduardo likes tea.", 0, 100)
    await asyncio.wait_for(second_client.responses.started.wait(), timeout=1.0)
    await asyncio.sleep(0.01)
    await second.close()
    budget = json.loads(second.budget_path.read_text(encoding="utf-8"))
    assert budget["calls"] == 2

    second.budget_path.write_text("corrupt", encoding="utf-8")
    third_client = _FakeClient(_proposal_for_request)
    third, _ = _capture(tmp_path, third_client, store=store)
    await third.start()
    third.add_fragment("user", "Eduardo likes coffee.", 0, 100)
    await asyncio.sleep(0.02)
    assert third_client.responses.calls == []
    await third.close()


@pytest.mark.asyncio
async def test_budget_clock_rollback_preserves_future_reservation_and_rejects_bad_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clock rollback cannot reset a future reservation or accept a malformed date."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    monkeypatch.setattr(capture_module, "REQUEST_INTERVAL_S", 0.0)
    clock_date = [datetime(2026, 9, 12, 12, tzinfo=timezone.utc)]
    clocks = CaptureClocks(now=lambda: clock_date[0], monotonic=lambda: 100.0)
    store = _FakeStore(tmp_path)

    first_client = _FakeClient(_proposal_for_request)
    first = MemoryCapture(store, "gpt-5.6-luna", client=first_client, clocks=clocks)
    await first.start()
    first.add_fragment("user", "Eduardo likes jazz.", 0, 100)
    await asyncio.wait_for(first_client.responses.started.wait(), timeout=1.0)
    await asyncio.sleep(0.01)
    await first.close()

    clock_date[0] = datetime(2026, 9, 11, 12, tzinfo=timezone.utc)
    second_client = _FakeClient(_proposal_for_request)
    second = MemoryCapture(store, "gpt-5.6-luna", client=second_client, clocks=clocks)
    await second.start()
    second.add_fragment("user", "Eduardo likes tea.", 0, 100)
    await asyncio.wait_for(second_client.responses.started.wait(), timeout=1.0)
    await asyncio.sleep(0.01)
    await second.close()

    budget = json.loads(second.budget_path.read_text(encoding="utf-8"))
    assert budget["local_date"] == "2026-09-12"
    assert budget["calls"] == 2

    budget["local_date"] = "2026-9-12"
    second.budget_path.write_text(json.dumps(budget), encoding="utf-8")
    third_client = _FakeClient(_proposal_for_request)
    third = MemoryCapture(store, "gpt-5.6-luna", client=third_client, clocks=clocks)
    await third.start()
    third.add_fragment("user", "Eduardo likes coffee.", 0, 100)
    await asyncio.sleep(0.02)
    assert third_client.responses.calls == []
    assert third.stats.budget_blocked == 1
    await third.close()


@pytest.mark.parametrize("version", [True, 1.0])
@pytest.mark.asyncio
async def test_budget_rejects_non_integer_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: object
) -> None:
    """Boolean and floating-point versions cannot pass integer validation."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    client = _FakeClient(_proposal_for_request)
    capture, _ = _capture(tmp_path, client)
    capture.budget_path.write_text(
        json.dumps(
            {
                "version": version,
                "local_date": "2026-09-11",
                "calls": 0,
                "reserved_output_tokens": 0,
            }
        ),
        encoding="utf-8",
    )
    await capture.start()
    capture.add_fragment("user", "Eduardo likes jazz.", 0, 100)
    await asyncio.sleep(0.02)
    assert client.responses.calls == []
    assert capture.stats.budget_blocked == 1
    await capture.close()


@pytest.mark.asyncio
async def test_invalidate_and_close_prevent_late_commit_from_cancellation_resistant_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Cancellation cannot allow a late response to mutate memory."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    client = _FakeClient(_proposal_for_request)
    client.responses.block = True
    client.responses.ignore_cancellation = True
    client.responses.late_error = RuntimeError("PRIVATE_LATE_RESPONSE")
    capture, store = _capture(tmp_path, client)
    await capture.start()
    capture.add_fragment("user", "Eduardo likes jazz.", 0, 100)
    await asyncio.wait_for(client.responses.started.wait(), timeout=1.0)

    capture.invalidate()
    await asyncio.sleep(0.01)
    assert client.responses.cancelled.is_set()
    client.responses.release.set()
    await asyncio.sleep(0.02)
    assert store.applied == []
    assert any("RuntimeError" in record.message for record in caplog.records)
    assert all("PRIVATE_LATE_RESPONSE" not in record.message for record in caplog.records)

    capture.add_fragment("user", "Eduardo likes tea.", 100, 200)
    await capture.close()
    client.responses.release.set()
    await asyncio.sleep(0.02)
    assert store.applied == []
    assert client.close_count == 1


@pytest.mark.asyncio
async def test_assistant_delta_does_not_cancel_user_capture_or_make_own_write_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Assistant context can arrive while a user batch is in flight and remain harmless context."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    monkeypatch.setattr(capture_module, "REQUEST_INTERVAL_S", 0.0)

    def user_proposal(request: dict[str, object]) -> str:
        payload = json.loads(str(request["input"]))
        user_fragment = next(item for item in reversed(payload["fragments"]) if item["role"] == "user")
        user_text = str(user_fragment["text"])
        fact_text = "Eduardo likes tea" if "tea" in user_text else "Eduardo likes jazz"
        return json.dumps(
            {
                "proposals": [
                    {
                        "text": fact_text,
                        "subject_id": "eduardo",
                        "kind": "fact",
                        "certainty": "stated",
                        "evidence": [
                            {"fragment_id": user_fragment["id"], "quote": user_text},
                        ],
                    }
                ]
            }
        )

    client = _FakeClient(user_proposal)
    client.responses.block = True
    capture, store = _capture(tmp_path, client)
    await capture.start()
    capture.add_fragment("user", "Eduardo likes jazz.", 0, 100)
    await asyncio.wait_for(client.responses.started.wait(), timeout=1.0)

    capture.add_fragment("assistant", "That sounds good.", 100, 200)
    await asyncio.sleep(0.01)
    assert not client.responses.cancelled.is_set()

    client.responses.release.set()
    for _ in range(100):
        if len(store.applied) == 1:
            break
        await asyncio.sleep(0.01)
    assert len(store.applied) == 1
    assert store.applied[0]["text"] == "Eduardo likes jazz"

    capture.add_fragment("user", "Eduardo likes tea.", 200, 300)
    for _ in range(100):
        if len(store.applied) == 2:
            break
        await asyncio.sleep(0.01)
    assert len(store.applied) == 2
    assert store.applied[1]["text"] == "Eduardo likes tea"
    assert capture.stats.stale_batches == 0
    await capture.close()


@pytest.mark.asyncio
async def test_oversized_fragments_are_dropped_without_cutting_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fragment larger than the RAM bound is dropped as one unit."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    client = _FakeClient(_proposal_for_request)
    capture, _ = _capture(tmp_path, client)
    await capture.start()

    capture.add_fragment("user", "Eduardo likes jazz.", 0, 100)
    oversized = "Eduardo likes jazz " + ("very " * (capture_module.MAX_BUFFER_CHARS // 5 + 1)) + "."
    capture.add_fragment("user", oversized, 100, 200)
    await asyncio.sleep(0.02)
    assert capture.stats.fragments_received == 2
    assert capture.stats.fragments_dropped == 1
    assert client.responses.calls == []
    await capture.close()


@pytest.mark.asyncio
async def test_oversized_new_user_fragment_fences_old_inflight_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dropping a new oversized user fragment still prevents an older response from saving."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    client = _FakeClient(_proposal_for_request)
    client.responses.block = True
    client.responses.ignore_cancellation = True
    capture, store = _capture(tmp_path, client)
    await capture.start()
    capture.add_fragment("user", "Eduardo likes jazz.", 0, 100)
    await asyncio.wait_for(client.responses.started.wait(), timeout=1.0)

    oversized = "Eduardo likes tea " + ("very " * (capture_module.MAX_BUFFER_CHARS // 5 + 1)) + "."
    capture.add_fragment("user", oversized, 100, 200)
    await asyncio.sleep(0.01)
    assert client.responses.cancelled.is_set()
    assert capture.stats.fragments_dropped == 1

    client.responses.release.set()
    await asyncio.sleep(0.03)
    assert store.applied == []
    await capture.close()


@pytest.mark.asyncio
async def test_input_packing_skips_oversized_fragment_without_partial_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Request packing skips a fragment that cannot fit rather than truncating its qualifier."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    monkeypatch.setattr(capture_module, "MAX_INPUT_CHARS", 180)
    client = _FakeClient(_proposal_for_request)
    capture, _ = _capture(tmp_path, client)
    await capture.start()

    qualified_text = "Eduardo likes jazz only when the room is quiet because " + ("very " * 40) + "."
    capture.add_fragment("user", qualified_text, 0, 100)
    await asyncio.sleep(0.02)
    assert client.responses.calls == []
    assert capture.stats.fragments_dropped == 1
    await capture.close()


@pytest.mark.asyncio
async def test_no_final_cloud_flush_on_close(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Closing an active worker drops RAM-only fragments without a final request."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    client = _FakeClient(_proposal_for_request)
    capture, _ = _capture(tmp_path, client)
    await capture.start()
    capture.add_fragment("user", "Eduardo likes jazz.", 0, 100)
    await capture.close()
    assert client.responses.calls == []


@pytest.mark.asyncio
async def test_close_bounds_cancellation_resistant_client_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A client that ignores cancellation reports incomplete standby cleanup."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    client = _FakeClient(_proposal_for_request)
    client.block_close = True
    client.ignore_close_cancellation = True
    capture, _ = _capture(tmp_path, client)
    await capture.start()

    with pytest.raises(RuntimeError, match="cleanup is still active"):
        await asyncio.wait_for(capture.close(), timeout=2.0)
    assert client.close_count == 1
    client.release_close.set()
    await asyncio.sleep(0.01)
    await capture.close()
    assert client.close_count == 1


@pytest.mark.parametrize(("proposal_status", "saved"), [("current", True), ("resolved", False)])
@pytest.mark.asyncio
async def test_new_note_accepts_only_current_explicit_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    proposal_status: str,
    saved: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Explicit current status is equivalent to its default for a new note."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)

    caplog.set_level(logging.INFO, logger=capture_module.__name__)

    def proposal_with_status(request: dict[str, object]) -> str:
        response = json.loads(_proposal_for_request(request))
        response["proposals"][0]["status"] = proposal_status
        return json.dumps(response)

    client = _FakeClient(proposal_with_status)
    capture, store = _capture(tmp_path, client)
    await capture.start()
    try:
        capture.add_fragment("user", "Eduardo likes jazz", 0, 100)
        await asyncio.wait_for(client.responses.started.wait(), timeout=1.0)
        await asyncio.sleep(0.02)
        assert bool(store.applied) is saved
        assert f"rejected_fields={0 if saved else 1}" in caplog.text
        assert "rejected_evidence=0 rejected_continuity=0 rejected_attribution=0" in caplog.text
        assert "Eduardo likes jazz" not in caplog.text
    finally:
        await capture.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("matched_revision", [True, False])
async def test_review_rebase_preserves_fresh_speech_but_not_unrelated_stale_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, matched_revision: bool
) -> None:
    """Only acknowledged maintenance permits queued user text to survive a store revision."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)
    monkeypatch.setattr(capture_module, "REQUEST_INTERVAL_S", 0.0)
    client = _FakeClient(_proposal_for_request)
    capture, store = _capture(tmp_path, client)
    await capture.start()
    capture.add_fragment("user", "Eduardo likes jazz.", 0, 100)
    store.revision = 1
    capture.rebase_after_review(0 if matched_revision else 9, 1)
    await asyncio.sleep(0.05)
    if matched_revision:
        assert len(store.applied) == 1
        assert store.applied[0]["text"] == "Eduardo likes jazz"
    else:
        assert store.applied == []
        assert client.responses.calls == []
    await capture.close()


def _single_proposal(
    text: str, subject_id: str, *, fragments: slice = slice(-1, None)
) -> Callable[[dict[str, object]], str]:
    """Extractor output citing the selected user fragments verbatim."""

    def output(request: dict[str, object]) -> str:
        payload = json.loads(str(request["input"]))
        cited = [item for item in payload["fragments"] if item["role"] == "user"][fragments]
        return json.dumps(
            {
                "proposals": [
                    {
                        "text": text,
                        "subject_id": subject_id,
                        "kind": "fact",
                        "certainty": "stated",
                        "evidence": [{"fragment_id": item["id"], "quote": item["text"]} for item in cited],
                    }
                ]
            }
        )

    return output


async def _run_capture(
    tmp_path: Path, output: Callable[[dict[str, object]], str], fragments: list[tuple[str, str, str | None]]
) -> tuple[_FakeStore, list[dict[str, object]]]:
    client = _FakeClient(output)
    capture, store = _capture(tmp_path, client)
    await capture.start()
    for index, (role, text, speaker) in enumerate(fragments):
        capture.add_fragment(role, text, index * 100, index * 100 + 90, speaker_id=speaker)  # type: ignore[arg-type]
    await asyncio.wait_for(client.responses.started.wait(), timeout=1.0)
    await asyncio.sleep(0.01)
    await capture.close()
    return store, [json.loads(str(request["input"])) for request in client.responses.calls]


@pytest.mark.asyncio
async def test_recognized_speaker_owns_their_first_person_statements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With face recognition, "I like jazz" is the speaker's own fact without a self-introduction."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)

    store, requests = await _run_capture(
        tmp_path, _single_proposal("Eduardo likes jazz", "Eduardo"), [("user", "I like jazz.", "eduardo")]
    )

    assert requests[0]["fragments"][0]["speaker"] == "eduardo"
    assert [(item["subject_id"], item["source_person_id"]) for item in store.applied] == [("Eduardo", "eduardo")]


@pytest.mark.asyncio
async def test_a_recognized_speaker_cannot_lend_first_person_words_to_someone_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eduardo saying "I like tea" never becomes a fact about Clara."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)

    store, _requests = await _run_capture(
        tmp_path, _single_proposal("Clara likes tea", "clara"), [("user", "I like tea.", "eduardo")]
    )

    assert store.applied == []


@pytest.mark.asyncio
async def test_facts_about_others_record_the_recognized_speaker_as_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A named third-party fact keeps the usual checks and notes who said it."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)

    store, _requests = await _run_capture(
        tmp_path, _single_proposal("Ana loves figs", "ana"), [("user", "Ana loves figs.", "eduardo")]
    )

    assert [(item["subject_id"], item["source_person_id"]) for item in store.applied] == [("ana", "eduardo")]


@pytest.mark.asyncio
async def test_evidence_from_two_speakers_falls_back_to_the_usual_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When cited fragments come from different recognized people, no one is assumed to be the speaker."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)

    store, _requests = await _run_capture(
        tmp_path,
        _single_proposal("Eduardo likes green tea", "eduardo", fragments=slice(0, 2)),
        [("user", "I like green", "eduardo"), ("user", " tea.", "ana")],
    )

    assert store.applied == []


@pytest.mark.asyncio
async def test_speaker_is_only_accepted_on_user_fragments(tmp_path: Path) -> None:
    """Assistant text has no speaker, and an empty speaker is a caller bug."""
    capture, _store = _capture(tmp_path, _FakeClient(lambda _request: '{"proposals":[]}'))
    await capture.start()
    with pytest.raises(ValueError):
        capture.add_fragment("assistant", "Hello.", 0, 10, speaker_id="eduardo")
    with pytest.raises(ValueError):
        capture.add_fragment("user", "Hello.", 0, 10, speaker_id=" ")
    await capture.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("said", "accepted"),
    [
        ("My sister Ana loves fresh figs.", True),
        ("Mi hermana Ana adora los higos.", True),
        ("I told Ana I love figs.", False),
    ],
)
async def test_a_recognized_speaker_may_say_my_about_a_named_person(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, said: str, accepted: bool
) -> None:
    """Possessives point at the known speaker, so "my sister Ana" is about Ana; "I" statements still are not."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)

    store, _requests = await _run_capture(
        tmp_path, _single_proposal("Ana loves figs", "ana"), [("user", said, "eduardo")]
    )

    assert [(item["subject_id"], item["source_person_id"]) for item in store.applied] == (
        [("ana", "eduardo")] if accepted else []
    )


@pytest.mark.asyncio
async def test_without_a_recognized_speaker_my_still_blocks_attribution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The original conservative rule holds when nobody is recognized."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)

    store, _requests = await _run_capture(
        tmp_path, _single_proposal("Ana loves figs", "ana"), [("user", "My sister Ana loves fresh figs.", None)]
    )

    assert store.applied == []


@pytest.mark.asyncio
async def test_extractor_is_told_today_so_relative_dates_can_be_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """'Next Saturday' needs a reference date; the request carries today's local date and weekday."""
    monkeypatch.setattr(capture_module, "SETTLING_DELAY_S", 0.0)

    _store, requests = await _run_capture(
        tmp_path, lambda _request: '{"proposals":[]}', [("user", "Ana visits next Saturday.", "eduardo")]
    )

    today = requests[0]["today"]
    assert set(today) == {"date", "weekday"}
    assert date.fromisoformat(today["date"]).strftime("%A") == today["weekday"]
