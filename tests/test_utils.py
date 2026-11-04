from reachy_mini_conversation_app.utils import settings_behind_privacy_prompt


def test_path_settings_in_prompted_folders_are_named(tmp_path):
    """Path settings inside Desktop, Documents or Downloads are named; other settings and paths are not."""
    env = {
        "SPOTIFY_CREDENTIALS_FILE": str(tmp_path / "Documents" / "spotify" / "credentials.json"),
        "PHOTO_ARCHIVE_DIR": str(tmp_path / "Desktop" / "photos"),
        "WAKE_BASE_SET": str(tmp_path / ".local" / "share" / "base.npz"),
        "PWD": str(tmp_path / "Documents"),
        "TUYA_DEVICES_FILE": "",
    }
    assert settings_behind_privacy_prompt(env, home=tmp_path) == ["PHOTO_ARCHIVE_DIR", "SPOTIFY_CREDENTIALS_FILE"]
