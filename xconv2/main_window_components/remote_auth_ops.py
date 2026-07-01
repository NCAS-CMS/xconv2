"""SSH auth and cache-default helpers extracted from CFVMain."""

from __future__ import annotations

import socket
from pathlib import Path


def with_cache_defaults(host: object, config: dict[str, object]) -> dict[str, object]:
    """Attach persisted cache settings when a remote config omits cache."""
    merged = dict(config)
    existing_cache = merged.get("cache")
    if isinstance(existing_cache, dict):
        return merged

    raw = host._settings.get("last_remote_configuration", {})
    if not isinstance(raw, dict):
        return merged

    merged["cache"] = {
        "disk_mode": str(raw.get("disk_mode", "Disabled")),
        "disk_location": str(raw.get("disk_location", str(Path.home() / ".cache/xconv2"))),
        "disk_limit_gb": int(raw.get("disk_limit_gb", 10)),
        "disk_expiry": str(raw.get("disk_expiry", "1 day")),
    }
    return merged


def probe_ssh_auth_methods(
    hostname: str,
    username: str,
    *,
    port: int = 22,
    timeout: float = 6.0,
) -> set[str] | None:
    """Probe SSH server auth methods quickly without waiting for filesystem auth timeout."""
    try:
        import paramiko  # type: ignore
    except Exception:
        return None

    sock = None
    transport = None
    try:
        sock = socket.create_connection((hostname, port), timeout=timeout)
        transport = paramiko.Transport(sock)
        transport.start_client(timeout=timeout)
        try:
            transport.auth_none(username)
            return set()
        except paramiko.BadAuthenticationType as exc:  # type: ignore[attr-defined]
            allowed = getattr(exc, "allowed_types", None) or ()
            methods = {
                str(item).strip().lower()
                for item in allowed
                if str(item).strip()
            }
            return methods or None
        except paramiko.AuthenticationException:  # type: ignore[attr-defined]
            return None
    except Exception:
        return None
    finally:
        if transport is not None:
            try:
                transport.close()
            except Exception:
                pass
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


def validate_ssh_secret(
    hostname: str,
    username: str,
    secret: str,
    *,
    port: int = 22,
    timeout: float = 6.0,
) -> bool | None:
    """Validate an SSH password/secret; returns None when validation is inconclusive."""
    try:
        import paramiko  # type: ignore
    except Exception:
        return None

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    from ..ui.remote_file_navigator import _XconvHostKeyPolicy  # noqa: PLC0415
    client.set_missing_host_key_policy(_XconvHostKeyPolicy())
    try:
        client.connect(
            hostname,
            port=port,
            username=username,
            password=secret,
            timeout=timeout,
            auth_timeout=timeout,
            banner_timeout=timeout,
            allow_agent=False,
            look_for_keys=False,
        )
        return True
    except paramiko.AuthenticationException:  # type: ignore[attr-defined]
        return False
    except Exception:
        return None
    finally:
        try:
            client.close()
        except Exception:
            pass


def parse_proxy_jump_target(proxy_jump: str) -> tuple[str | None, str, int]:
    """Parse first ProxyJump hop into (user, host-or-alias, port)."""
    first = proxy_jump.split(",", 1)[0].strip()
    if not first:
        return None, "", 22

    port = 22
    user: str | None = None
    if "@" in first:
        user_part, rest = first.split("@", 1)
        user = user_part or None
    else:
        rest = first

    if ":" in rest:
        host, port_text = rest.rsplit(":", 1)
        try:
            port = int(port_text)
        except ValueError:
            host = rest
    else:
        host = rest

    return user, host, port


def resolve_ssh_alias(alias: str) -> tuple[str, str | None]:
    """Resolve SSH config alias to concrete hostname/user when available."""
    host = alias
    user: str | None = None
    try:
        import paramiko  # type: ignore
        ssh_config_path = Path.home() / ".ssh/config"
        if ssh_config_path.is_file():
            cfg = paramiko.SSHConfig.from_path(str(ssh_config_path))
            looked_up = cfg.lookup(alias)
            looked_host = looked_up.get("hostname")
            looked_user = looked_up.get("user")
            if isinstance(looked_host, str) and looked_host.strip():
                host = looked_host.strip()
            if isinstance(looked_user, str) and looked_user.strip():
                user = looked_user.strip()
    except Exception:
        pass
    return host, user


def prompt_ssh_secret(
    host: object,
    *,
    title: str,
    prompt: str,
    qinputdialog_cls: type[object],
    qlineedit_cls: type[object],
) -> tuple[str, bool]:
    """Prompt the user for an SSH secret/response."""
    secret, ok = qinputdialog_cls.getText(
        host,
        title,
        prompt,
        qlineedit_cls.Password,
    )
    if not ok:
        host._show_status_message("SSH login cancelled by user.", is_error=True)
        return "", False
    if not secret:
        host._show_status_message("SSH secret is required for this host.", is_error=True)
        return "", False
    return secret, True


def prepare_ssh_config_for_auth(
    host: object,
    config: dict[str, object],
    *,
    default_probe_ssh_auth_methods_fn,
    default_prompt_ssh_secret_fn,
    validate_ssh_secret_fn,
    parse_proxy_jump_target_fn,
    resolve_ssh_alias_fn,
    qmessagebox_cls: type[object],
) -> dict[str, object] | None:
    """Inject transient SSH password credentials when preflight indicates a challenge."""
    if str(config.get("protocol", "")).upper() != "SSH":
        return config

    remote = config.get("remote")
    if not isinstance(remote, dict):
        return config

    details = remote.get("details")
    detail_map = dict(details) if isinstance(details, dict) else {}

    hostname_raw = detail_map.get("hostname") or remote.get("hostname")
    username_raw = detail_map.get("user") or remote.get("user")
    hostname = str(hostname_raw).strip() if isinstance(hostname_raw, str) else ""
    username = str(username_raw).strip() if isinstance(username_raw, str) else ""
    if not hostname or not username:
        return config

    updated_remote = dict(remote)
    updated_details = dict(detail_map)

    probe_fn = getattr(host, "_probe_ssh_auth_methods", None)
    if not callable(probe_fn):
        probe_fn = default_probe_ssh_auth_methods_fn

    prompt_fn = getattr(host, "_prompt_ssh_secret", None)
    if not callable(prompt_fn):
        prompt_fn = lambda **kwargs: default_prompt_ssh_secret_fn(host, **kwargs)

    target_auth_methods = probe_fn(hostname, username)
    target_needs_secret = bool(target_auth_methods) and (
        "password" in target_auth_methods or "keyboard-interactive" in target_auth_methods
    )

    if target_needs_secret:
        cache_key = f"{username}@{hostname}:22"
        secret = host._ssh_session_passwords.get(cache_key, "")
        requires_otp_style = bool(target_auth_methods) and (
            "keyboard-interactive" in target_auth_methods and "password" not in target_auth_methods
        )

        if secret and not requires_otp_style:
            validation = validate_ssh_secret_fn(hostname, username, secret, port=22)
            if validation is False:
                host._ssh_session_passwords.pop(cache_key, None)
                secret = ""

        if not secret:
            prompt = f"Enter SSH secret for {username}@{hostname}"
            if requires_otp_style:
                prompt = f"Enter one-time code or challenge response for {username}@{hostname}"

            attempts = 2
            for _ in range(attempts):
                entered, ok = prompt_fn(
                    title="SSH Authentication Required",
                    prompt=prompt,
                )
                if not ok:
                    return None

                validation = validate_ssh_secret_fn(hostname, username, entered, port=22)
                if validation is False:
                    qmessagebox_cls.warning(
                        host,
                        "SSH Authentication Failed",
                        "Authentication failed for the provided SSH secret. Please try again.",
                    )
                    continue

                secret = entered
                break

            if not secret:
                return None

        host._ssh_session_passwords[cache_key] = secret
        updated_details["password"] = secret
        updated_remote["password"] = secret

    proxy_jump_raw = updated_details.get("proxyjump") or updated_remote.get("proxyjump")
    proxy_jump = str(proxy_jump_raw).strip() if isinstance(proxy_jump_raw, str) else ""
    if proxy_jump:
        jump_user_hint, jump_alias, jump_port = parse_proxy_jump_target_fn(proxy_jump)
        if jump_alias:
            jump_host, jump_user_cfg = resolve_ssh_alias_fn(jump_alias)
            jump_user = (jump_user_hint or jump_user_cfg or username).strip()

            jump_auth_methods = probe_fn(jump_host, jump_user, port=jump_port)
            jump_needs_secret = bool(jump_auth_methods) and (
                "password" in jump_auth_methods or "keyboard-interactive" in jump_auth_methods
            )

            if jump_needs_secret:
                jump_cache_key = f"jump:{jump_user}@{jump_host}:{jump_port}"
                jump_secret = host._ssh_session_passwords.get(jump_cache_key, "")
                jump_requires_otp_style = bool(jump_auth_methods) and (
                    "keyboard-interactive" in jump_auth_methods and "password" not in jump_auth_methods
                )

                if jump_secret and not jump_requires_otp_style:
                    validation = validate_ssh_secret_fn(
                        jump_host,
                        jump_user,
                        jump_secret,
                        port=jump_port,
                    )
                    if validation is False:
                        host._ssh_session_passwords.pop(jump_cache_key, None)
                        jump_secret = ""

                if not jump_secret:
                    prompt = (
                        f"Authenticating with bastion host {jump_host} "
                        f"before proxyjump to {hostname}.\n\n"
                        f"Enter bastion SSH secret for {jump_user}@{jump_host}"
                    )
                    if jump_requires_otp_style:
                        prompt = (
                            f"Authenticating with bastion host {jump_host} "
                            f"before proxyjump to {hostname}.\n\n"
                            f"Enter bastion one-time code or challenge response for {jump_user}@{jump_host}"
                        )

                    attempts = 2
                    for _ in range(attempts):
                        entered, ok = prompt_fn(
                            title="Bastion Authentication Required",
                            prompt=prompt,
                        )
                        if not ok:
                            return None

                        validation = validate_ssh_secret_fn(
                            jump_host,
                            jump_user,
                            entered,
                            port=jump_port,
                        )
                        if validation is False:
                            qmessagebox_cls.warning(
                                host,
                                "Bastion Authentication Failed",
                                "Authentication failed for the provided bastion secret. Please try again.",
                            )
                            continue

                        jump_secret = entered
                        break

                    if not jump_secret:
                        return None

                host._ssh_session_passwords[jump_cache_key] = jump_secret
                updated_details["proxyjump_password"] = jump_secret
                updated_details["proxyjump_user"] = jump_user
                updated_remote["proxyjump_password"] = jump_secret
                updated_remote["proxyjump_user"] = jump_user

    updated_remote["details"] = updated_details

    updated_config = dict(config)
    updated_config["remote"] = updated_remote
    return updated_config


def is_ssh_auth_failure_message(message: str) -> bool:
    """Return True when a worker prepare failure message looks like SSH auth failure."""
    text = (message or "").strip().lower()
    if not text:
        return False
    markers = (
        "authentication",
        "bad authentication type",
        "auth fail",
        "permission denied",
        "keyboard-interactive",
        "auth",
    )
    return any(marker in text for marker in markers)


def clear_ssh_cached_secrets_for_config(
    host: object,
    config: dict[str, object],
    *,
    parse_proxy_jump_target_fn,
    resolve_ssh_alias_fn,
) -> None:
    """Forget cached SSH secrets for target and bastion hosts in a config."""
    if str(config.get("protocol", "")).upper() != "SSH":
        return

    remote = config.get("remote")
    if not isinstance(remote, dict):
        return

    details = remote.get("details")
    detail_map = dict(details) if isinstance(details, dict) else {}

    hostname_raw = detail_map.get("hostname") or remote.get("hostname")
    username_raw = detail_map.get("user") or remote.get("user")
    hostname = str(hostname_raw).strip() if isinstance(hostname_raw, str) else ""
    username = str(username_raw).strip() if isinstance(username_raw, str) else ""
    if hostname and username:
        host._ssh_session_passwords.pop(f"{username}@{hostname}:22", None)

    proxy_jump_raw = detail_map.get("proxyjump") or remote.get("proxyjump")
    proxy_jump = str(proxy_jump_raw).strip() if isinstance(proxy_jump_raw, str) else ""
    if proxy_jump:
        jump_user_hint, jump_alias, jump_port = parse_proxy_jump_target_fn(proxy_jump)
        if jump_alias:
            jump_host, jump_user_cfg = resolve_ssh_alias_fn(jump_alias)
            jump_user = (jump_user_hint or jump_user_cfg or username).strip()
            if jump_host and jump_user:
                host._ssh_session_passwords.pop(f"jump:{jump_user}@{jump_host}:{jump_port}", None)


def maybe_retry_ssh_authentication(
    host: object,
    config: dict[str, object],
    failure_message: str,
    *,
    is_ssh_auth_failure_message_fn,
    qmessagebox_cls: type[object],
) -> bool:
    """Offer auth retry for SSH prepare failures that look like authentication problems."""
    if str(config.get("protocol", "")).upper() != "SSH":
        return False
    if not is_ssh_auth_failure_message_fn(failure_message):
        return False

    host._clear_ssh_cached_secrets_for_config(config)
    choice = qmessagebox_cls.question(
        host,
        "SSH Authentication Failed",
        "SSH authentication failed. Retry with new credentials/response?",
        qmessagebox_cls.Retry | qmessagebox_cls.Cancel,
        qmessagebox_cls.Retry,
    )
    return choice == qmessagebox_cls.Retry
