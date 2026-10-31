"""Pytest configuration for path setup."""

import os
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1].resolve()
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


# Make tests reproducible by ignoring machine-specific profile/tool env config.
# Without this, importing config during test collection can pick up a developer's
# local .env and fail before tests run.
os.environ["REACHY_MINI_SKIP_DOTENV"] = "1"
os.environ.pop("REACHY_MINI_CUSTOM_PROFILE", None)
os.environ.pop("REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY", None)
os.environ.pop("REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY", None)
# Privacy state, the activity log, kept photos and the control token default to the developer's real files.
_PRIVATE_STATE = tempfile.mkdtemp(prefix="reachy-tests-")
os.environ["PRIVACY_STATE_FILE"] = os.path.join(_PRIVATE_STATE, "privacy.json")
os.environ["ACTIVITY_LOG_FILE"] = os.path.join(_PRIVATE_STATE, "activity.jsonl")
os.environ["APP_CONTROL_TOKEN_FILE"] = os.path.join(_PRIVATE_STATE, "control-token")
os.environ["PHOTO_ARCHIVE_DIR"] = os.path.join(_PRIVATE_STATE, "photos")
os.environ.pop("QUIET_HOURS", None)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _private_usage_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests from writing cost records into the developer's real usage ledger."""
    from reachy_mini_conversation_app import usage_costs

    monkeypatch.setattr(usage_costs, "_ledger", usage_costs.UsageLedger(tmp_path / "usage.jsonl"))
