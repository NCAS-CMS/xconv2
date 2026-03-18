from __future__ import annotations

from pathlib import Path

from xconv2.ui.settings_store import SettingsStore


def _build_store(tmp_path: Path) -> SettingsStore:
    settings_path = tmp_path / "settings.json"
    recent_log_path = tmp_path / "recent.log"
    store = SettingsStore(
        settings_path=settings_path,
        recent_log_path=recent_log_path,
        settings_version=1,
        default_max_recent_files=10,
    )
    store.data = store.default_settings()
    return store


def test_record_recent_file_preserves_remote_uri_verbatim(tmp_path: Path) -> None:
    store = _build_store(tmp_path)

    uri = "s3://bnl/CMIP6-test.nc"
    store.record_recent_file(uri)

    assert store.load_recent_files()[0] == uri


def test_record_recent_file_expands_local_paths(tmp_path: Path) -> None:
    store = _build_store(tmp_path)

    local = "~/tmp/example.nc"
    store.record_recent_file(local)

    assert store.load_recent_files()[0] == str(Path(local).expanduser())
