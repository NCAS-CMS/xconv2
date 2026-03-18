from __future__ import annotations

import xconv2.main_window as main_window
from xconv2.gui import CFVMain
from xconv2.ui.remote_file_navigator import build_remote_filesystem_spec


class _DummyHost:
    def __init__(self) -> None:
        self._ssh_session_passwords: dict[str, str] = {}
        self.messages: list[tuple[str, bool]] = []

    def _show_status_message(self, message: str, is_error: bool = False) -> None:
        self.messages.append((message, is_error))

    def _probe_ssh_auth_methods(self, hostname: str, username: str) -> set[str] | None:
        _ = hostname, username
        return None

    def _prompt_ssh_secret(self, *, title: str, prompt: str) -> tuple[str, bool]:
        _ = title, prompt
        return "", False


def test_prepare_ssh_config_for_auth_prompts_for_password(monkeypatch) -> None:
    host = _DummyHost()
    host._probe_ssh_auth_methods = lambda hostname, username: {"publickey", "password"}
    host._prompt_ssh_secret = lambda **kwargs: ("s3cr3t", True)
    monkeypatch.setattr(CFVMain, "_validate_ssh_secret", staticmethod(lambda *args, **kwargs: True))

    config = {
        "protocol": "SSH",
        "remote": {
            "mode": "Select from existing",
            "alias": "hpc",
            "details": {
                "hostname": "hpc.example.org",
                "user": "alice",
            },
        },
    }

    updated = CFVMain._prepare_ssh_config_for_auth(host, config)

    assert isinstance(updated, dict)
    remote = updated["remote"]
    assert isinstance(remote, dict)
    details = remote["details"]
    assert isinstance(details, dict)
    assert details["password"] == "s3cr3t"
    assert host._ssh_session_passwords["alice@hpc.example.org:22"] == "s3cr3t"


def test_prepare_ssh_config_for_auth_keyboard_interactive_prompts_for_secret(monkeypatch) -> None:
    host = _DummyHost()
    host._probe_ssh_auth_methods = lambda hostname, username: {"keyboard-interactive"}
    host._prompt_ssh_secret = lambda **kwargs: ("123456", True)

    config = {
        "protocol": "SSH",
        "remote": {
            "mode": "Select from existing",
            "alias": "hpc",
            "details": {
                "hostname": "hpc.example.org",
                "user": "alice",
            },
        },
    }

    updated = CFVMain._prepare_ssh_config_for_auth(host, config)

    assert isinstance(updated, dict)
    remote = updated["remote"]
    assert isinstance(remote, dict)
    details = remote["details"]
    assert isinstance(details, dict)
    assert details["password"] == "123456"


def test_build_remote_filesystem_spec_includes_ssh_password() -> None:
    spec = build_remote_filesystem_spec(
        {
            "protocol": "SSH",
            "remote": {
                "alias": "hpc",
                "details": {
                    "hostname": "hpc.example.org",
                    "user": "alice",
                    "password": "s3cr3t",
                },
            },
        }
    )

    assert spec.protocol == "sftp"
    assert spec.storage_options.get("username") == "alice"
    assert spec.storage_options.get("password") == "s3cr3t"


def test_prepare_ssh_config_for_auth_retries_wrong_cached_password(monkeypatch) -> None:
    host = _DummyHost()
    host._ssh_session_passwords["alice@hpc.example.org:22"] = "wrong"
    host._probe_ssh_auth_methods = lambda hostname, username, port=22: {"password"}

    prompts = iter([("newsecret", True)])
    host._prompt_ssh_secret = lambda **kwargs: next(prompts)

    def _fake_validate(hostname: str, username: str, secret: str, *, port: int = 22, timeout: float = 6.0):
        _ = hostname, username, port, timeout
        return secret == "newsecret"

    warnings: list[str] = []
    monkeypatch.setattr(CFVMain, "_validate_ssh_secret", staticmethod(_fake_validate))
    monkeypatch.setattr(
        main_window.QMessageBox,
        "warning",
        staticmethod(lambda _parent, _title, text: warnings.append(text)),
    )

    config = {
        "protocol": "SSH",
        "remote": {
            "mode": "Select from existing",
            "alias": "hpc",
            "details": {
                "hostname": "hpc.example.org",
                "user": "alice",
            },
        },
    }

    updated = CFVMain._prepare_ssh_config_for_auth(host, config)

    assert isinstance(updated, dict)
    assert host._ssh_session_passwords["alice@hpc.example.org:22"] == "newsecret"
    remote = updated["remote"]
    assert isinstance(remote, dict)
    details = remote["details"]
    assert isinstance(details, dict)
    assert details["password"] == "newsecret"
    assert warnings == []


def test_prepare_ssh_config_for_auth_collects_proxyjump_secret(monkeypatch) -> None:
    host = _DummyHost()
    prompted_messages: list[str] = []

    def _fake_probe(hostname: str, username: str, port: int = 22):
        if hostname == "bastion.example.org":
            return {"password"}
        return set()

    host._probe_ssh_auth_methods = _fake_probe
    host._prompt_ssh_secret = lambda **kwargs: (prompted_messages.append(str(kwargs.get("prompt", ""))) or "jumpsecret", True)

    monkeypatch.setattr(CFVMain, "_validate_ssh_secret", staticmethod(lambda *args, **kwargs: True))
    monkeypatch.setattr(
        CFVMain,
        "_resolve_ssh_alias",
        staticmethod(lambda alias: ("bastion.example.org", "bob") if alias == "bastion" else (alias, None)),
    )

    config = {
        "protocol": "SSH",
        "remote": {
            "mode": "Select from existing",
            "alias": "hpc",
            "details": {
                "hostname": "target.example.org",
                "user": "alice",
                "proxyjump": "bastion",
            },
        },
    }

    updated = CFVMain._prepare_ssh_config_for_auth(host, config)

    assert isinstance(updated, dict)
    remote = updated["remote"]
    assert isinstance(remote, dict)
    details = remote["details"]
    assert isinstance(details, dict)
    assert details["proxyjump_password"] == "jumpsecret"
    assert details["proxyjump_user"] == "bob"
    assert host._ssh_session_passwords["jump:bob@bastion.example.org:22"] == "jumpsecret"
    assert prompted_messages
    assert (
        "Authenticating with bastion host bastion.example.org before proxyjump to target.example.org."
        in prompted_messages[0]
    )


def test_build_remote_filesystem_spec_includes_proxyjump_credentials() -> None:
    spec = build_remote_filesystem_spec(
        {
            "protocol": "SSH",
            "remote": {
                "alias": "hpc",
                "details": {
                    "hostname": "hpc.example.org",
                    "user": "alice",
                    "proxyjump": "bastion",
                    "proxyjump_password": "jumpsecret",
                    "proxyjump_user": "bob",
                },
            },
        }
    )

    assert spec.storage_options.get("proxyjump_password") == "jumpsecret"
    assert spec.storage_options.get("proxyjump_username") == "bob"


def test_is_ssh_auth_failure_message_matches_expected_patterns() -> None:
    assert CFVMain._is_ssh_auth_failure_message(
        "Bad authentication type; allowed types: ['keyboard-interactive']"
    )
    assert CFVMain._is_ssh_auth_failure_message("Permission denied (publickey,password)")
    assert not CFVMain._is_ssh_auth_failure_message("Connection timed out while reading directory")


def test_clear_ssh_cached_secrets_for_config_clears_target_and_bastion(monkeypatch) -> None:
    host = _DummyHost()
    host._ssh_session_passwords = {
        "alice@target.example.org:22": "targetsecret",
        "jump:bob@bastion.example.org:22": "jumpsecret",
        "other@elsewhere:22": "keep",
    }

    monkeypatch.setattr(
        CFVMain,
        "_resolve_ssh_alias",
        staticmethod(lambda alias: ("bastion.example.org", "bob") if alias == "bastion" else (alias, None)),
    )

    config = {
        "protocol": "SSH",
        "remote": {
            "details": {
                "hostname": "target.example.org",
                "user": "alice",
                "proxyjump": "bastion",
            }
        },
    }

    CFVMain._clear_ssh_cached_secrets_for_config(host, config)

    assert "alice@target.example.org:22" not in host._ssh_session_passwords
    assert "jump:bob@bastion.example.org:22" not in host._ssh_session_passwords
    assert host._ssh_session_passwords.get("other@elsewhere:22") == "keep"


def test_maybe_retry_ssh_authentication_returns_true_on_retry(monkeypatch) -> None:
    host = _DummyHost()
    cleared: list[dict[str, object]] = []

    host._clear_ssh_cached_secrets_for_config = lambda cfg: cleared.append(cfg)

    monkeypatch.setattr(
        main_window.QMessageBox,
        "question",
        staticmethod(lambda *args, **kwargs: main_window.QMessageBox.Retry),
    )

    config = {
        "protocol": "SSH",
        "remote": {"details": {"hostname": "target.example.org", "user": "alice"}},
    }

    result = CFVMain._maybe_retry_ssh_authentication(
        host,
        config,
        "Bad authentication type; allowed types: ['keyboard-interactive']",
    )

    assert result is True
    assert len(cleared) == 1


def test_maybe_retry_ssh_authentication_returns_false_for_non_auth_failure(monkeypatch) -> None:
    host = _DummyHost()
    called = {"clear": 0}

    host._clear_ssh_cached_secrets_for_config = lambda cfg: called.__setitem__("clear", called["clear"] + 1)

    monkeypatch.setattr(
        main_window.QMessageBox,
        "question",
        staticmethod(lambda *args, **kwargs: main_window.QMessageBox.Retry),
    )

    config = {
        "protocol": "SSH",
        "remote": {"details": {"hostname": "target.example.org", "user": "alice"}},
    }

    result = CFVMain._maybe_retry_ssh_authentication(
        host,
        config,
        "Connection timed out while listing files",
    )

    assert result is False
    assert called["clear"] == 0
