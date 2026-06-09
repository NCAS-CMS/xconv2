"""Remote-related helper operations extracted from CFVMain."""

from __future__ import annotations

from typing import Callable
from urllib.parse import unquote, urlparse


def resolve_remote_uri(
    host: object,
    uri: str,
    *,
    canonical_remote_uri: Callable[[str], str],
) -> tuple[dict[str, object] | None, str, str, bool]:
    """Resolve URI into (config, remote_path, host_alias, unknown_host)."""
    from ..ui.dialogs import RemoteConfigurationDialog  # noqa: PLC0415

    canonical_uri = canonical_remote_uri(uri)
    parsed = urlparse(canonical_uri)
    scheme = parsed.scheme.lower()

    if scheme == "s3":
        locations = RemoteConfigurationDialog._load_s3_locations()

        endpoint_to_alias: dict[str, str] = {}
        for alias_name, details in locations.items():
            if not isinstance(alias_name, str) or not isinstance(details, dict):
                continue
            endpoint_url = str(details.get("url", "")).strip()
            endpoint_host = urlparse(endpoint_url).netloc.strip()
            if endpoint_host:
                endpoint_to_alias[endpoint_host] = alias_name

        netloc = parsed.netloc.strip()
        endpoint_alias = endpoint_to_alias.get(netloc, "")
        if endpoint_alias:
            path = parsed.path.lstrip("/")
        else:
            path = f"{parsed.netloc}{parsed.path}".lstrip("/")

        aliases = getattr(host, "_settings", {}).get("recent_uri_aliases")
        alias_map = aliases if isinstance(aliases, dict) else {}
        preferred_alias = alias_map.get(canonical_uri) or alias_map.get(uri)
        if not isinstance(preferred_alias, str):
            preferred_alias = ""
        preferred_alias = preferred_alias.strip()

        if endpoint_alias:
            preferred_alias = endpoint_alias

        if not preferred_alias:
            raw_state = getattr(host, "_settings", {}).get("last_remote_configuration")
            state = raw_state if isinstance(raw_state, dict) else {}
            candidate = state.get("s3_existing_alias")
            if isinstance(candidate, str) and candidate.strip():
                preferred_alias = candidate.strip()

        chosen_alias = preferred_alias if preferred_alias in locations else ""
        if not chosen_alias and len(locations) == 1:
            chosen_alias = next(iter(locations.keys()))

        details = dict(locations.get(chosen_alias, {})) if chosen_alias else {}
        config: dict[str, object] = {
            "protocol": "S3",
            "remote": {
                "mode": "Select from existing",
                "alias": chosen_alias or "S3",
                "details": details,
            },
        }
        return config, path, chosen_alias or "S3", False

    if scheme == "ssh":
        host_name = (parsed.hostname or parsed.netloc or "").strip()
        user = (parsed.username or "").strip()
        remote_path = unquote(parsed.path or "").lstrip("/")
        if not remote_path:
            remote_path = "."
        hosts = RemoteConfigurationDialog._load_ssh_hosts()

        runtime_preferences: dict[str, object] = {}
        configured_state = getattr(host, "_settings", {}).get("last_remote_configuration")
        if isinstance(configured_state, dict):
            configured_prefs = configured_state.get("ssh_runtime_preferences")
            if isinstance(configured_prefs, dict):
                runtime_preferences.update(configured_prefs)
        open_state = getattr(host, "_settings", {}).get("last_remote_open")
        if isinstance(open_state, dict):
            open_prefs = open_state.get("ssh_runtime_preferences")
            if isinstance(open_prefs, dict):
                runtime_preferences.update(open_prefs)

        matched_alias = ""
        matched_details: dict[str, object] | None = None
        for alias, details in hosts.items():
            if alias == host_name or str(details.get("hostname", "")) == host_name:
                matched_alias = alias
                matched_details = dict(details)
                break

        if matched_details is None:
            return None, remote_path, host_name or "SSH", True

        if user and not matched_details.get("user"):
            matched_details["user"] = user

        alias_prefs = runtime_preferences.get(matched_alias)
        if isinstance(alias_prefs, dict):
            remote_python = alias_prefs.get("remote_python")
            if isinstance(remote_python, str) and remote_python.strip():
                matched_details["remote_python"] = remote_python.strip()

            remote_python_options = alias_prefs.get("remote_python_options")
            if isinstance(remote_python_options, dict):
                cleaned_options = {
                    str(label): str(command)
                    for label, command in remote_python_options.items()
                    if str(label).strip() and str(command).strip()
                }
                if cleaned_options:
                    matched_details["remote_python_options"] = cleaned_options
            elif isinstance(remote_python_options, list):
                cleaned_options = {
                    str(item): str(item)
                    for item in remote_python_options
                    if str(item).strip()
                }
                if cleaned_options:
                    matched_details["remote_python_options"] = cleaned_options

            if "login_shell" in alias_prefs:
                login_shell_value = alias_prefs.get("login_shell")
                if isinstance(login_shell_value, str):
                    matched_details["login_shell"] = login_shell_value.strip().lower() in {"1", "true", "yes", "on"}
                else:
                    matched_details["login_shell"] = bool(login_shell_value)

        config = {
            "protocol": "SSH",
            "remote": {
                "mode": "Select from existing",
                "alias": matched_alias,
                "details": matched_details,
            },
        }
        return config, remote_path, matched_alias, False

    if scheme in {"http", "https"}:
        https_locations = getattr(host, "_settings", {}).get("remote_https_locations")
        locations = dict(https_locations) if isinstance(https_locations, dict) else {}
        if not locations:
            cfg_state = getattr(host, "_settings", {}).get("last_remote_configuration")
            if isinstance(cfg_state, dict):
                raw = cfg_state.get("https_locations")
                if isinstance(raw, dict):
                    locations = dict(raw)

        matched_alias = ""
        matched_url = ""
        for alias, details in locations.items():
            if not isinstance(details, dict):
                continue
            base_url = str(details.get("url") or details.get("base_url") or "").strip()
            if base_url and uri.startswith(base_url):
                if len(base_url) > len(matched_url):
                    matched_alias = str(alias)
                    matched_url = base_url

        remote_path = unquote(parsed.path or "/")
        if not matched_alias:
            return None, remote_path, (parsed.hostname or "HTTPS"), True

        config = {
            "protocol": "HTTPS",
            "remote": {
                "mode": "Select from existing",
                "alias": matched_alias,
                "details": locations.get(matched_alias, {}),
            },
        }
        return config, remote_path, matched_alias, False

    return None, "", "", False
