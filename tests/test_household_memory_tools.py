import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from reachy_mini_conversation_app.tools import core_tools
from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.tools.forget import Forget
from reachy_mini_conversation_app.tools.remember import Remember
from reachy_mini_conversation_app.household_memory import HouseholdMemoryStore
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.tools.update_memory import UpdateMemory


def _dependencies(instance_path: Path) -> ToolDependencies:
    return ToolDependencies(
        reachy_mini=MagicMock(),
        movement_manager=MagicMock(),
        instance_path=instance_path,
    )


@pytest.mark.asyncio
async def test_tools_keep_legacy_behavior_when_household_memory_is_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep the v1 remember and forget contract when the feature is off."""
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", False)
    deps = _dependencies(tmp_path)

    saved = await Remember()(deps, fact="Likes jazz")
    removed = await Forget()(deps, query="jazz")
    update = await UpdateMemory()(deps, memory_id="m_missing", fact="Changed")

    assert saved["saved"] == "Likes jazz"
    assert saved["memory_id"]
    assert removed["removed"] == "Likes jazz"
    assert removed["memory_id"] == saved["memory_id"]
    assert update == {"error": "household memory is disabled"}


@pytest.mark.asyncio
async def test_enabled_remember_persists_explicit_attribution_and_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pass explicit attribution and lifecycle metadata to the v2 store."""
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", True)
    deps = _dependencies(tmp_path)

    result = await Remember()(
        deps,
        fact="Will visit Porto",
        subject_id="eduardo",
        source_person_id="adriana",
        kind="plan",
        certainty="tentative",
        event_start="2026-12-24",
        event_end="2026-12-26",
        source_ref="session-1-turn-2",
    )

    assert result["saved"] == "Will visit Porto"
    assert result["memory_id"]
    entries = HouseholdMemoryStore(tmp_path).list_entries(include_inactive=True)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.subject_id == "eduardo"
    assert entry.source_person_id == "adriana"
    assert entry.kind == "plan"
    assert entry.certainty == "tentative"
    assert entry.event_start == "2026-12-24"
    assert entry.event_end == "2026-12-26"
    assert entry.source_ref == "session-1-turn-2"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        ({"subject_id": "eduardo", "event_start": "tomorrow"}, "invalid household memory input"),
        ({"subject_id": "eduardo", "kind": "guess"}, "invalid household memory input"),
        ({"subject_id": "eduardo", "certainty": "maybe"}, "invalid household memory input"),
        ({"subject_id": "eduardo", "typo": "unexpected"}, "invalid household memory input"),
        ({"event_start": "2026-09-11"}, "invalid household memory input"),
    ],
)
async def test_enabled_remember_rejects_invalid_or_unknown_inputs_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    arguments: dict[str, object],
    error: str,
) -> None:
    """Reject malformed or unknown enabled-mode arguments before writing."""
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", True)
    deps = _dependencies(tmp_path)

    result = await Remember()(deps, fact="A bounded fact", **arguments)

    assert result == {"error": error}
    assert HouseholdMemoryStore(tmp_path).list_entries(include_inactive=True) == []


@pytest.mark.asyncio
async def test_forget_refuses_ambiguous_query_and_accepts_stable_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse ambiguous text matches and remove by a stable ID."""
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", True)
    deps = _dependencies(tmp_path)
    remember = Remember()
    first = await remember(deps, fact="Likes jazz", subject_id="eduardo")
    second = await remember(deps, fact="Likes jazz piano", subject_id="adriana")

    ambiguous = await Forget()(deps, query="jazz")
    removed = await Forget()(deps, memory_id=str(first["memory_id"]))

    assert ambiguous["error"] == "memory query is ambiguous; provide the memory id"
    assert len(ambiguous["candidates"]) == 2
    assert removed["removed"] == "Likes jazz"
    assert removed["memory_id"] == first["memory_id"]
    assert [entry.id for entry in HouseholdMemoryStore(tmp_path).list_entries(include_inactive=True)] == [
        str(second["memory_id"])
    ]


@pytest.mark.asyncio
async def test_update_memory_corrects_one_entry_by_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Apply a correction to the entry selected by its stable ID."""
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", True)
    deps = _dependencies(tmp_path)
    saved = await Remember()(deps, fact="Likes jazz", subject_id="eduardo", kind="fact")

    updated = await UpdateMemory()(deps, memory_id=str(saved["memory_id"]), fact="Prefers classical")

    assert updated == {"updated": "Prefers classical", "memory_id": saved["memory_id"]}
    entries = HouseholdMemoryStore(tmp_path).list_entries(include_inactive=True)
    assert [entry.text for entry in entries] == ["Prefers classical"]


@pytest.mark.asyncio
async def test_memory_tools_do_not_log_private_content_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Keep private memory text out of tool logs in enabled mode."""
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", True)
    private_fact = "household-private-memory"
    deps = _dependencies(tmp_path)

    with caplog.at_level(logging.INFO, logger="reachy_mini_conversation_app.tools"):
        await Remember()(deps, fact=private_fact, subject_id="eduardo")

    assert private_fact not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_memory_store_failures_are_graceful(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    """Return bounded errors when the local store is corrupt in either mode."""
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", enabled)
    (tmp_path / "memory.v1.json").write_text("{not-json", encoding="utf-8")
    deps = _dependencies(tmp_path)

    remember = await Remember()(deps, fact="A fact", subject_id="eduardo")
    forget = await Forget()(deps, query="fact")
    update = await UpdateMemory()(deps, memory_id="m_missing", fact="Changed")

    assert remember["error"] == "household memory is unavailable"
    assert forget["error"] == "household memory is unavailable"
    assert update["error"] == ("household memory is unavailable" if enabled else "household memory is disabled")


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_failed_memory_write_preserves_existing_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    """Filesystem write failures return an error without replacing existing memories."""
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", enabled)
    deps = _dependencies(tmp_path)
    await Remember()(deps, fact="Original synthetic fact", subject_id="household")
    path = tmp_path / "memory.v1.json"
    before = path.read_bytes()
    monkeypatch.setattr(Path, "replace", MagicMock(side_effect=PermissionError("synthetic write failure")))
    response = await Remember()(deps, fact="New synthetic fact", subject_id="household")
    assert "error" in response
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_manager_dispatch_metadata_is_not_treated_as_memory_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Allow the dispatcher’s internal manager argument without exposing it in schemas."""
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", True)
    monkeypatch.setattr(
        core_tools,
        "get_tools",
        lambda: {"remember": Remember(), "update_memory": UpdateMemory(), "forget": Forget()},
    )
    deps = _dependencies(tmp_path)
    manager = MagicMock()

    saved = await core_tools.dispatch_tool_call_with_manager(
        "remember",
        '{"fact":"Likes jazz","subject_id":"eduardo"}',
        deps,
        manager,
    )
    updated = await core_tools.dispatch_tool_call_with_manager(
        "update_memory",
        f'{{"memory_id":"{saved["memory_id"]}","fact":"Prefers classical"}}',
        deps,
        manager,
    )
    forgotten = await core_tools.dispatch_tool_call_with_manager(
        "forget",
        f'{{"memory_id":"{saved["memory_id"]}"}}',
        deps,
        manager,
    )

    assert updated["updated"] == "Prefers classical"
    assert forgotten["removed"] == "Prefers classical"


@pytest.mark.parametrize("capture_enabled", [True, False])
def test_remember_defers_to_background_capture_when_it_is_on(
    monkeypatch: pytest.MonkeyPatch, capture_enabled: bool
) -> None:
    """With capture on, the model saves only explicit requests, so one fact is not stored twice."""
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", True)
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_CAPTURE_ENABLED", capture_enabled)

    description = Remember().description

    assert ("only when someone explicitly asks you to remember" in description) is capture_enabled
    assert "explicit subject_id" in description
