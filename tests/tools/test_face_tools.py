from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from reachy_mini_conversation_app.tools import core_tools
from reachy_mini_conversation_app.config import config
from reachy_mini_conversation_app.face_identity import (
    FaceMatch,
    EnrollResult,
    FaceRegistry,
    EnrolledPerson,
)
from reachy_mini_conversation_app.tools.remember import Remember
from reachy_mini_conversation_app.household_memory import HouseholdMemoryStore
from reachy_mini_conversation_app.tools.core_tools import ToolDependencies
from reachy_mini_conversation_app.tools.forget_face import ForgetFace
from reachy_mini_conversation_app.tools.who_is_here import WhoIsHere
from reachy_mini_conversation_app.tools.remember_face import RememberFace


def _deps(service: object = None, **kwargs: object) -> ToolDependencies:
    return ToolDependencies(reachy_mini=MagicMock(), movement_manager=MagicMock(), face_recognition=service, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", [RememberFace(), ForgetFace(), WhoIsHere()])
async def test_face_tools_report_when_disabled(tool: object) -> None:
    """Without the service the tools say so instead of failing."""
    assert await tool(_deps(), name="Ana") == {"error": "face recognition is disabled"}  # type: ignore[operator]


@pytest.mark.asyncio
async def test_remember_face_turns_toward_the_speaker_and_enrolls() -> None:
    """Enrollment faces the person first and reports how many samples were saved."""
    service = MagicMock()
    service.enroll_from_camera.return_value = EnrollResult(EnrolledPerson("ana", "Ana", 5, (), "", ""))
    deps = _deps(service)

    result = await RememberFace()(deps, name="Ana")

    deps.movement_manager.turn_toward_speaker.assert_called_once()
    service.enroll_from_camera.assert_called_once_with("Ana")
    assert result == {"status": "remembered", "name": "Ana", "samples": 5}


@pytest.mark.asyncio
async def test_remember_face_relays_enrollment_problems() -> None:
    """A failed capture is explained to the model so it can guide the person."""
    service = MagicMock()
    service.enroll_from_camera.return_value = EnrollResult(None, "no clear face in view")

    assert await RememberFace()(_deps(service), name="Ana") == {"error": "no clear face in view"}
    assert await RememberFace()(_deps(service), name="  ") == {"error": "name is required"}


@pytest.mark.asyncio
async def test_forget_face_deletes_by_name(tmp_path: Path) -> None:
    """Forgetting works by spoken name, case-insensitively, and reports unknown names."""
    service = MagicMock()
    service.registry = FaceRegistry(tmp_path)
    service.registry.enroll("Ana", [(np.eye(128, dtype=np.float32)[0], None)])

    assert await ForgetFace()(_deps(service), name="ana") == {"status": "forgotten", "name": "Ana"}
    assert await ForgetFace()(_deps(service), name="ana") == {"error": "no saved face under that name"}
    assert await ForgetFace()(_deps(service), name="") == {"error": "name must not be empty"}


@pytest.mark.asyncio
async def test_who_is_here_lists_recognized_and_unknown_faces() -> None:
    """Recognized people come with a confidence; strangers are only counted."""
    service = MagicMock(ready=True)
    service.identify_now.return_value = [FaceMatch("ana", "Ana", 0.71234), None]

    result = await WhoIsHere()(_deps(service))

    assert result == {"recognized": [{"name": "Ana", "confidence": 0.71}], "unrecognized_faces": 1}
    service.identify_now.return_value = []
    assert await WhoIsHere()(_deps(service)) == {"status": "no faces in view"}
    service.ready = False
    assert "still starting" in (await WhoIsHere()(_deps(service)))["error"]


@pytest.mark.asyncio
async def test_remember_attributes_notes_to_the_recognized_person(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The face in front of the camera is the note's source unless the model names one."""
    monkeypatch.setattr(config, "HOUSEHOLD_MEMORY_ENABLED", True)
    deps = _deps(instance_path=tmp_path, current_person_id=lambda: "ana")

    await Remember()(deps, fact="Likes figs", subject_id="ana")
    await Remember()(deps, fact="Ben is visiting", subject_id="ben", source_person_id="carla")

    sources = {e.text: e.source_person_id for e in HouseholdMemoryStore(tmp_path).list_entries(include_inactive=True)}
    assert sources == {"Likes figs": "ana", "Ben is visiting": "carla"}


@pytest.mark.parametrize(("enabled", "expected"), [(True, True), (False, False)])
def test_face_tools_follow_the_feature_flag(monkeypatch: pytest.MonkeyPatch, enabled: bool, expected: bool) -> None:
    """The three face tools load only when face recognition is enabled."""
    monkeypatch.setattr(config, "FACE_RECOGNITION_ENABLED", enabled)
    monkeypatch.setattr(config, "AUTOLOAD_EXTERNAL_TOOLS", False)
    monkeypatch.setattr(core_tools, "read_profile_tool_names", lambda *_args: ["camera", "who_is_here"])
    monkeypatch.setattr(core_tools, "read_profile_tool_override", lambda *_args: None)

    names = core_tools._read_profile_tool_names(None)

    assert all((name in names) is expected for name in core_tools.FACE_TOOLS)


def test_identity_hint_leads_the_startup_context() -> None:
    """The recognized name is read when the context is sent, placed first, and skipped without context."""
    from reachy_mini_conversation_app.conversation_handler import ConversationHandler

    handler = MagicMock(spec=ConversationHandler)
    handler._initial_context = "The user has just called you."
    handler._identity_hint_provider = lambda: "The camera recognizes Ana."
    assert (
        ConversationHandler.get_initial_context(handler)
        == "The camera recognizes Ana.\n\nThe user has just called you."
    )

    handler._identity_hint_provider = lambda: None
    assert ConversationHandler.get_initial_context(handler) == "The user has just called you."

    handler._initial_context = ""
    handler._identity_hint_provider = lambda: "The camera recognizes Ana."
    assert ConversationHandler.get_initial_context(handler) == ""
