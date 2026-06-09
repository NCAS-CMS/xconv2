"""Remote open/configure/browse flow helpers extracted from CFVMain."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse


def open_remote_from_config(
    host: object,
    config: dict[str, object],
    *,
    with_cache_defaults_fn: Callable[[dict[str, object]], dict[str, object]],
    qeventloop_cls: type[object],
    qapplication_cls: type[object],
    qdialog_accepted_value: int,
    qmessagebox_cls: type[object],
) -> None:
    """Perform remote login once in the worker, then navigate via IPC."""
    from ..remote_access import (  # noqa: PLC0415
        build_remote_filesystem_spec, remote_descriptor_hash, spec_to_descriptor,
    )
    from ..ui.remote_file_navigator import RemoteFileNavigatorDialog, RemoteLoginLogDialog  # noqa: PLC0415

    if not isinstance(config, dict):
        return

    config = with_cache_defaults_fn(config)

    prepared_config = host._prepare_ssh_config_for_auth(config)
    if prepared_config is None:
        return
    config = prepared_config

    if str(config.get("protocol", "")).upper() in {"HTTP", "HTTPS"}:
        http_locations = host._settings.get("remote_https_locations")
        if not isinstance(http_locations, dict):
            http_locations = host._settings.get("remote_http_locations")
        if isinstance(http_locations, dict):
            updated = dict(http_locations)
        else:
            updated = {}

        remote = config.get("remote")
        if isinstance(remote, dict):
            alias = str(remote.get("alias", "")).strip()
            details = remote.get("details")
            if alias and isinstance(details, dict):
                url = details.get("url") or details.get("base_url")
                if isinstance(url, str) and url.strip():
                    updated[alias] = {"url": url.strip()}

        host._settings["remote_https_locations"] = updated

    try:
        spec = build_remote_filesystem_spec(config)
    except Exception as exc:
        qmessagebox_cls.critical(host, "Remote configuration invalid", str(exc))
        return

    if getattr(host, "file_open_mode", "single") != "multi":
        host._clear_loaded_data_views()

    descriptor = spec_to_descriptor(spec, cache=config.get("cache") if isinstance(config, dict) else None)
    descriptor_hash = remote_descriptor_hash(descriptor)
    host._last_remote_config = config
    host._last_remote_navigator_state = None

    reuse_active_session = bool(host._remote_session_id) and host._remote_descriptor_hash == descriptor_hash
    if reuse_active_session:
        host._remote_descriptor = descriptor
        host._show_status_message("Reusing active remote session.")
    else:
        session_id = uuid.uuid4().hex
        host._remote_session_id = session_id
        host._remote_descriptor_hash = descriptor_hash
        host._remote_descriptor = descriptor

    if not reuse_active_session:
        log_dialog = RemoteLoginLogDialog(host, spec.display_name)
        host._pending_prepare_log_dialog = log_dialog
        host._pending_prepare_loop = qeventloop_cls()
        host._pending_prepare_loop_ok = False
        log_dialog.show()
        qapplication_cls.processEvents()

        host._send_worker_control_task(
            "REMOTE_PREPARE",
            {
                "session_id": host._remote_session_id,
                "descriptor_hash": descriptor_hash,
                "descriptor": descriptor,
            },
        )
        host._pending_prepare_failure_message = ""
        host._pending_prepare_loop.exec()
        host._pending_prepare_log_dialog = None

        if not host._pending_prepare_loop_ok:
            log_dialog.exec()
            failure_message = host._pending_prepare_failure_message
            if host._maybe_retry_ssh_authentication(config, failure_message):
                host._open_remote_from_config(config)
                return
            return

        log_dialog.close()

    list_callback = host._make_worker_list_callback()
    dialog = RemoteFileNavigatorDialog(
        host,
        config,
        spec=spec,
        list_callback=list_callback,
        new_remote_button=True,
        session_active=bool(host._remote_session_id),
    )
    result = dialog.exec()
    host._last_remote_navigator_state = dialog._collect_tree_state()
    if dialog.shutdown_session_requested:
        host._release_remote_session_if_active()
        host._show_status_message("Remote session shut down.")
        return
    if dialog.new_remote_requested:
        host._choose_remote()
        return
    if result != qdialog_accepted_value:
        return

    selected_uri = dialog.selected_uri()
    selected_path = dialog.selected_path()
    if not selected_uri or not selected_path:
        host._show_status_message("Remote file selection was incomplete.", is_error=True)
        return

    remote = config.get("remote") if isinstance(config, dict) else None
    host_alias = str(remote.get("alias", "")).strip() if isinstance(remote, dict) else ""
    host._set_window_title_for_file(selected_uri)
    host._show_status_message(f"Selected remote file: {selected_uri}")
    if host_alias:
        host._record_recent_uri(selected_uri, host_alias)
    else:
        host._record_recent_file(selected_uri)
    host._load_remote_selected_file(selected_uri, selected_path)


def open_remote_uri_direct(
    host: object,
    *,
    uri: str,
    remote_path: str,
    config: dict[str, object],
    host_alias: str,
    with_cache_defaults_fn: Callable[[dict[str, object]], dict[str, object]],
    qeventloop_cls: type[object],
    qapplication_cls: type[object],
    qmessagebox_cls: type[object],
) -> None:
    """Open a specific remote URI directly without launching the navigator dialog."""
    from ..remote_access import (  # noqa: PLC0415
        build_remote_filesystem_spec, remote_descriptor_hash, spec_to_descriptor,
    )
    from ..ui.remote_file_navigator import RemoteLoginLogDialog  # noqa: PLC0415

    if not isinstance(config, dict):
        return

    config = with_cache_defaults_fn(config)

    prepared_config = host._prepare_ssh_config_for_auth(config)
    if prepared_config is None:
        return
    config = prepared_config

    if str(config.get("protocol", "")).upper() in {"HTTP", "HTTPS"} and host_alias:
        details = {}
        remote = config.get("remote")
        if isinstance(remote, dict):
            raw_details = remote.get("details")
            if isinstance(raw_details, dict):
                details = dict(raw_details)
            if not details and isinstance(remote.get("url"), str):
                details = {"url": str(remote.get("url"))}

        https_locations = host._settings.get("remote_https_locations")
        merged = dict(https_locations) if isinstance(https_locations, dict) else {}
        if details:
            merged[host_alias] = details
        host._settings["remote_https_locations"] = merged

    try:
        spec = build_remote_filesystem_spec(config)
    except Exception as exc:
        qmessagebox_cls.critical(host, "Remote configuration invalid", str(exc))
        return

    if getattr(host, "file_open_mode", "single") != "multi":
        host._clear_loaded_data_views()
    descriptor = spec_to_descriptor(spec, cache=config.get("cache") if isinstance(config, dict) else None)
    descriptor_hash = remote_descriptor_hash(descriptor)
    host._last_remote_config = config
    host._last_remote_navigator_state = None

    reuse_active_session = bool(host._remote_session_id) and host._remote_descriptor_hash == descriptor_hash
    if reuse_active_session:
        host._remote_descriptor = descriptor
        host._show_status_message("Reusing active remote session.")
        host._set_window_title_for_file(uri)
        host._show_status_message(f"Selected remote file: {uri}")
        host._record_recent_uri(uri, host_alias or spec.display_name)
        host._load_remote_selected_file(uri, remote_path)
        return

    session_id = uuid.uuid4().hex
    host._remote_session_id = session_id
    host._remote_descriptor_hash = descriptor_hash
    host._remote_descriptor = descriptor

    log_dialog = RemoteLoginLogDialog(host, spec.display_name)
    host._pending_prepare_log_dialog = log_dialog
    host._pending_prepare_loop = qeventloop_cls()
    host._pending_prepare_loop_ok = False
    log_dialog.show()
    qapplication_cls.processEvents()

    host._send_worker_control_task(
        "REMOTE_PREPARE",
        {
            "session_id": session_id,
            "descriptor_hash": descriptor_hash,
            "descriptor": descriptor,
        },
    )
    host._pending_prepare_failure_message = ""
    host._pending_prepare_loop.exec()
    host._pending_prepare_log_dialog = None

    if not host._pending_prepare_loop_ok:
        log_dialog.exec()
        failure_message = host._pending_prepare_failure_message
        if host._maybe_retry_ssh_authentication(config, failure_message):
            host._open_remote_uri_direct(
                uri=uri,
                remote_path=remote_path,
                config=config,
                host_alias=host_alias,
            )
            return
        return

    log_dialog.close()
    host._set_window_title_for_file(uri)
    host._show_status_message(f"Selected remote file: {uri}")
    host._record_recent_uri(uri, host_alias or spec.display_name)
    host._load_remote_selected_file(uri, remote_path)


def configure_remote_for_uri(
    host: object,
    uri: str,
    *,
    remote_configuration_dialog_cls: type[object],
) -> None:
    """Open Configure Remote pre-populated for URI-driven add-new workflows."""
    parsed = urlparse(uri)
    scheme = parsed.scheme.lower()
    raw_state = host._settings.get("last_remote_configuration", {})
    state = dict(raw_state) if isinstance(raw_state, dict) else {}
    https_locations = host._settings.get("remote_https_locations")
    if isinstance(https_locations, dict):
        state["https_locations"] = dict(https_locations)
    s3_reductionist_locations = host._settings.get("remote_s3_reductionist_locations")
    if isinstance(s3_reductionist_locations, dict):
        state["s3_reductionist_locations"] = dict(s3_reductionist_locations)

    if scheme in {"http", "https"}:
        state.update(
            {
                "protocol_index": 1,
                "https_mode": "Add new",
                "https_alias": (parsed.hostname or "https").strip(),
                "https_url": f"{scheme}://{parsed.netloc}",
            }
        )
    elif scheme == "ssh":
        state.update(
            {
                "protocol_index": 2,
                "ssh_mode": "Add new",
                "ssh_alias": (parsed.hostname or parsed.netloc or "ssh").strip(),
                "ssh_hostname": (parsed.hostname or parsed.netloc or "").strip(),
                "ssh_user": (parsed.username or "").strip(),
            }
        )

    def _on_finished_uri(config: dict | None, _ok: bool, next_state: dict) -> None:
        host._settings["last_remote_configuration"] = next_state
        if isinstance(next_state, dict):
            persisted_https = next_state.get("https_locations")
            if isinstance(persisted_https, dict):
                host._settings["remote_https_locations"] = dict(persisted_https)
            persisted_s3_reductionist = next_state.get("s3_reductionist_locations")
            if isinstance(persisted_s3_reductionist, dict):
                host._settings["remote_s3_reductionist_locations"] = {
                    str(alias).strip(): str(url).strip()
                    for alias, url in persisted_s3_reductionist.items()
                    if str(alias).strip() and str(url).strip()
                }
        host._save_settings()

    remote_configuration_dialog_cls.show_non_modal(host, state=state, on_finished=_on_finished_uri)


def open_uri_entry(
    host: object,
    uri: str,
    *,
    from_uri_dialog: bool,
    canonical_remote_uri: Callable[[str], str],
    qmessagebox_cls: type[object],
) -> None:
    """Open a URI from user input or recent list."""
    canonical_uri = canonical_remote_uri(uri)
    parsed = urlparse(canonical_uri)
    scheme = parsed.scheme.lower()

    if not scheme:
        host._open_recent_file(canonical_uri)
        return

    if scheme not in {"s3", "ssh", "http", "https"}:
        qmessagebox_cls.critical(host, "Unsupported URI", f"Unsupported URI protocol: {scheme}")
        return

    config, remote_path, host_alias, unknown_host = host._resolve_remote_uri(canonical_uri)
    if unknown_host and from_uri_dialog:
        host._configure_remote_for_uri(canonical_uri)
        config, remote_path, host_alias, _unknown_host_after = host._resolve_remote_uri(canonical_uri)

    if config is None:
        qmessagebox_cls.critical(host, "Unknown host", "Host route is not known. Configure a remote first.")
        return

    host._open_remote_uri_direct(
        uri=canonical_uri,
        remote_path=remote_path,
        config=config,
        host_alias=host_alias,
    )


def configure_remote(
    host: object,
    *,
    remote_configuration_dialog_cls: type[object],
) -> None:
    """Open the full remote configuration dialog non-modally."""
    raw_state = host._settings.get("last_remote_configuration", {})
    state = dict(raw_state) if isinstance(raw_state, dict) else {}
    https_locations = host._settings.get("remote_https_locations")
    if not isinstance(https_locations, dict):
        https_locations = host._settings.get("remote_http_locations")
    if isinstance(https_locations, dict) and https_locations:
        state["https_locations"] = dict(https_locations)
    s3_reductionist_locations = host._settings.get("remote_s3_reductionist_locations")
    if isinstance(s3_reductionist_locations, dict) and s3_reductionist_locations:
        state["s3_reductionist_locations"] = dict(s3_reductionist_locations)

    def _on_finished(config: dict | None, ok: bool, next_state: dict) -> None:
        host._settings["last_remote_configuration"] = next_state
        if isinstance(next_state, dict):
            persisted_https = next_state.get("https_locations")
            if not isinstance(persisted_https, dict):
                persisted_https = next_state.get("http_locations")
            if isinstance(persisted_https, dict):
                host._settings["remote_https_locations"] = dict(persisted_https)
            persisted_s3_reductionist = next_state.get("s3_reductionist_locations")
            if isinstance(persisted_s3_reductionist, dict):
                host._settings["remote_s3_reductionist_locations"] = {
                    str(alias).strip(): str(url).strip()
                    for alias, url in persisted_s3_reductionist.items()
                    if str(alias).strip() and str(url).strip()
                }
        host._save_settings()
        if not ok or config is None:
            return
        host._open_remote_from_config(config)

    remote_configuration_dialog_cls.show_non_modal(host, state=state, on_finished=_on_finished)


def choose_remote(
    host: object,
    *,
    remote_open_dialog_cls: type[object],
    with_cache_defaults_fn: Callable[[dict[str, object]], dict[str, object]],
) -> None:
    """Open using existing short names via a streamlined protocol picker dialog."""
    raw_state = host._settings.get("last_remote_open", {})
    state = raw_state if isinstance(raw_state, dict) else {}
    if isinstance(state, dict):
        merged_http: dict[str, object] = {}
        merged_ssh_runtime_preferences: dict[str, object] = {}
        merged_s3_reductionist: dict[str, str] = {}

        configured_state = host._settings.get("last_remote_configuration")
        if isinstance(configured_state, dict):
            cfg_http = configured_state.get("https_locations")
            if not isinstance(cfg_http, dict):
                cfg_http = configured_state.get("http_locations")
            if isinstance(cfg_http, dict):
                merged_http.update(cfg_http)
            cfg_ssh_prefs = configured_state.get("ssh_runtime_preferences")
            if isinstance(cfg_ssh_prefs, dict):
                merged_ssh_runtime_preferences.update(cfg_ssh_prefs)

        http_locations = host._settings.get("remote_https_locations")
        if not isinstance(http_locations, dict):
            http_locations = host._settings.get("remote_http_locations")
        if isinstance(http_locations, dict):
            merged_http.update(http_locations)

        persisted_s3_reductionist = host._settings.get("remote_s3_reductionist_locations")
        if isinstance(persisted_s3_reductionist, dict):
            merged_s3_reductionist.update(
                {
                    str(alias).strip(): str(url).strip()
                    for alias, url in persisted_s3_reductionist.items()
                    if str(alias).strip() and str(url).strip()
                }
            )

        open_state_ssh_prefs = state.get("ssh_runtime_preferences") if isinstance(state, dict) else None
        if isinstance(open_state_ssh_prefs, dict):
            merged_ssh_runtime_preferences.update(open_state_ssh_prefs)

        if merged_http or merged_ssh_runtime_preferences or merged_s3_reductionist:
            state = dict(state)
            if merged_http:
                state["https_locations"] = dict(merged_http)
            if merged_ssh_runtime_preferences:
                state["ssh_runtime_preferences"] = dict(merged_ssh_runtime_preferences)
            if merged_s3_reductionist:
                state["s3_reductionist_locations"] = dict(merged_s3_reductionist)

    config, ok, next_state = remote_open_dialog_cls.get_configuration(host, state=state)
    host._settings["last_remote_open"] = next_state
    host._save_settings()
    if isinstance(next_state, dict) and bool(next_state.get("configure_new_remote")):
        host._configure_remote()
        return
    if not ok or config is None:
        return
    config = with_cache_defaults_fn(config)
    host._open_remote_from_config(config)


def browse_remote(
    host: object,
    *,
    qdialog_accepted_value: int,
    qmessagebox_cls: type[object],
) -> None:
    """Re-browse active remote session or prompt for a new one."""
    if host._remote_session_id and host._last_remote_config:
        from ..remote_access import build_remote_filesystem_spec  # noqa: PLC0415
        from ..ui.remote_file_navigator import RemoteFileNavigatorDialog  # noqa: PLC0415
        try:
            spec = build_remote_filesystem_spec(host._last_remote_config)
        except Exception as exc:
            qmessagebox_cls.critical(host, "Remote configuration invalid", str(exc))
            return
        list_callback = host._make_worker_list_callback()
        dialog = RemoteFileNavigatorDialog(
            host,
            host._last_remote_config,
            spec=spec,
            list_callback=list_callback,
            new_remote_button=True,
            session_active=bool(host._remote_session_id),
            initial_tree_state=host._last_remote_navigator_state,
        )
        result = dialog.exec()
        host._last_remote_navigator_state = dialog._collect_tree_state()
        if dialog.shutdown_session_requested:
            host._release_remote_session_if_active()
            host._show_status_message("Remote session shut down.")
            return
        if dialog.new_remote_requested:
            host._choose_remote()
            return
        if result != qdialog_accepted_value:
            return
        selected_uri = dialog.selected_uri()
        selected_path = dialog.selected_path()
        if not selected_uri or not selected_path:
            host._show_status_message("Remote file selection was incomplete.", is_error=True)
            return
        remote = host._last_remote_config.get("remote") if isinstance(host._last_remote_config, dict) else None
        host_alias = str(remote.get("alias", "")).strip() if isinstance(remote, dict) else ""
        host._set_window_title_for_file(selected_uri)
        host._show_status_message(f"Selected remote file: {selected_uri}")
        if host_alias:
            host._record_recent_uri(selected_uri, host_alias)
        else:
            host._record_recent_file(selected_uri)
        host._load_remote_selected_file(selected_uri, selected_path)
    else:
        host._choose_remote()


def choose_uris(
    host: object,
    *,
    open_uri_dialog_cls: type[object],
) -> None:
    """Show URI dialog and open supported URIs through the worker."""
    default_uri = host._default_open_uri_value()
    uri, ok, quit_requested = open_uri_dialog_cls.get_uri(host, default_uri=default_uri)
    if quit_requested:
        return
    if not ok:
        return
    host._open_uri_entry(uri, from_uri_dialog=True)


def open_recent_file(
    host: object,
    file_path: str,
    *,
    super_open_recent_file: Callable[[str], None],
) -> None:
    """Open recent entry, routing remote URIs via URI resolution flow."""
    if urlparse(file_path).scheme:
        host._open_uri_entry(file_path, from_uri_dialog=False)
        return
    super_open_recent_file(file_path)


def make_worker_list_callback(host: object, *, qeventloop_cls: type[object]) -> Callable[[str], list[Any]]:
    """Return callable that lists a remote directory via worker IPC using nested event loop."""

    def list_dir(path: str) -> list[Any]:
        loop = qeventloop_cls()
        host._pending_list_loop = loop
        host._pending_list_result = None
        host._send_worker_control_task(
            "REMOTE_LIST",
            {
                "session_id": host._remote_session_id,
                "descriptor_hash": host._remote_descriptor_hash,
                "descriptor": host._remote_descriptor,
                "path": path,
            },
        )
        loop.exec()
        result = host._pending_list_result
        host._pending_list_loop = None
        host._pending_list_result = None
        if result is None:
            raise RuntimeError(f"No response from worker for directory listing of {path!r}")
        error = result.get("error")
        if error:
            raise RuntimeError(str(error))
        return list(result.get("entries", []))

    return list_dir
