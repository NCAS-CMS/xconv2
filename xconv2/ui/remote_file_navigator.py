from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Callable

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from xconv2.cache_utils import parse_disk_expiry_seconds, prune_disk_cache


logger = logging.getLogger(__name__)
_TRACE_REMOTE_FS = os.getenv("XCONV2_REMOTE_FS_TRACE", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_TRACE_REMOTE_FILE_IO = os.getenv("XCONV2_REMOTE_FS_TRACE_FILE_IO", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


class _ConfiguredRemoteFileSystem:
    """Proxy filesystem that injects default open kwargs for read caching."""

    def __init__(self, filesystem: Any, *, open_defaults: dict[str, Any]) -> None:
        self._filesystem = filesystem
        self._open_defaults = dict(open_defaults)
        self.protocol = getattr(filesystem, "protocol", None)

    def open(self, path: str, mode: str = "rb", **kwargs: Any):
        """Open a path while applying configured cache defaults for reads."""
        return self._filesystem.open(path, mode=mode, **self._merge_open_kwargs(mode, kwargs))

    def _open(self, path: str, mode: str = "rb", **kwargs: Any):
        """Delegate low-level open while applying configured cache defaults for reads."""
        return self._filesystem._open(path, mode=mode, **self._merge_open_kwargs(mode, kwargs))

    def _merge_open_kwargs(self, mode: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        if "r" not in mode:
            return kwargs
        merged = dict(self._open_defaults)
        merged.update(kwargs)
        return merged

    def __getattr__(self, name: str) -> Any:
        return getattr(self._filesystem, name)


class _LoggingFileHandleProxy:
    """Log file-handle operations on the underlying remote filesystem."""

    def __init__(self, handle: Any, *, label: str, path: str) -> None:
        self._handle = handle
        self._label = label
        self._path = path

    def read(self, *args: Any, **kwargs: Any):
        started = time.perf_counter()
        result = self._handle.read(*args, **kwargs)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        size = len(result) if isinstance(result, (bytes, bytearray, memoryview, str)) else "na"
        logger.info(
            "REMOTE_FS file_read label=%s path=%r request=%r size=%s elapsed_ms=%d",
            self._label,
            self._path,
            args[0] if args else None,
            size,
            elapsed_ms,
        )
        return result

    def seek(self, *args: Any, **kwargs: Any):
        started = time.perf_counter()
        result = self._handle.seek(*args, **kwargs)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "REMOTE_FS file_seek label=%s path=%r args=%r kwargs=%r elapsed_ms=%d result=%r",
            self._label,
            self._path,
            args,
            kwargs,
            elapsed_ms,
            result,
        )
        return result

    def tell(self, *args: Any, **kwargs: Any):
        return self._handle.tell(*args, **kwargs)

    def close(self) -> Any:
        logger.info("REMOTE_FS file_close label=%s path=%r", self._label, self._path)
        return self._handle.close()

    def __enter__(self):
        self._handle.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._handle.__exit__(exc_type, exc, tb)

    def __iter__(self):
        return iter(self._handle)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._handle, name)


def _wrap_filesystem_with_logging(filesystem: Any, *, label: str) -> Any:
    """Return *filesystem* with logging wrappers injected on key I/O methods.

    Rather than using a proxy class (which breaks fsspec's BlockCache because it
    does ``getattr(type(self.fs), method)`` class-level lookups), we dynamically
    subclass the *actual* filesystem type and swizzle the instance's ``__class__``.
    Every attribute not explicitly overridden here is inherited correctly.
    """
    if not _TRACE_REMOTE_FS:
        return filesystem

    base_cls = type(filesystem)

    def _open(self, path: str, mode: str = "rb", **kwargs: Any):
        started = time.perf_counter()
        handle = base_cls._open(self, path, mode=mode, **kwargs)
        logger.info(
            "REMOTE_FS _open label=%s path=%r mode=%s elapsed_ms=%d",
            label, path, mode, int((time.perf_counter() - started) * 1000),
        )
        if _TRACE_REMOTE_FILE_IO:
            return _LoggingFileHandleProxy(handle, label=label, path=path)
        return handle

    def open(self, path: str, mode: str = "rb", **kwargs: Any):
        started = time.perf_counter()
        handle = base_cls.open(self, path, mode=mode, **kwargs)
        logger.info(
            "REMOTE_FS open label=%s path=%r mode=%s elapsed_ms=%d",
            label, path, mode, int((time.perf_counter() - started) * 1000),
        )
        if _TRACE_REMOTE_FILE_IO:
            return _LoggingFileHandleProxy(handle, label=label, path=path)
        return handle

    def info(self, path: str, **kwargs: Any):
        started = time.perf_counter()
        result = base_cls.info(self, path, **kwargs)
        logger.info(
            "REMOTE_FS info label=%s path=%r elapsed_ms=%d",
            label, path, int((time.perf_counter() - started) * 1000),
        )
        return result

    def ls(self, path: str, detail: bool = True, **kwargs: Any):
        started = time.perf_counter()
        result = base_cls.ls(self, path, detail=detail, **kwargs)
        logger.info(
            "REMOTE_FS ls label=%s path=%r elapsed_ms=%d count=%d",
            label, path, int((time.perf_counter() - started) * 1000),
            len(result) if hasattr(result, "__len__") else -1,
        )
        return result

    def glob(self, path: str, **kwargs: Any):
        started = time.perf_counter()
        result = base_cls.glob(self, path, **kwargs)
        logger.info(
            "REMOTE_FS glob label=%s path=%r elapsed_ms=%d count=%d",
            label, path, int((time.perf_counter() - started) * 1000),
            len(result) if hasattr(result, "__len__") else -1,
        )
        return result

    def exists(self, path: str, **kwargs: Any):
        started = time.perf_counter()
        result = base_cls.exists(self, path, **kwargs)
        logger.info(
            "REMOTE_FS exists label=%s path=%r elapsed_ms=%d result=%r",
            label, path, int((time.perf_counter() - started) * 1000), result,
        )
        return result

    def cat_file(self, path: str, **kwargs: Any):
        started = time.perf_counter()
        result = base_cls.cat_file(self, path, **kwargs)
        size = len(result) if isinstance(result, (bytes, bytearray, memoryview, str)) else "na"
        logger.info(
            "REMOTE_FS cat_file label=%s path=%r elapsed_ms=%d size=%s",
            label, path, int((time.perf_counter() - started) * 1000), size,
        )
        return result

    def head(self, path: str, size: int = 1024, **kwargs: Any):
        started = time.perf_counter()
        result = base_cls.head(self, path, size=size, **kwargs)
        logger.info(
            "REMOTE_FS head label=%s path=%r size=%d elapsed_ms=%d",
            label, path, size, int((time.perf_counter() - started) * 1000),
        )
        return result

    def read_block(self, path: str, offset: int, length: int, **kwargs: Any):
        started = time.perf_counter()
        result = base_cls.read_block(self, path, offset, length, **kwargs)
        logger.info(
            "REMOTE_FS read_block label=%s path=%r offset=%d length=%d elapsed_ms=%d",
            label, path, offset, length, int((time.perf_counter() - started) * 1000),
        )
        return result

    # Only override methods that actually exist on the base class so we don't
    # accidentally shadow something with an AttributeError on call.
    overrides: dict[str, Any] = {}
    for name, fn in [
        ("_open", _open), ("open", open), ("info", info), ("ls", ls),
        ("glob", glob), ("exists", exists), ("cat_file", cat_file),
        ("head", head), ("read_block", read_block),
    ]:
        if hasattr(base_cls, name):
            overrides[name] = fn

    proxy_cls = type(f"_Logging_{base_cls.__name__}", (base_cls,), overrides)
    filesystem.__class__ = proxy_cls
    return filesystem


@dataclass(frozen=True)
class RemoteFilesystemSpec:
    """Normalized filesystem construction details for a remote picker session."""

    protocol: str
    storage_options: dict[str, Any]
    root_path: str
    display_name: str
    uri_scheme: str
    uri_authority: str
    proxy_jump: str | None = None  # raw ProxyJump value from SSH config


@dataclass(frozen=True)
class RemoteEntry:
    """Single normalized directory entry from an fsspec ls call."""

    path: str
    name: str
    is_dir: bool
    size: int | None
    is_link: bool = False


def spec_to_descriptor(
    spec: RemoteFilesystemSpec,
    *,
    cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a JSON-safe descriptor for worker-side warm-up/open tasks."""
    descriptor = {
        "protocol": spec.protocol,
        "storage_options": dict(spec.storage_options),
        "root_path": spec.root_path,
        "display_name": spec.display_name,
        "uri_scheme": spec.uri_scheme,
        "uri_authority": spec.uri_authority,
        "proxy_jump": spec.proxy_jump,
    }
    if cache is not None:
        descriptor["cache"] = dict(cache)
    return descriptor


def descriptor_to_spec(descriptor: dict[str, Any]) -> RemoteFilesystemSpec:
    """Rebuild a filesystem spec from a worker/UI transport descriptor."""
    return RemoteFilesystemSpec(
        protocol=str(descriptor.get("protocol", "")),
        storage_options=dict(descriptor.get("storage_options", {})),
        root_path=str(descriptor.get("root_path", "")),
        display_name=str(descriptor.get("display_name", "Remote")),
        uri_scheme=str(descriptor.get("uri_scheme", "")),
        uri_authority=str(descriptor.get("uri_authority", "")),
        proxy_jump=(
            str(descriptor["proxy_jump"])
            if descriptor.get("proxy_jump")
            else None
        ),
    )


def remote_descriptor_hash(descriptor: dict[str, Any]) -> str:
    """Create a stable hash for descriptor-keyed worker session reuse."""
    normalized = json.dumps(descriptor, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _value_from_keys(details: dict[str, Any], *keys: str) -> str:
    """Return the first non-empty string value among candidate keys."""
    for key in keys:
        value = details.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _parse_proxy_jump(s: str) -> tuple[str | None, str, int]:
    """Parse a ProxyJump directive into (user, host, port). Only the first hop is used."""
    first = s.split(",")[0].strip()
    port = 22
    user: str | None = None
    if "@" in first:
        user_part, rest = first.split("@", 1)
        user = user_part or None
    else:
        rest = first
    if ":" in rest:
        host, port_str = rest.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            host = rest
    else:
        host = rest
    return user, host, port


def _emit_log(log: Callable[[str], None] | None, message: str) -> None:
    """Write a line to the optional connection log callback."""
    if log is not None:
        log(message)


def _memory_cache_type(strategy: object) -> str | None:
    """Map UI memory cache strategy labels to fsspec cache types."""
    value = str(strategy or "").strip().lower()
    if value == "block":
        return "bytes"
    if value == "readahead":
        return "readahead"
    if value == "whole-file":
        return "all"
    return None


def _apply_cache_configuration(
    filesystem: Any,
    *,
    cache: dict[str, Any] | None = None,
    log: Callable[[str], None] | None = None,
) -> Any:
    """Apply optional disk and in-memory caching wrappers to a filesystem."""
    if not isinstance(cache, dict):
        return filesystem

    configured = filesystem
    disk_wrapped = False
    block_size = max(1, int(cache.get("blocksize_mb", 2))) * 1024 * 1024
    disk_mode = str(cache.get("disk_mode", "Disabled")).strip().lower()
    disk_location = str(cache.get("disk_location", "")).strip() or "TMP"
    expiry_time = parse_disk_expiry_seconds(cache.get("disk_expiry"))
    disk_limit_gb = int(cache.get("disk_limit_gb", 0) or 0)

    if disk_mode in {"blocks", "files"}:
        try:
            import fsspec  # type: ignore
        except ImportError as exc:  # pragma: no cover - exercised at runtime
            raise RuntimeError("fsspec is required for remote caching") from exc

        cache_path = Path(disk_location).expanduser()
        prune_summary = prune_disk_cache(
            cache_path,
            limit_bytes=disk_limit_gb * 1024 * 1024 * 1024,
            expiry_seconds=expiry_time,
            log=log,
        )
        if prune_summary["removed_files"]:
            logger.info(
                "Pruned remote disk cache removed_files=%d removed_bytes=%d total_bytes=%d",
                prune_summary["removed_files"],
                prune_summary["removed_bytes"],
                prune_summary["total_bytes"],
            )

        protocol = "blockcache" if disk_mode == "blocks" else "filecache"
        cache_kwargs: dict[str, Any] = {
            "fs": configured,
            "cache_storage": str(cache_path),
            "expiry_time": expiry_time,
            "check_files": False,
        }
        if disk_mode == "blocks":
            cache_kwargs["block_size"] = block_size

        configured = fsspec.filesystem(protocol, **cache_kwargs)
        disk_wrapped = True
        logger.info(
            "Configured remote disk cache protocol=%s storage=%s expiry=%s",
            protocol,
            cache_path,
            expiry_time,
        )

    cache_type = _memory_cache_type(cache.get("cache_strategy"))
    if cache_type is not None:
        if disk_wrapped:
            logger.info(
                "Skipping remote memory cache defaults because disk cache wrapper is active"
            )
            return configured
        configured = _ConfiguredRemoteFileSystem(
            configured,
            open_defaults={
                "cache_type": cache_type,
                "block_size": block_size,
            },
        )
        logger.info(
            "Configured remote memory cache type=%s block_size=%d",
            cache_type,
            block_size,
        )

    return configured


class _XconvHostKeyPolicy:
    """Trust-on-first-use host key policy for Paramiko SSH clients.

    The client should call ``load_system_host_keys()`` before connecting so
    that hosts already present in ``~/.ssh/known_hosts`` are accepted without
    a prompt.  This policy is invoked only for hosts that are *not* in the
    loaded store.  It shows a Qt confirmation dialog with the key fingerprint;
    the key is accepted in-memory for the session if the user confirms, or
    the connection is aborted if the user declines or no Qt UI is available.
    """

    def __init__(
        self,
        parent: Any = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._parent = parent
        self._log = log

    def missing_host_key(self, client: Any, hostname: str, key: Any) -> None:
        """Prompt the user to accept or reject an unknown host key."""
        try:
            import paramiko  # type: ignore
            SSHException = paramiko.SSHException
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("paramiko is required for SSH host key verification") from exc

        try:
            fingerprint = ":".join(f"{b:02x}" for b in key.get_fingerprint())
            key_type = key.get_name()
        except Exception:
            fingerprint = "<unavailable>"
            key_type = "<unavailable>"

        _emit_log(self._log, f"Unknown host key for {hostname!r}: {key_type} {fingerprint}")

        try:
            from PySide6.QtWidgets import QMessageBox
            answer = QMessageBox.question(
                self._parent,
                "Unknown Host Key",
                f"The host {hostname!r} is not in known_hosts.\n\n"
                f"Key type:    {key_type}\n"
                f"Fingerprint: {fingerprint}\n\n"
                "Verify this fingerprint out-of-band before accepting.\n\n"
                "Trust this host key for the current session?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
        except Exception:
            # Qt unavailable (headless / test context) — reject by default.
            raise SSHException(
                f"Host key for {hostname!r} not in known_hosts; rejected (no UI available)."
            )

        if answer == QMessageBox.Yes:
            _emit_log(self._log, f"Host key for {hostname!r} accepted by user")
            # Accept in-memory only; do not write back to ~/.ssh/known_hosts.
            client._host_keys.add(hostname, key.get_name(), key)
        else:
            _emit_log(self._log, f"Host key for {hostname!r} rejected by user")
            raise SSHException(f"Host key for {hostname!r} rejected by user.")


def _create_sftp_via_jump(spec: RemoteFilesystemSpec, log: Callable[[str], None] | None = None) -> Any:
    """Build an SFTP filesystem tunnelled through a ProxyJump host."""
    try:
        import fsspec  # type: ignore
        import paramiko  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("fsspec and paramiko are required for remote navigation") from exc

    assert spec.proxy_jump is not None
    jump_user_override, jump_alias, jump_port = _parse_proxy_jump(spec.proxy_jump)
    _emit_log(log, f"Using ProxyJump: {spec.proxy_jump}")

    # Resolve the jump alias through the SSH config (handles Host aliases)
    jump_hostname = jump_alias
    jump_resolved_user: str | None = None
    jump_key_filename: str | None = None
    ssh_config_path = Path.home() / ".ssh/config"
    if ssh_config_path.is_file():
        try:
            ssh_cfg = paramiko.SSHConfig.from_path(str(ssh_config_path))
            looked_up = ssh_cfg.lookup(jump_alias)
            jump_hostname = looked_up.get("hostname", jump_alias)
            jump_resolved_user = looked_up.get("user")
            identity = looked_up.get("identityfile")
            if isinstance(identity, list) and identity:
                jump_key_filename = str(Path(identity[0]).expanduser())
            elif isinstance(identity, str) and identity:
                jump_key_filename = str(Path(identity).expanduser())
            _emit_log(log, f"Resolved jump alias {jump_alias!r} to host {jump_hostname!r}")
        except Exception:  # pragma: no cover
            _emit_log(log, "Could not fully resolve jump alias from ~/.ssh/config; using raw value")
            pass

    target_user = str(spec.storage_options.get("username", "")) or None
    target_key = str(spec.storage_options.get("key_filename", "")) or None
    explicit_jump_user = str(spec.storage_options.get("proxyjump_username", "")) or None
    explicit_jump_password = str(spec.storage_options.get("proxyjump_password", "")) or None

    # Prefer explicit user from ProxyJump directive, then SSH config user, then target user
    effective_jump_user = explicit_jump_user or jump_user_override or jump_resolved_user or target_user
    # Prefer jump-specific key, fall back to target key
    effective_jump_key = jump_key_filename or target_key

    jump_connect: dict[str, Any] = {"hostname": jump_hostname, "port": jump_port}
    if effective_jump_user:
        jump_connect["username"] = effective_jump_user
    if explicit_jump_password:
        jump_connect["password"] = explicit_jump_password
    if effective_jump_key:
        jump_connect["key_filename"] = effective_jump_key

    _emit_log(log, f"Connecting to jump host {jump_hostname}:{jump_port}")
    jump_client = paramiko.SSHClient()
    jump_client.load_system_host_keys()
    jump_client.set_missing_host_key_policy(_XconvHostKeyPolicy(log=log))
    jump_client.connect(**jump_connect)
    _emit_log(log, "Connected to jump host")

    transport = jump_client.get_transport()
    if transport is None:  # pragma: no cover
        jump_client.close()
        raise RuntimeError(f"Could not establish transport to jump host {jump_hostname!r}")

    target_host = str(spec.storage_options["host"])
    _emit_log(log, f"Opening jump tunnel to target {target_host}:22")
    channel = transport.open_channel("direct-tcpip", (target_host, 22), ("", 0))

    # Pass the pre-opened channel as the socket for the SFTP connection
    connect_kwargs = dict(spec.storage_options)
    connect_kwargs["sock"] = channel

    _emit_log(log, "Connecting SFTP over tunnel")
    fs = fsspec.filesystem(spec.protocol, **connect_kwargs)
    # Keep a reference to prevent the jump transport from being garbage-collected
    fs._xconv_jump_client = jump_client
    _emit_log(log, "SFTP tunnel established")
    return fs


def build_remote_filesystem_spec(config: dict[str, Any]) -> RemoteFilesystemSpec:
    """Translate remote configuration state into fsspec filesystem arguments."""
    protocol = str(config.get("protocol", "")).upper()
    remote = config.get("remote", {})
    if not isinstance(remote, dict):
        raise ValueError("Remote configuration is malformed")

    details = remote.get("details", {})
    if not isinstance(details, dict):
        details = {}

    if protocol == "S3":
        alias = str(remote.get("alias") or "S3")
        url = _value_from_keys(details, "url") or _value_from_keys(remote, "url")
        key = _value_from_keys(details, "accessKey", "access_key") or _value_from_keys(remote, "access_key")
        secret = _value_from_keys(details, "secretKey", "secret_key") or _value_from_keys(remote, "secret_key")

        storage_options: dict[str, Any] = {"anon": not (key and secret)}
        if key and secret:
            storage_options["key"] = key
            storage_options["secret"] = secret
        if url:
            storage_options["client_kwargs"] = {"endpoint_url": url}

        return RemoteFilesystemSpec(
            protocol="s3",
            storage_options=storage_options,
            root_path="",
            display_name=alias,
            uri_scheme="s3",
            uri_authority="",
        )

    if protocol == "SSH":
        alias = str(remote.get("alias") or "SSH")
        hostname = _value_from_keys(details, "hostname") or _value_from_keys(remote, "hostname")
        user = _value_from_keys(details, "user") or _value_from_keys(remote, "user")
        password = _value_from_keys(details, "password") or _value_from_keys(remote, "password")
        proxyjump_password = _value_from_keys(details, "proxyjump_password") or _value_from_keys(remote, "proxyjump_password")
        proxyjump_user = _value_from_keys(details, "proxyjump_user") or _value_from_keys(remote, "proxyjump_user")
        identity_file = _value_from_keys(details, "identityfile", "identity_file") or _value_from_keys(remote, "identity_file")
        if not hostname:
            raise ValueError("SSH configuration is missing a hostname")

        proxy_jump_raw = _value_from_keys(details, "proxyjump") or _value_from_keys(remote, "proxyjump")

        storage_options = {"host": hostname}
        if user:
            storage_options["username"] = user
        if password:
            storage_options["password"] = password
        if proxyjump_password:
            storage_options["proxyjump_password"] = proxyjump_password
        if proxyjump_user:
            storage_options["proxyjump_username"] = proxyjump_user
        if identity_file:
            storage_options["key_filename"] = str(Path(identity_file).expanduser())

        return RemoteFilesystemSpec(
            protocol="sftp",
            storage_options=storage_options,
            root_path=".",
            display_name=alias,
            uri_scheme="ssh",
            uri_authority=hostname,
            proxy_jump=proxy_jump_raw or None,
        )

    if protocol in {"HTTP", "HTTPS"}:
        url = _value_from_keys(details, "url", "base_url") or _value_from_keys(remote, "url", "base_url")
        if not url:
            raise ValueError("HTTPS remote navigation is not configured yet")
        return RemoteFilesystemSpec(
            protocol="http",
            storage_options={},
            root_path=url,
            display_name="HTTPS",
            uri_scheme="",
            uri_authority="",
        )

    raise ValueError(f"Unsupported remote protocol: {protocol}")


def create_filesystem(
    spec: RemoteFilesystemSpec,
    log: Callable[[str], None] | None = None,
    cache: dict[str, Any] | None = None,
) -> Any:
    """Create the underlying fsspec filesystem instance lazily."""
    if spec.proxy_jump and spec.protocol == "sftp":
        base_filesystem = _wrap_filesystem_with_logging(
            _create_sftp_via_jump(spec, log=log),
            label=f"{spec.protocol}:{spec.display_name}",
        )
        return _apply_cache_configuration(base_filesystem, cache=cache, log=log)

    try:
        import fsspec  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised at runtime
        raise RuntimeError("fsspec is required for remote navigation") from exc

    _emit_log(log, f"Connecting filesystem protocol {spec.protocol!r}")
    base_filesystem = _wrap_filesystem_with_logging(
        fsspec.filesystem(spec.protocol, **spec.storage_options),
        label=f"{spec.protocol}:{spec.display_name}",
    )
    return _apply_cache_configuration(base_filesystem, cache=cache, log=log)


def normalize_remote_entries(entries: list[Any]) -> list[RemoteEntry]:
    """Normalize fsspec ls results into tree-friendly entries sorted dirs-first."""
    normalized: list[RemoteEntry] = []
    for entry in entries:
        if isinstance(entry, str):
            raw_path = entry
            is_dir = raw_path.endswith("/")
            size: int | None = None
            is_link = False
        elif isinstance(entry, dict):
            raw_path = str(entry.get("name") or entry.get("Key") or "")
            entry_type = str(entry.get("type", "")).lower()
            is_dir = entry_type in {"directory", "dir"} or raw_path.endswith("/")
            is_link = entry_type in {"link", "symlink"} or bool(entry.get("islink") or entry.get("is_link"))
            raw_size = entry.get("size")
            size = int(raw_size) if isinstance(raw_size, int) else None
        else:
            continue

        cleaned = raw_path.rstrip("/") if raw_path not in {"", "/"} else raw_path
        if not cleaned and raw_path not in {"", "/"}:
            continue

        if cleaned in {"", "/"}:
            display_name = cleaned or "/"
        else:
            display_name = PurePosixPath(cleaned).name or cleaned

        normalized.append(
            RemoteEntry(
                path=cleaned or raw_path,
                name=display_name,
                is_dir=is_dir,
                size=size,
                is_link=is_link,
            )
        )

    return sorted(normalized, key=lambda item: (not item.is_dir, item.name.lower()))


def resolve_link_entries(entries: list[RemoteEntry], filesystem: Any) -> list[RemoteEntry]:
    """Resolve symlink entries to determine if they target directories."""
    resolved: list[RemoteEntry] = []
    for entry in entries:
        if not entry.is_link or entry.is_dir:
            resolved.append(entry)
            continue

        try:
            target_is_dir = bool(filesystem.isdir(entry.path))
        except Exception:
            target_is_dir = False

        if target_is_dir:
            resolved.append(
                RemoteEntry(
                    path=entry.path,
                    name=entry.name,
                    is_dir=True,
                    size=entry.size,
                    is_link=True,
                )
            )
            continue

        resolved.append(entry)

    return sorted(resolved, key=lambda item: (not item.is_dir, item.name.lower()))


def filter_hidden_entries(entries: list[RemoteEntry], *, show_hidden: bool) -> list[RemoteEntry]:
    """Optionally remove dot-prefixed entries from a normalized listing."""
    if show_hidden:
        return entries
    return [entry for entry in entries if not entry.name.startswith(".")]


_KNOWN_EXTENSIONS = frozenset((".nc", ".pp"))
_ZARR_METADATA_FILENAMES = frozenset((".zarray", ".zgroup", ".zmetadata", "zarr.json"))


def filter_type_entries(entries: list[RemoteEntry], *, show_all: bool) -> list[RemoteEntry]:
    """When show_all is False, keep only directories and .nc/.pp files."""
    if show_all:
        return entries
    return [
        entry for entry in entries
        if entry.is_dir or PurePosixPath(entry.name).suffix.lower() in _KNOWN_EXTENSIONS
    ]


def format_size(size: int | None) -> str:
    """Format raw byte sizes using human-readable binary units."""
    if size is None:
        return ""
    if size < 0:
        return ""
    if size < 1024:
        return f"{size} B"

    value = float(size)
    units = ["KB", "MB", "GB", "TB"]
    for unit in units:
        value /= 1024.0
        if value < 1024.0 or unit == units[-1]:
            text = f"{value:.1f}".rstrip("0").rstrip(".")
            return f"{text} {unit}"
    return ""


def is_zarr_path(path: str) -> bool:
    """Return True when a path's leaf-name uses the .zarr suffix."""
    name = PurePosixPath(path.rstrip("/") or path).name
    return name.lower().endswith(".zarr")


def directory_contains_zarr_metadata(entries: list[RemoteEntry]) -> bool:
    """Return True when a directory listing looks like a Zarr store root."""
    names = {entry.name for entry in entries}
    return bool(names & _ZARR_METADATA_FILENAMES)


def build_remote_uri(spec: RemoteFilesystemSpec, path: str) -> str:
    """Build a user-facing remote URI from a filesystem path."""
    cleaned = path.strip()
    if spec.uri_scheme == "s3":
        return f"s3://{cleaned.lstrip('/')}"
    if spec.uri_scheme == "ssh":
        remote_path = cleaned if cleaned.startswith("/") else f"/{cleaned}"
        return f"ssh://{spec.uri_authority}{remote_path}"
    return cleaned


class RemoteLoginLogDialog(QDialog):
    """Show connection progress for remote login/setup."""

    def __init__(self, parent: QWidget | None, display_name: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Remote Login")
        self.resize(720, 360)
        self._follow_log_output = True
        self._auto_scrolling = False

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Connecting to {display_name}"))

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setCenterOnScroll(False)
        self.log_view.textChanged.connect(self._queue_scroll_to_end)
        self.log_view.cursorPositionChanged.connect(self._queue_scroll_to_end)
        self.log_view.verticalScrollBar().rangeChanged.connect(lambda _min, _max: self._queue_scroll_to_end())
        layout.addWidget(self.log_view, 1)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.close_button = self.buttons.button(QDialogButtonBox.Close)
        self.close_button.setEnabled(False)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def append_line(self, message: str) -> None:
        """Append one log line and keep the viewport pinned to EOF."""
        scrollbar = self.log_view.verticalScrollBar()
        logger.info(
            "REMOTE_LOG append start len=%d value=%d max=%d",
            len(message),
            scrollbar.value(),
            scrollbar.maximum(),
        )
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.log_view.setTextCursor(cursor)
        cursor.insertText(f"{message}\n")
        self.log_view.setTextCursor(cursor)
        self.log_view.ensureCursorVisible()
        logger.info(
            "REMOTE_LOG append done cursor=%d blocks=%d value=%d max=%d",
            self.log_view.textCursor().position(),
            self.log_view.document().blockCount(),
            scrollbar.value(),
            scrollbar.maximum(),
        )
        self._queue_scroll_to_end()

    def _queue_scroll_to_end(self) -> None:
        """Queue an EOF scroll after pending UI updates settle."""
        if not self._follow_log_output:
            return
        scrollbar = self.log_view.verticalScrollBar()
        logger.info(
            "REMOTE_LOG queue_scroll value=%d max=%d",
            scrollbar.value(),
            scrollbar.maximum(),
        )
        QTimer.singleShot(0, self._scroll_to_end)

    def _scroll_to_end(self) -> None:
        """Force the log viewport to the newest line."""
        if not self._follow_log_output or self._auto_scrolling:
            logger.info(
                "REMOTE_LOG scroll skipped follow=%s auto=%s",
                self._follow_log_output,
                self._auto_scrolling,
            )
            return
        self._auto_scrolling = True
        cursor = self.log_view.textCursor()
        scrollbar = self.log_view.verticalScrollBar()
        before = (scrollbar.value(), scrollbar.maximum())
        try:
            cursor.movePosition(QTextCursor.End)
            self.log_view.setTextCursor(cursor)
            self.log_view.ensureCursorVisible()
            scrollbar.setValue(scrollbar.maximum())
            logger.info(
                "REMOTE_LOG scroll applied before=(%d,%d) after=(%d,%d) cursor=%d blocks=%d",
                before[0],
                before[1],
                scrollbar.value(),
                scrollbar.maximum(),
                self.log_view.textCursor().position(),
                self.log_view.document().blockCount(),
            )
        finally:
            self._auto_scrolling = False

    def mark_failed(self, message: str) -> None:
        """Mark connection failure and keep dialog open until user closes it."""
        self.append_line("")
        self.append_line("Connection failed.")
        self.append_line(message)
        self.close_button.setEnabled(True)
        self.close_button.setFocus()


class RemoteFileNavigatorDialog(QDialog):
    """Lazy-loaded tree browser backed by an fsspec filesystem."""

    _ROLE_DATA = Qt.UserRole
    _PLACEHOLDER = "Loading..."

    def __init__(
        self,
        parent: QWidget | None,
        config: dict[str, Any],
        *,
        spec: RemoteFilesystemSpec | None = None,
        filesystem: Any | None = None,
        list_callback: Callable[[str], list[RemoteEntry]] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Remote File Navigator")
        self.resize(820, 560)

        self._config = config
        self._selected_uri = ""
        self._selected_path = ""
        self._detected_zarr_paths: set[str] = set()
        self._spec = spec or build_remote_filesystem_spec(config)
        if list_callback is not None:
            self._filesystem: Any | None = None
            self._list_callback: Callable[[str], list[RemoteEntry]] | None = list_callback
        else:
            self._filesystem = filesystem or create_filesystem(
                self._spec,
                cache=config.get("cache") if isinstance(config, dict) else None,
            )
            self._list_callback = None

        layout = QVBoxLayout(self)

        header = QLabel(f"Browsing {self._spec.display_name}")
        layout.addWidget(header)

        filter_row = QWidget()
        filter_layout = QHBoxLayout(filter_row)
        filter_layout.setContentsMargins(0, 0, 0, 0)

        self.show_all_types_check = QCheckBox("Show all files")
        self.show_all_types_check.setChecked(False)
        self.show_all_types_check.toggled.connect(self._on_show_all_types_toggled)
        filter_layout.addWidget(self.show_all_types_check)

        self.show_hidden_check = QCheckBox("Show hidden files")
        self.show_hidden_check.setChecked(self._spec.protocol != "sftp")
        self.show_hidden_check.setVisible(self._spec.protocol == "sftp")
        self.show_hidden_check.toggled.connect(self._on_show_hidden_toggled)
        filter_layout.addWidget(self.show_hidden_check)

        filter_layout.addStretch()
        layout.addWidget(filter_row)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Type", "Size"])
        # Give the name column a practical default width of roughly 40 characters.
        self.tree.setColumnWidth(0, self.tree.fontMetrics().horizontalAdvance("M" * 40))
        self.tree.itemExpanded.connect(self._on_item_expanded)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.tree)

        footer = QWidget()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.addWidget(QLabel("Selection:"))
        self.selection_label = QLabel("No file selected")
        footer_layout.addWidget(self.selection_label, 1)
        layout.addWidget(footer)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.open_button = buttons.addButton("Open", QDialogButtonBox.AcceptRole)
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._populate_root()

    def _populate_root(self) -> None:
        """Load the top-level listing for the configured remote filesystem."""
        try:
            entries = self._list_entries(self._spec.root_path)
        except Exception as exc:  # pragma: no cover - UI error path
            QMessageBox.critical(self, "Remote navigation failed", str(exc))
            return

        self.tree.clear()
        for entry in entries:
            self.tree.addTopLevelItem(self._create_item(entry))

    def _item_path(self, item: QTreeWidgetItem) -> str:
        """Return the remote path metadata attached to a tree item."""
        data = item.data(0, self._ROLE_DATA) or {}
        if not isinstance(data, dict):
            return ""
        return str(data.get("path", ""))

    def _collect_tree_state(self) -> tuple[list[str], str]:
        """Capture only the active branch so refreshes do not fan out network calls."""
        selected_items = self.tree.selectedItems()
        focus_item = selected_items[0] if selected_items else self.tree.currentItem()
        if focus_item is None:
            return [], ""

        selected_path = self._item_path(focus_item)
        branch_items: list[QTreeWidgetItem] = []
        cursor: QTreeWidgetItem | None = focus_item
        while cursor is not None:
            branch_items.append(cursor)
            cursor = cursor.parent()

        expanded_branch_paths: list[str] = []
        for item in reversed(branch_items):
            path = self._item_path(item)
            data = item.data(0, self._ROLE_DATA) or {}
            is_dir = isinstance(data, dict) and bool(data.get("is_dir"))
            if path and is_dir and item.isExpanded():
                expanded_branch_paths.append(path)

        return expanded_branch_paths, selected_path

    def _find_item_by_path(self, path: str) -> QTreeWidgetItem | None:
        """Find the first tree item that matches a remote path."""
        if not path:
            return None

        stack: list[QTreeWidgetItem] = [
            self.tree.topLevelItem(index)
            for index in range(self.tree.topLevelItemCount())
        ]
        while stack:
            item = stack.pop()
            if self._item_path(item) == path:
                return item
            for index in range(item.childCount()):
                stack.append(item.child(index))
        return None

    def _restore_tree_state(self, expanded_paths: list[str], selected_path: str) -> None:
        """Re-expand the focused branch and restore selection after a listing refresh."""
        for path in expanded_paths:
            item = self._find_item_by_path(path)
            if item is None:
                continue
            data = item.data(0, self._ROLE_DATA) or {}
            if isinstance(data, dict) and bool(data.get("is_dir")):
                item.setExpanded(True)

        selected_item = self._find_item_by_path(selected_path)
        if selected_item is None:
            # Fall back to nearest visible ancestor when the exact entry is filtered out.
            current_path = selected_path
            while "/" in current_path:
                current_path = current_path.rsplit("/", 1)[0]
                selected_item = self._find_item_by_path(current_path)
                if selected_item is not None:
                    break

        if selected_item is not None:
            self.tree.setCurrentItem(selected_item)

        self._on_selection_changed()

    def _refresh_tree_preserving_state(self) -> None:
        """Refresh root listing while keeping navigation context where possible."""
        expanded_paths, selected_path = self._collect_tree_state()
        self._populate_root()
        self._restore_tree_state(expanded_paths, selected_path)

    def _list_entries_unfiltered(self, path: str) -> list[RemoteEntry]:
        """Call filesystem ls and return normalized entries before UI filtering."""
        if self._list_callback is not None:
            return self._list_callback(path)
        listing = self._filesystem.ls(path, detail=True)
        if not isinstance(listing, list):
            raise RuntimeError(f"Unexpected listing result for {path!r}")
        entries = normalize_remote_entries(listing)
        return resolve_link_entries(entries, self._filesystem)

    def _apply_entry_filters(self, entries: list[RemoteEntry]) -> list[RemoteEntry]:
        """Apply hidden-file and type filters according to current UI toggles."""
        filtered = filter_hidden_entries(entries, show_hidden=self.show_hidden_check.isChecked())
        return filter_type_entries(filtered, show_all=self.show_all_types_check.isChecked())

    def _list_entries(self, path: str) -> list[RemoteEntry]:
        """Call filesystem ls and return entries after UI filtering."""
        return self._apply_entry_filters(self._list_entries_unfiltered(path))

    def _on_show_all_types_toggled(self, _checked: bool) -> None:
        """Refresh the tree when the file-type filter changes."""
        self._refresh_tree_preserving_state()

    def _on_show_hidden_toggled(self, _checked: bool) -> None:
        """Refresh the tree when hidden-file visibility changes."""
        self._refresh_tree_preserving_state()

    def _create_item(self, entry: RemoteEntry) -> QTreeWidgetItem:
        """Create a tree item for a normalized remote entry."""
        is_zarr = entry.is_dir and (
            is_zarr_path(entry.path) or entry.path in self._detected_zarr_paths
        )
        if is_zarr:
            entry_type = "Zarr"
        elif entry.is_link:
            entry_type = "Link to folder" if entry.is_dir else "Link to file"
        else:
            entry_type = "Folder" if entry.is_dir else "File"

        item = QTreeWidgetItem([
            entry.name,
            entry_type,
            format_size(entry.size),
        ])
        item.setData(0, self._ROLE_DATA, {
            "path": entry.path,
            "is_dir": entry.is_dir,
            "is_zarr": is_zarr,
            "loaded": False,
        })
        if entry.is_dir:
            item.addChild(QTreeWidgetItem([self._PLACEHOLDER]))
        return item

    def _on_item_expanded(self, item: QTreeWidgetItem) -> None:
        """Lazy-load directory contents when a tree item is expanded."""
        data = item.data(0, self._ROLE_DATA) or {}
        if not isinstance(data, dict) or not data.get("is_dir") or data.get("loaded"):
            return

        try:
            all_entries = self._list_entries_unfiltered(str(data.get("path", "")))
        except Exception as exc:  # pragma: no cover - UI error path
            QMessageBox.warning(self, "Listing failed", str(exc))
            return

        if directory_contains_zarr_metadata(all_entries):
            item.setText(1, "Zarr")
            data["is_zarr"] = True
            path = str(data.get("path", ""))
            if path:
                self._detected_zarr_paths.add(path)

        entries = self._apply_entry_filters(all_entries)

        item.takeChildren()
        for entry in entries:
            item.addChild(self._create_item(entry))
        data["loaded"] = True
        item.setData(0, self._ROLE_DATA, data)

    def _on_selection_changed(self) -> None:
        """Update selection state and enable Open only for files."""
        selected = self.tree.selectedItems()
        if not selected:
            self._selected_uri = ""
            self._selected_path = ""
            self.selection_label.setText("No file selected")
            self.open_button.setEnabled(False)
            return

        item = selected[0]
        data = item.data(0, self._ROLE_DATA) or {}
        if not isinstance(data, dict):
            return

        path = str(data.get("path", ""))
        is_dir = bool(data.get("is_dir"))
        self._selected_path = "" if is_dir else path
        self._selected_uri = "" if is_dir else build_remote_uri(self._spec, path)
        self.selection_label.setText(path or "No file selected")
        self.open_button.setEnabled(not is_dir and bool(path))

    def _on_item_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        """Open a file immediately on double click."""
        data = item.data(0, self._ROLE_DATA) or {}
        if isinstance(data, dict) and not bool(data.get("is_dir")):
            self.accept()

    def selected_uri(self) -> str:
        """Return the currently selected remote file URI."""
        return self._selected_uri

    def selected_path(self) -> str:
        """Return the currently selected filesystem path."""
        return self._selected_path

    def accept(self) -> None:  # type: ignore[override]
        """Require a file selection before closing with success."""
        if not self._selected_uri:
            QMessageBox.warning(self, "No file selected", "Select a remote file before opening.")
            return
        super().accept()

    def done(self, result: int) -> None:
        """Clean up jump-host SSH sessions when the dialog closes."""
        jump_client = getattr(self._filesystem, "_xconv_jump_client", None)
        if jump_client is not None:
            try:
                jump_client.close()
            except Exception:
                pass
        super().done(result)

    @classmethod
    def get_remote_selection_details(
        cls,
        parent: QWidget | None,
        config: dict[str, Any],
        *,
        spec: RemoteFilesystemSpec | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Show the remote navigator and return the selected URI and path."""
        if spec is None:
            try:
                spec = build_remote_filesystem_spec(config)
            except Exception as exc:
                QMessageBox.critical(parent, "Remote configuration invalid", str(exc))
                return {}, False

        log_dialog = RemoteLoginLogDialog(parent, spec.display_name)
        log_dialog.show()
        QApplication.processEvents()

        def _log_line(message: str) -> None:
            log_dialog.append_line(message)

        filesystem: Any | None = None
        _log_line("Starting remote login...")
        try:
            filesystem = create_filesystem(
                spec,
                log=_log_line,
                cache=config.get("cache") if isinstance(config, dict) else None,
            )
            _log_line(f"Checking remote root: {spec.root_path or '/'}")
            listing = filesystem.ls(spec.root_path, detail=True)
            if not isinstance(listing, list):
                raise RuntimeError(f"Unexpected listing result for {spec.root_path!r}")
            _log_line("Login ready. Opening file picker...")
            log_dialog.close()
        except Exception as exc:
            log_dialog.mark_failed(str(exc))
            log_dialog.exec()
            if filesystem is not None:
                client = getattr(filesystem, "_xconv_jump_client", None)
                if client is not None:
                    try:
                        client.close()
                    except Exception:
                        pass
            return {}, False

        assert filesystem is not None
        dialog = cls(parent, config, spec=spec, filesystem=filesystem)
        if dialog.exec() != QDialog.Accepted:
            return {}, False
        return {
            "uri": dialog.selected_uri(),
            "path": dialog.selected_path(),
        }, True

    @classmethod
    def get_remote_selection(
        cls,
        parent: QWidget | None,
        config: dict[str, Any],
    ) -> tuple[str, bool]:
        """Show the remote navigator and return the selected file URI."""
        details, ok = cls.get_remote_selection_details(parent, config)
        return str(details.get("uri", "")), ok