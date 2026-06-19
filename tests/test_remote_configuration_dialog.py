from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from xconv2.ui.dialogs import RemoteConfigurationDialog, RemoteOpenDialog


def _ensure_qapp() -> QApplication:
    app = QApplication.instance()
    if app is not None:
        return app

    try:
        return QApplication([])
    except Exception as exc:  # pragma: no cover - environment specific
        pytest.skip(f"Qt application setup unavailable: {exc}")


def test_load_s3_locations_returns_alias_mapping(monkeypatch) -> None:
    def _fake_get_locations():
        return ({"cedadev": {"url": "https://example.invalid", "api": "S3v4"}}, {"example.invalid": "cedadev"})

    monkeypatch.setattr("xconv2.ui.dialogs.get_locations", _fake_get_locations)

    locations = RemoteConfigurationDialog._load_s3_locations()

    assert locations == {"cedadev": {"url": "https://example.invalid", "api": "S3v4"}}


def test_parse_ssh_config_extracts_named_hosts(tmp_path: Path) -> None:
    config_path = tmp_path / "config"
    config_path.write_text(
        """
Host alpha
    HostName alpha.example.org
    User alice
    IdentityFile ~/.ssh/id_alpha
    ProxyJump gateway.example.org

Host *
    User ignored

Host beta gamma
    HostName shared.example.org
    User bob
""".strip(),
        encoding="utf-8",
    )

    hosts = RemoteConfigurationDialog._parse_ssh_config(config_path)

    assert hosts["alpha"] == {
        "hostname": "alpha.example.org",
        "user": "alice",
        "identityfile": "~/.ssh/id_alpha",
        "proxyjump": "gateway.example.org",
    }
    assert hosts["beta"]["hostname"] == "shared.example.org"
    assert hosts["gamma"]["user"] == "bob"
    assert "*" not in hosts


def test_s3_config_path_from_choice_maps_targets() -> None:
    assert RemoteConfigurationDialog._s3_config_path_from_choice("MinIO") == Path.home() / ".mc/config.json"
    assert RemoteConfigurationDialog._s3_config_path_from_choice("xconv") == Path.home() / ".config/cfview/config.json"


def test_save_s3_location_writes_minio_style_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"

    written_path = RemoteConfigurationDialog._save_s3_location(
        "cedadev",
        "https://example.invalid",
        "abc",
        "xyz",
        "S3v4",
        config_path=config_path,
    )

    payload = json.loads(config_path.read_text(encoding="utf-8"))

    assert written_path == config_path
    assert payload["aliases"]["cedadev"] == {
        "url": "https://example.invalid",
        "accessKey": "abc",
        "secretKey": "xyz",
        "api": "S3v4",
        "path": "auto",
    }


def test_upsert_ssh_config_text_replaces_existing_host_block() -> None:
    existing = """
Host alpha
    HostName old.example.org
    User olduser

Host beta
    HostName beta.example.org
    User betauser
""".lstrip()

    updated = RemoteConfigurationDialog._upsert_ssh_config_text(
        existing,
        "alpha",
        "new.example.org",
        "alice",
        "~/.ssh/id_alpha",
        "gateway.example.org",
    )

    assert "HostName new.example.org" in updated
    assert "User alice" in updated
    assert "IdentityFile ~/.ssh/id_alpha" in updated
    assert "ProxyJump gateway.example.org" in updated
    assert "Host beta" in updated


def test_save_ssh_host_writes_config_file(tmp_path: Path) -> None:
    config_path = tmp_path / "ssh_config"

    written_path = RemoteConfigurationDialog._save_ssh_host(
        "alpha",
        "alpha.example.org",
        "alice",
        "~/.ssh/id_alpha",
        "gateway.example.org",
        config_path=config_path,
    )

    content = config_path.read_text(encoding="utf-8")

    assert written_path == config_path
    assert "Host alpha" in content
    assert "HostName alpha.example.org" in content
    assert "User alice" in content
    assert "ProxyJump gateway.example.org" in content


def test_load_https_locations_prefers_https_key() -> None:
    state = {
        "https_locations": {
            "prod": {
                "url": "https://example.org/data",
                "reductionist_url": "https://reductionist.example.org/prod",
            },
        },
        "http_locations": {
            "legacy": {"url": "http://legacy.example.org"},
        },
    }

    locations = RemoteConfigurationDialog._load_http_locations(state)

    assert locations == {
        "prod": {
            "url": "https://example.org/data",
            "reductionist_url": "https://reductionist.example.org/prod",
        }
    }


def test_load_https_locations_falls_back_to_legacy_http_key() -> None:
    state = {
        "http_locations": {
            "legacy": {"url": "https://legacy.example.org"},
        },
    }

    locations = RemoteConfigurationDialog._load_http_locations(state)

    assert locations == {"legacy": {"url": "https://legacy.example.org"}}


def test_open_dialog_uses_https_locations() -> None:
    state = {
        "https_locations": {
            "alpha": {
                "url": "https://alpha.example.org",
                "reductionist_url": "https://reductionist.example.org/alpha",
            },
            "beta": {"base_url": "https://beta.example.org"},
        },
    }

    locations = RemoteOpenDialog._load_http_locations(state)

    assert locations == {
        "alpha": {
            "url": "https://alpha.example.org",
            "reductionist_url": "https://reductionist.example.org/alpha",
        },
        "beta": {"url": "https://beta.example.org"},
    }


def test_load_s3_locations_merges_reductionist_from_state(monkeypatch) -> None:
    def _fake_get_locations():
        return (
            {
                "cedadev": {"url": "https://example.invalid", "api": "S3v4"},
                "noextra": {"url": "https://noextra.invalid", "api": "S3v4"},
            },
            {"example.invalid": "cedadev", "noextra.invalid": "noextra"},
        )

    monkeypatch.setattr("xconv2.ui.dialogs.get_locations", _fake_get_locations)

    locations = RemoteConfigurationDialog._load_s3_locations(
        {
            "s3_reductionist_locations": {
                "cedadev": "https://reductionist.example.org/ceda",
                "unknown": "https://reductionist.example.org/unknown",
            }
        }
    )

    assert locations["cedadev"]["reductionist_url"] == "https://reductionist.example.org/ceda"
    assert "reductionist_url" not in locations["noextra"]


def test_normalize_s3_reductionist_locations_filters_empty_values() -> None:
    cleaned = RemoteConfigurationDialog._normalize_s3_reductionist_locations(
        {
            "alpha": "https://reductionist.example.org/alpha",
            "  ": "https://reductionist.example.org/blank-alias",
            "beta": "   ",
            "gamma": None,
        }
    )

    assert cleaned == {"alpha": "https://reductionist.example.org/alpha"}


def test_remote_python_options_from_discovered_envs() -> None:
    options = RemoteConfigurationDialog._remote_python_options_from_envs(
        {
            "base": "/opt/miniforge3",
            "work26": "/opt/miniforge3/envs/work26",
        }
    )

    assert options["python3"] == "python3"
    assert options["base"] == "conda run -p /opt/miniforge3 --no-capture-output python"
    assert options["work26"] == "conda run -p /opt/miniforge3/envs/work26 --no-capture-output python"


def test_coerce_bool_handles_common_string_values() -> None:
    assert RemoteConfigurationDialog._coerce_bool(True) is True
    assert RemoteConfigurationDialog._coerce_bool(False) is False
    assert RemoteConfigurationDialog._coerce_bool("true") is True
    assert RemoteConfigurationDialog._coerce_bool("yes") is True
    assert RemoteConfigurationDialog._coerce_bool("1") is True
    assert RemoteConfigurationDialog._coerce_bool("false") is False
    assert RemoteConfigurationDialog._coerce_bool("no") is False
    assert RemoteConfigurationDialog._coerce_bool("0") is False
    assert RemoteConfigurationDialog._coerce_bool("", default=True) is True


def test_extract_ssh_runtime_preferences_normalizes_mapping() -> None:
    prefs = RemoteConfigurationDialog._extract_ssh_runtime_preferences(
        {
            "ssh_runtime_preferences": {
                "alpha": {
                    "remote_python": "conda run -n work26 python",
                    "remote_python_options": {"python3": "python3", "work26": "conda run -n work26 python"},
                    "login_shell": "true",
                },
                "": {"remote_python": "ignored"},
            }
        }
    )

    assert "alpha" in prefs
    assert prefs["alpha"]["remote_python"] == "conda run -n work26 python"
    assert prefs["alpha"]["remote_python_options"]["work26"] == "conda run -n work26 python"
    assert prefs["alpha"]["login_shell"] is True
    assert "" not in prefs


def test_apply_ssh_runtime_preferences_overrides_host_details() -> None:
    merged = RemoteConfigurationDialog._apply_ssh_runtime_preferences(
        {
            "alpha": {
                "hostname": "alpha.example.org",
                "user": "alice",
            }
        },
        {
            "alpha": {
                "remote_python": "conda run -n work26 python",
                "login_shell": True,
            }
        },
    )

    assert merged["alpha"]["hostname"] == "alpha.example.org"
    assert merged["alpha"]["remote_python"] == "conda run -n work26 python"
    assert merged["alpha"]["login_shell"] is True


def test_edit_selected_https_prefills_add_new_fields() -> None:
    _ensure_qapp()
    dialog = RemoteConfigurationDialog(
        None,
        state={
            "https_locations": {
                "ceda": {
                    "url": "https://data.example.org",
                    "reductionist_url": "https://reductionist.example.org/ceda",
                }
            }
        },
    )

    dialog.http_mode_combo.setCurrentText("Select from existing")
    dialog.http_existing_combo.setCurrentText("ceda")
    dialog._edit_selected_http_alias()

    assert dialog.http_mode_combo.currentText() == "Add new"
    assert dialog.http_alias_edit.text() == "ceda"
    assert dialog.http_url_edit.text() == "https://data.example.org"
    assert dialog.http_reductionist_url_edit.text() == "https://reductionist.example.org/ceda"


def test_edit_selected_s3_prefills_add_new_fields(monkeypatch) -> None:
    _ensure_qapp()

    def _fake_get_locations():
        return (
            {
                "ceda": {
                    "url": "https://s3.example.org",
                    "access_key": "AKIA",
                    "secret_key": "SECRET",
                    "api": "S3v4",
                    "reductionist_url": "https://reductionist.example.org/s3",
                }
            },
            {},
        )

    monkeypatch.setattr("xconv2.ui.dialogs.get_locations", _fake_get_locations)

    dialog = RemoteConfigurationDialog(None)
    dialog.s3_mode_combo.setCurrentText("Select from existing")
    dialog.s3_existing_combo.setCurrentText("ceda")
    dialog._edit_selected_s3_alias()

    assert dialog.s3_mode_combo.currentText() == "Add new"
    assert dialog.s3_alias_edit.text() == "ceda"
    assert dialog.s3_url_edit.text() == "https://s3.example.org"
    assert dialog.s3_access_key_edit.text() == "AKIA"
    assert dialog.s3_secret_key_edit.text() == "SECRET"
    assert dialog.s3_reductionist_url_edit.text() == "https://reductionist.example.org/s3"


def test_edit_selected_ssh_prefills_add_new_fields(monkeypatch) -> None:
    _ensure_qapp()

    monkeypatch.setattr(
        RemoteConfigurationDialog,
        "_load_ssh_hosts",
        classmethod(
            lambda cls: {
                "alpha": {
                    "hostname": "alpha.example.org",
                    "user": "alice",
                    "identityfile": "~/.ssh/id_alpha",
                    "proxyjump": "bastion.example.org",
                }
            }
        ),
    )

    dialog = RemoteConfigurationDialog(None)
    dialog.ssh_mode_combo.setCurrentText("Select from existing")
    dialog.ssh_existing_combo.setCurrentText("alpha")
    dialog._edit_selected_ssh_alias()

    assert dialog.ssh_mode_combo.currentText() == "Add new"
    assert dialog.ssh_alias_edit.text() == "alpha"
    assert dialog.ssh_hostname_edit.text() == "alpha.example.org"
    assert dialog.ssh_user_edit.text() == "alice"
    assert dialog.ssh_identity_file_edit.text() == "~/.ssh/id_alpha"
    assert dialog.ssh_proxy_jump_edit.text() == "bastion.example.org"


def test_delete_selected_https_removes_alias_after_confirmation(monkeypatch) -> None:
    _ensure_qapp()
    dialog = RemoteConfigurationDialog(None, state={"https_locations": {"ceda": {"url": "https://data.example.org"}}})
    dialog.http_existing_combo.setCurrentText("ceda")
    monkeypatch.setattr("xconv2.ui.dialogs.QMessageBox.question", lambda *args, **kwargs: QMessageBox.Yes)

    dialog._delete_selected_http_alias()

    assert "ceda" not in dialog._http_locations