from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
import types

import pytest

from xconv2.cf_templates import coordinate_list
from xconv2.xconv_cf_interface import coordinate_info, field_info
from xconv2.gui import CFVMain
import xconv2.main_window as main_window


@dataclass
class _DummyStatus:
    messages: list[str] = field(default_factory=list)

    def showMessage(self, message: str) -> None:
        self.messages.append(message)


@dataclass
class _DummyWindow:
    status: _DummyStatus = field(default_factory=_DummyStatus)
    sent_tasks: list[str] = field(default_factory=list)
    sent_control_tasks: list[tuple[str, dict[str, object]]] = field(default_factory=list)
    _remote_session_id: str | None = None
    _remote_descriptor_hash: str | None = None
    _remote_descriptor: dict[str, object] | None = None

    def _show_status_message(self, message: str, is_error: bool = False) -> None:
        _ = is_error
        self.status.showMessage(message)

    def _send_worker_task(self, code: str) -> None:
        self.sent_tasks.append(code)

    def _send_worker_control_task(self, kind: str, payload: dict[str, object]) -> None:
        self.sent_control_tasks.append((kind, payload))


class _FakeWorkerPipe:
    def __init__(self) -> None:
        self.payloads: list[str] = []

    def write(self, payload: bytes) -> None:
        self.payloads.append(payload.decode())


def test_load_selected_file_builds_worker_task() -> None:
    """The GUI should send a worker task when a file is selected."""
    window = _DummyWindow()
    file_path = "/tmp/mock-data.nc"

    CFVMain._load_selected_file(window, file_path)

    assert window.status.messages[-1] == f"Loading file: {file_path}"
    assert len(window.sent_tasks) == 1

    code = window.sent_tasks[0]
    assert f"cf.read({file_path!r})" in code
    assert "fields = field_info(f)" in code
    assert "send_to_gui('METADATA', fields)" in code


def test_load_selected_file_task_executes_with_mock_cf_example_fields() -> None:
    """
    The generated worker code should emit field metadata when executed.
    """
    cf = pytest.importorskip("cf")

    window = _DummyWindow()
    file_path = "/tmp/mock-data.nc"
    CFVMain._load_selected_file(window, file_path)
    code = window.sent_tasks[0]

    messages: list[tuple[str, object]] = []

    class _FakeCF:
        @staticmethod
        def read(_path: str):
            return cf.example_fields(0, 1, 2, 3, 4, 5, 6, 7)

    namespace = {
        "cf": _FakeCF,
        "field_info": field_info,
        "send_to_gui": lambda prefix, payload: messages.append((prefix, payload)),
    }

    exec(code, namespace)

    prefix, payload = messages[-1]
    assert prefix == "METADATA"
    assert isinstance(payload, list)
    assert len(payload) == 8
    assert all(isinstance(item, dict) for item in payload)

    first = payload[0]
    assert str(first["identity"]).startswith("specific_humidity")
    assert "latitude" in str(first["detail"])
    assert isinstance(first["properties"], dict)


def test_coordinate_list_emits_coordinates_for_example_field() -> None:
    """Coordinate template should emit a non-empty coordinate payload for a sample field."""
    cf = pytest.importorskip("cf")

    messages: list[tuple[str, object]] = []
    namespace = {
        "cf": cf,
        "coordinate_info": coordinate_info,
        "send_to_gui": lambda prefix, payload: messages.append((prefix, payload)),
    }

    code = "f = cf.example_fields(0, 1, 2, 3, 4, 5, 6, 7)\n" + coordinate_list(0)
    exec(code, namespace)

    prefix, payload = messages[-1]
    assert prefix == "COORD"
    assert isinstance(payload, list)
    assert payload
    assert isinstance(payload[0], tuple)
    assert len(payload[0]) == 3
    assert payload[0][0].startswith("latitude")
    assert isinstance(payload[0][1], list)
    assert isinstance(payload[0][2], str)


def test_load_remote_selected_file_builds_control_task() -> None:
    window = _DummyWindow(
        _remote_session_id="session-1",
        _remote_descriptor_hash="hash-1",
        _remote_descriptor={"protocol": "sftp"},
    )

    CFVMain._load_remote_selected_file(window, "ssh://host/data/file.nc", "/data/file.nc")

    assert window.status.messages[-1] == "Loading remote file: ssh://host/data/file.nc"
    assert window.sent_control_tasks == [
        (
            "REMOTE_OPEN",
            {
                "session_id": "session-1",
                "descriptor_hash": "hash-1",
                "descriptor": {"protocol": "sftp"},
                "uri": "ssh://host/data/file.nc",
                "path": "/data/file.nc",
            },
        )
    ]


def test_send_worker_control_task_writes_typed_headers() -> None:
    fake_worker = _FakeWorkerPipe()
    window = _DummyWindow()
    window.worker = fake_worker

    CFVMain._send_worker_control_task(window, "REMOTE_PREPARE", {"session_id": "abc", "value": 2})

    assert len(fake_worker.payloads) == 1
    payload = fake_worker.payloads[0]
    assert payload.startswith("#TASK_KIND:REMOTE_PREPARE\n#TASK_PAYLOAD_B64:")
    assert payload.endswith("#END_TASK\n")
    assert hasattr(window, "_pending_worker_task_starts")
    assert len(window._pending_worker_task_starts) == 1

    encoded = payload.split("#TASK_PAYLOAD_B64:", 1)[1].split("\n", 1)[0]
    decoded = json.loads(base64.b64decode(encoded.encode("ascii")).decode("utf-8"))
    assert decoded == {"session_id": "abc", "value": 2}


# ---------------------------------------------------------------------------
# Window title tests
# ---------------------------------------------------------------------------

@dataclass
class _DummyTitleWindow:
    base_window_title: str = "xconv2 (test)"
    current_file_path: str = ""
    _remote_descriptor: dict | None = None
    titles: list[str] = field(default_factory=list)

    def setWindowTitle(self, title: str) -> None:
        self.titles.append(title)

    def _set_window_title_for_file(self, file_path: str) -> None:  # super() fallback
        from pathlib import Path
        self.current_file_path = file_path
        self.setWindowTitle(f"{self.base_window_title}: {Path(file_path).name}")


def test_set_window_title_for_remote_file_includes_host_tag() -> None:
    window = _DummyTitleWindow(
        _remote_descriptor={
            "protocol": "sftp",
            "uri_scheme": "ssh",
            "display_name": "sci1",
        }
    )

    CFVMain._set_window_title_for_file(window, "/data/archive/model.nc")

    assert window.titles == ["xconv2 (test): model.nc (ssh:sci1)"]
    assert window.current_file_path == "/data/archive/model.nc"


def test_set_window_title_for_local_file_no_tag() -> None:
    # Without a remote descriptor the CFVMain override delegates to CFVCore,
    # which just shows the bare filename. Test CFVCore directly.
    from xconv2.core_window import CFVCore

    window = _DummyTitleWindow()

    CFVCore._set_window_title_for_file(window, "/home/user/data/local.nc")

    assert window.titles == ["xconv2 (test): local.nc"]
    assert window.current_file_path == "/home/user/data/local.nc"


def test_recent_menu_label_for_remote_uri_uses_filename_and_alias() -> None:
    from xconv2.core_window import CFVCore

    class _RecentLabelHost:
        def __init__(self) -> None:
            self._settings = {
                "recent_uri_aliases": {
                    "https://example.org/archive/test1.nc": "canari",
                }
            }

    host = _RecentLabelHost()

    label = CFVCore._recent_menu_label(host, "https://example.org/archive/test1.nc")

    assert label == "test1.nc (canari)"


def test_default_open_uri_value_returns_most_recent_uri() -> None:
    from xconv2.core_window import CFVCore

    class _RecentDefaultHost:
        def __init__(self) -> None:
            self._recent = [
                "/tmp/local.nc",
                "ssh://alpha.example.org/data/field.nc",
                "https://example.org/archive/test2.nc",
            ]

        def _load_recent_files(self):
            return list(self._recent)

    host = _RecentDefaultHost()

    value = CFVCore._default_open_uri_value(host)

    assert value == "ssh://alpha.example.org/data/field.nc"


def test_default_open_uri_value_for_s3_uses_endpoint_host(monkeypatch: pytest.MonkeyPatch) -> None:
    from xconv2.core_window import CFVCore

    class _RecentDefaultHost:
        def __init__(self) -> None:
            self._settings = {
                "recent_uri_aliases": {
                    "s3://bnl/CMIP6-test.nc": "hpos",
                }
            }
            self._recent = [
                "s3://bnl/CMIP6-test.nc",
            ]

        def _load_recent_files(self):
            return list(self._recent)

        def _shareable_remote_uri(self, uri: str) -> str:
            return CFVCore._shareable_remote_uri(self, uri)

    monkeypatch.setattr(
        main_window.RemoteConfigurationDialog,
        "_load_s3_locations",
        staticmethod(
            lambda: {
                "hpos": {
                    "url": "https://hpos.example.org",
                    "accessKey": "minioadmin",
                    "secretKey": "minioadmin",
                    "api": "S3v4",
                }
            }
        ),
    )

    host = _RecentDefaultHost()

    value = CFVCore._default_open_uri_value(host)

    assert value == "s3://hpos.example.org/bnl/CMIP6-test.nc"


def test_recent_menu_tooltip_for_s3_uses_shareable_host_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    from xconv2.core_window import CFVCore

    class _RecentTooltipHost:
        def __init__(self) -> None:
            self._settings = {
                "recent_uri_aliases": {
                    "s3://bnl/CMIP6-test.nc": "hpos",
                }
            }

    monkeypatch.setattr(
        main_window.RemoteConfigurationDialog,
        "_load_s3_locations",
        staticmethod(
            lambda: {
                "hpos": {
                    "url": "https://hpos.example.org",
                    "accessKey": "minioadmin",
                    "secretKey": "minioadmin",
                    "api": "S3v4",
                }
            }
        ),
    )

    host = _RecentTooltipHost()

    tooltip = CFVCore._recent_menu_tooltip(host, "s3://bnl/CMIP6-test.nc")

    assert tooltip == "s3://hpos.example.org/bnl/CMIP6-test.nc"


def test_https_locations_from_configure_are_passed_to_open_dialog(monkeypatch: pytest.MonkeyPatch) -> None:
    class _DummyRemoteFlowWindow:
        def __init__(self) -> None:
            self._settings = {
                "last_remote_configuration": {},
                "last_remote_open": {},
            }
            self.saved = 0

        def _save_settings(self) -> None:
            self.saved += 1

        def _open_remote_from_config(self, _config: dict[str, object]) -> None:
            pass

        def _configure_remote(self) -> None:
            CFVMain._configure_remote(self)

    window = _DummyRemoteFlowWindow()

    configured_https = {
        "archive": {"url": "https://archive.example.org/data"},
    }

    monkeypatch.setattr(
        main_window.RemoteConfigurationDialog,
        "get_configuration",
        lambda _parent, state=None: (None, False, {"https_locations": configured_https, **(state or {})}),
    )

    captured_state: dict[str, object] = {}

    def _fake_open_config(_parent, state=None):
        nonlocal captured_state
        captured_state = dict(state or {})
        return None, False, dict(state or {})

    monkeypatch.setattr(main_window.RemoteOpenDialog, "get_configuration", _fake_open_config)

    # Save-only configure should persist HTTPS aliases into shared settings.
    CFVMain._configure_remote(window)
    assert window._settings["remote_https_locations"] == configured_https

    # Open dialog should receive merged HTTPS aliases from settings/config state.
    CFVMain._choose_remote(window)
    assert captured_state.get("https_locations") == configured_https


def test_choose_remote_injects_cache_defaults_when_open_dialog_returns_no_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _DummyRemoteFlowWindow:
        def __init__(self) -> None:
            self._settings = {
                "last_remote_configuration": {
                    "disk_mode": "Blocks",
                    "disk_location": "/tmp/xconv-cache",
                    "disk_limit_gb": 5,
                    "disk_expiry": "7 days",
                },
                "last_remote_open": {},
            }
            self.saved = 0
            self.opened_config: dict[str, object] | None = None

        def _save_settings(self) -> None:
            self.saved += 1

        def _open_remote_from_config(self, config: dict[str, object]) -> None:
            self.opened_config = dict(config)

        def _with_cache_defaults(self, config: dict[str, object]) -> dict[str, object]:
            return CFVMain._with_cache_defaults(self, config)

    window = _DummyRemoteFlowWindow()

    monkeypatch.setattr(
        main_window.RemoteOpenDialog,
        "get_configuration",
        lambda _parent, state=None: (
            {"protocol": "HTTPS", "remote": {"mode": "Select from existing", "alias": "archive", "details": {"url": "https://example.org"}}},
            True,
            dict(state or {}),
        ),
    )

    CFVMain._choose_remote(window)

    assert window.opened_config is not None
    cache = window.opened_config.get("cache")
    assert isinstance(cache, dict)
    assert cache == {
        "disk_mode": "Blocks",
        "disk_location": "/tmp/xconv-cache",
        "disk_limit_gb": 5,
        "disk_expiry": "7 days",
    }


def test_open_remote_from_config_keeps_existing_session_and_clears_loaded_ui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _DummyRemoteOpenWindow:
        def __init__(self) -> None:
            self._settings = {}
            self.released = 0
            self.cleared = 0
            self._remote_session_id = None
            self._remote_descriptor_hash = None
            self._remote_descriptor = None
            self._pending_prepare_log_dialog = None
            self._pending_prepare_loop = None
            self._pending_prepare_loop_ok = True
            self._pending_prepare_failure_message = ""

        def _prepare_ssh_config_for_auth(self, config: dict[str, object]) -> dict[str, object]:
            return config

        def _release_remote_session_if_active(self) -> None:
            self.released += 1

        def _clear_loaded_data_views(self) -> None:
            self.cleared += 1

        def _maybe_retry_ssh_authentication(self, _config: dict[str, object], _failure_message: str) -> bool:
            return False

        def _send_worker_control_task(self, _kind: str, _payload: dict[str, object]) -> None:
            return None

        def _make_worker_list_callback(self):
            return lambda _path: []

        def _show_status_message(self, _message: str, is_error: bool = False) -> None:
            _ = is_error

        def _set_window_title_for_file(self, _file_path: str) -> None:
            return None

        def _record_recent_uri(self, _uri: str, _host_alias: str | None = None) -> None:
            return None

        def _record_recent_file(self, _file_path: str) -> None:
            return None

        def _load_remote_selected_file(self, _uri: str, _remote_path: str) -> None:
            return None

    window = _DummyRemoteOpenWindow()

    monkeypatch.setattr(
        main_window,
        "build_remote_filesystem_spec",
        lambda _config: types.SimpleNamespace(display_name="HTTP"),
    )
    monkeypatch.setattr(main_window, "spec_to_descriptor", lambda _spec, cache=None: {"protocol": "http", "cache": cache})
    monkeypatch.setattr(main_window, "remote_descriptor_hash", lambda _descriptor: "hash-1")

    class _FakeLogDialog:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def show(self) -> None:
            return None

        def close(self) -> None:
            return None

        def exec(self) -> int:
            return 0

    class _FakeNavigator:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def exec(self) -> int:
            return QDialog.Rejected

    monkeypatch.setattr(main_window, "RemoteLoginLogDialog", _FakeLogDialog)
    monkeypatch.setattr(main_window, "RemoteFileNavigatorDialog", _FakeNavigator)
    monkeypatch.setattr(main_window.QApplication, "processEvents", staticmethod(lambda: None))

    class _FakeLoop:
        def exec(self) -> None:
            return None

    monkeypatch.setattr(main_window, "QEventLoop", _FakeLoop)

    CFVMain._open_remote_from_config(window, {"protocol": "HTTP", "remote": {"details": {"url": "http://server/public"}}})

    assert window.released == 0
    assert window.cleared == 1


def test_resolve_remote_uri_s3_prefers_recent_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    class _DummyResolveWindow:
        def __init__(self) -> None:
            self._settings = {
                "recent_uri_aliases": {
                    "s3://bnl/CMIP6-test.nc": "hpos",
                },
                "last_remote_configuration": {
                    "s3_existing_alias": "hpos",
                },
            }

    monkeypatch.setattr(
        main_window.RemoteConfigurationDialog,
        "_load_s3_locations",
        staticmethod(
            lambda: {
                "hpos": {
                    "url": "https://hpos.example.org",
                    "accessKey": "minioadmin",
                    "secretKey": "minioadmin",
                    "api": "S3v4",
                }
            }
        ),
    )

    window = _DummyResolveWindow()

    config, remote_path, host_alias, unknown_host = CFVMain._resolve_remote_uri(
        window,
        "s3://bnl/CMIP6-test.nc",
    )

    assert unknown_host is False
    assert remote_path == "bnl/CMIP6-test.nc"
    assert host_alias == "hpos"
    assert config is not None
    assert config["protocol"] == "S3"
    remote = config["remote"]
    assert isinstance(remote, dict)
    assert remote.get("alias") == "hpos"
    details = remote.get("details")
    assert isinstance(details, dict)
    assert details.get("url") == "https://hpos.example.org"


def test_resolve_remote_uri_s3_accepts_legacy_single_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    class _DummyResolveWindow:
        def __init__(self) -> None:
            self._settings = {
                "recent_uri_aliases": {
                    "s3://bnl/CMIP6-test.nc": "hpos",
                },
                "last_remote_configuration": {
                    "s3_existing_alias": "hpos",
                },
            }

    monkeypatch.setattr(
        main_window.RemoteConfigurationDialog,
        "_load_s3_locations",
        staticmethod(
            lambda: {
                "hpos": {
                    "url": "https://hpos.example.org",
                    "accessKey": "minioadmin",
                    "secretKey": "minioadmin",
                    "api": "S3v4",
                }
            }
        ),
    )

    window = _DummyResolveWindow()

    config, remote_path, host_alias, unknown_host = CFVMain._resolve_remote_uri(
        window,
        "s3:/bnl/CMIP6-test.nc",
    )

    assert unknown_host is False
    assert remote_path == "bnl/CMIP6-test.nc"
    assert host_alias == "hpos"
    assert config is not None


def test_resolve_remote_uri_s3_accepts_host_based_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    class _DummyResolveWindow:
        def __init__(self) -> None:
            self._settings = {
                "recent_uri_aliases": {},
                "last_remote_configuration": {
                    "s3_existing_alias": "hpos",
                },
            }

    monkeypatch.setattr(
        main_window.RemoteConfigurationDialog,
        "_load_s3_locations",
        staticmethod(
            lambda: {
                "hpos": {
                    "url": "https://hpos.example.org",
                    "accessKey": "minioadmin",
                    "secretKey": "minioadmin",
                    "api": "S3v4",
                }
            }
        ),
    )

    window = _DummyResolveWindow()

    config, remote_path, host_alias, unknown_host = CFVMain._resolve_remote_uri(
        window,
        "s3://hpos.example.org/bnl/CMIP6-test.nc",
    )

    assert unknown_host is False
    assert remote_path == "bnl/CMIP6-test.nc"
    assert host_alias == "hpos"
    assert config is not None
