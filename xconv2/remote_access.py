"""Shared remote-access primitives used by the GUI and worker layers.

The module is intentionally organised in three blocks so a new reader can scan
it top-down:

1. Data classes: stable value objects passed between UI and worker code.
2. Classes: stateful facades and internal wrapper classes.
3. Utility functions: descriptor helpers, normalization helpers, cache
   wrappers, and filesystem construction helpers.

The main public entry points are ``RemoteAccessSession``,
``RemoteFilesystemSpec``, ``build_remote_filesystem_spec``, and
``create_filesystem``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import urlparse

from xconv2.cache_utils import parse_disk_expiry_seconds, prune_disk_cache
from xconv2.logging_utils import coerce_log_level


logger = logging.getLogger(__name__)

_KNOWN_EXTENSIONS = frozenset((".nc", ".pp"))
_ZARR_METADATA_FILENAMES = frozenset((".zarray", ".zgroup", ".zmetadata", "zarr.json"))

__all__ = [
    "RemoteAccessSession",
    "RemoteEntry",
    "RemoteFilesystemSpec",
    "RemoteLoggingConfiguration",
    "build_remote_filesystem_spec",
    "build_remote_uri",
    "create_filesystem",
    "descriptor_to_spec",
    "directory_contains_zarr_metadata",
    "filter_hidden_entries",
    "filter_type_entries",
    "format_size",
    "is_zarr_path",
    "normalize_remote_datasets_for_cf_read",
    "normalize_remote_entries",
    "remote_descriptor_hash",
    "resolve_link_entries",
    "spec_to_descriptor",
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RemoteLoggingConfiguration:
    """Runtime logging controls for remote filesystem tracing."""

    level: int = logging.INFO
    trace_filesystem: bool = False
    trace_file_io: bool = False


@dataclass(frozen=True)
class RemoteEntry:
    """Single normalized directory entry from an fsspec ls call."""

    path: str
    name: str
    is_dir: bool
    size: int | None
    is_link: bool = False


@dataclass(frozen=True)
class RemoteFilesystemSpec:
    """Normalized filesystem construction details for a remote picker session."""

    protocol: str
    storage_options: dict[str, Any]
    root_path: str
    display_name: str
    uri_scheme: str
    uri_authority: str
    proxy_jump: str | None = None


# ---------------------------------------------------------------------------
# Classes
# ---------------------------------------------------------------------------


class RemoteAccessSession:
    """Shared remote access facade for listing and reading via one filesystem."""

    _logging_configuration = RemoteLoggingConfiguration()

    def __init__(self, filesystem: Any) -> None:
        self.filesystem = filesystem

    @classmethod
    def configure_logging(
        cls,
        *,
        level: int | str | None = None,
        trace_filesystem: bool | None = None,
        trace_file_io: bool | None = None,
    ) -> RemoteLoggingConfiguration:
        """Update shared runtime logging settings for remote access."""
        current = cls._logging_configuration
        cls._logging_configuration = RemoteLoggingConfiguration(
            level=coerce_log_level(level, default=current.level) if level is not None else current.level,
            trace_filesystem=(
                current.trace_filesystem
                if trace_filesystem is None
                else bool(trace_filesystem)
            ),
            trace_file_io=(
                current.trace_file_io
                if trace_file_io is None
                else bool(trace_file_io)
            ),
        )
        return cls._logging_configuration

    @classmethod
    def logging_configuration(cls) -> RemoteLoggingConfiguration:
        """Return the active shared runtime logging configuration."""
        return cls._logging_configuration

    def list_entries(self, path: str) -> list[RemoteEntry]:
        """List and normalize directory entries from the backing filesystem."""
        listing = self.filesystem.ls(path, detail=True)
        if not isinstance(listing, list):
            return []
        entries = normalize_remote_entries(listing)
        return resolve_link_entries(entries, self.filesystem)

    def read_fields(
        self,
        *,
        descriptor: dict[str, Any],
        datasets: str | list[str],
        reader: Callable[..., Any],
    ) -> Any:
        """
        Read fields with descriptor-aware dataset normalization.
        The reader is expected to accept datasets and an optional filesystem"
        For example, this is currenlty used to wrap cf.read with auto-normalization of 
        HTTP paths. When we want to simplify our code, we may choose to 
        move the normalization logic into the worker,and emit the filesytem so that 
        cf.read can be used directly from the worker code.  #FIXME
        """
        normalized = normalize_remote_datasets_for_cf_read(
            descriptor=descriptor,
            datasets=datasets,
        )
        return reader(normalized, filesystem=self.filesystem)

    def close(self) -> None:
        """Best-effort cleanup for filesystem and jump-host resources."""
        close = getattr(self.filesystem, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

        jump_client = getattr(self.filesystem, "_xconv_jump_client", None)
        if jump_client is not None:
            try:
                jump_client.close()
            except Exception:
                pass


class _ConfiguredRemoteFileSystem:
    """Proxy filesystem that injects default open kwargs for read caching."""

    def __init__(self, filesystem: Any, *, open_defaults: dict[str, Any]) -> None:
        self._filesystem = filesystem
        self._open_defaults = dict(open_defaults)
        self.protocol = getattr(filesystem, "protocol", None)

    def open(self, path: str, mode: str = "rb", **kwargs: Any):
        return self._filesystem.open(path, mode=mode, **self._merge_open_kwargs(mode, kwargs))

    def _open(self, path: str, mode: str = "rb", **kwargs: Any):
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

    def __getattr__(self, name: str) -> Any:
        return getattr(self._handle, name)


class _XconvHostKeyPolicy:
    """Trust-on-first-use host key policy for Paramiko SSH clients."""

    def __init__(self, parent: Any = None, log: Callable[[str], None] | None = None) -> None:
        self._parent = parent
        self._log = log

    def missing_host_key(self, client: Any, hostname: str, key: Any) -> None:
        import paramiko  # type: ignore

        try:
            fingerprint = ":".join(f"{b:02x}" for b in key.get_fingerprint())
            key_type = key.get_name()
        except Exception:
            fingerprint = "<unavailable>"
            key_type = "<unavailable>"

        _emit_log(self._log, f"Unknown host key for {hostname!r}: {key_type} {fingerprint}")
        from PySide6.QtWidgets import QMessageBox

        answer = QMessageBox.question(
            self._parent,
            "Unknown Host Key",
            f"The host {hostname!r} is not in known_hosts.\n\n"
            f"Key type:    {key_type}\n"
            f"Fingerprint: {fingerprint}\n\n"
            "Trust this host key for the current session?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            client._host_keys.add(hostname, key.get_name(), key)
        else:
            raise paramiko.SSHException(f"Host key for {hostname!r} rejected by user.")


# ---------------------------------------------------------------------------
# Utility functions: descriptors and logging
# ---------------------------------------------------------------------------


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
    """Parse a ProxyJump directive into ``(user, host, port)``."""
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


# ---------------------------------------------------------------------------
# Utility functions: entry and dataset normalization
# ---------------------------------------------------------------------------


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


def normalize_remote_datasets_for_cf_read(
    *,
    descriptor: dict[str, Any],
    datasets: str | list[str],
) -> str | list[str]:
    """Normalize remote dataset paths to forms cf.read can open with a filesystem."""
    protocol = str(descriptor.get("protocol", "")).lower()
    if protocol != "http":
        return datasets

    root_path = str(descriptor.get("root_path", "")).strip()
    parsed_root = urlparse(root_path)
    if parsed_root.scheme not in {"http", "https"} or not parsed_root.netloc:
        return datasets

    origin = f"{parsed_root.scheme}://{parsed_root.netloc}"
    root_prefix = parsed_root.path.rstrip("/")

    def _normalize_one(path: str) -> str:
        text = str(path).strip()
        parsed = urlparse(text)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return text

        if text.startswith("/"):
            if root_prefix and (text == root_prefix or text.startswith(root_prefix + "/")):
                return origin + text
            if root_prefix:
                return origin + root_prefix + text
            return origin + text

        suffix = text.lstrip("/")
        if root_prefix:
            return origin + root_prefix + "/" + suffix
        return origin + "/" + suffix

    if isinstance(datasets, list):
        return [_normalize_one(item) for item in datasets]
    return _normalize_one(datasets)


def filter_hidden_entries(entries: list[RemoteEntry], *, show_hidden: bool) -> list[RemoteEntry]:
    """Optionally remove dot-prefixed entries from a normalized listing."""
    if show_hidden:
        return entries
    return [entry for entry in entries if not entry.name.startswith(".")]


def filter_type_entries(entries: list[RemoteEntry], *, show_all: bool) -> list[RemoteEntry]:
    """When show_all is False, keep only directories and .nc/.pp files."""
    if show_all:
        return entries
    return [entry for entry in entries if entry.is_dir or PurePosixPath(entry.name).suffix.lower() in _KNOWN_EXTENSIONS]


def format_size(size: int | None) -> str:
    """Format raw byte sizes using human-readable binary units."""
    if size is None or size < 0:
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


# ---------------------------------------------------------------------------
# Utility functions: caching and logging wrappers
# ---------------------------------------------------------------------------


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


def _wrap_filesystem_with_logging(filesystem: Any, *, label: str) -> Any:
    """Inject optional tracing wrappers without breaking fsspec class lookups."""
    if getattr(filesystem, "_xconv_logging_wrapped", False):
        return filesystem

    base_cls = type(filesystem)

    def _open(self, path: str, mode: str = "rb", **kwargs: Any):
        started = time.perf_counter()
        handle = base_cls._open(self, path, mode=mode, **kwargs)
        config = RemoteAccessSession.logging_configuration()
        if config.trace_filesystem:
            logger.info(
                "REMOTE_FS _open label=%s path=%r mode=%s elapsed_ms=%d",
                label,
                path,
                mode,
                int((time.perf_counter() - started) * 1000),
            )
        if config.trace_file_io:
            return _LoggingFileHandleProxy(handle, label=label, path=path)
        return handle

    def open(self, path: str, mode: str = "rb", **kwargs: Any):
        started = time.perf_counter()
        handle = base_cls.open(self, path, mode=mode, **kwargs)
        config = RemoteAccessSession.logging_configuration()
        if config.trace_filesystem:
            logger.info(
                "REMOTE_FS open label=%s path=%r mode=%s elapsed_ms=%d",
                label,
                path,
                mode,
                int((time.perf_counter() - started) * 1000),
            )
        if config.trace_file_io:
            return _LoggingFileHandleProxy(handle, label=label, path=path)
        return handle

    overrides: dict[str, Any] = {}
    for name, fn in [("_open", _open), ("open", open)]:
        if hasattr(base_cls, name):
            overrides[name] = fn

    proxy_cls = type(f"_Logging_{base_cls.__name__}", (base_cls,), overrides)
    filesystem.__class__ = proxy_cls
    filesystem._xconv_logging_wrapped = True
    return filesystem


def _prune_incompatible_blockcache_entries(
    cache_path: Path,
    *,
    block_size: int,
    log: Callable[[str], None] | None = None,
) -> None:
    """Remove blockcache entries that were created with a different block size."""
    index_path = cache_path / "cache"
    if not index_path.is_file():
        return

    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to read blockcache index at %s", index_path)
        return

    if not isinstance(payload, dict):
        return

    removed = 0
    for key, details in list(payload.items()):
        if not isinstance(details, dict):
            continue
        existing_block_size = details.get("blocksize")
        if not isinstance(existing_block_size, int) or existing_block_size == block_size:
            continue
        cache_file = details.get("fn")
        if isinstance(cache_file, str) and cache_file:
            try:
                (cache_path / cache_file).unlink(missing_ok=True)
            except OSError:
                logger.exception("Failed to remove stale blockcache file %s", cache_file)
        payload.pop(key, None)
        removed += 1

    if not removed:
        return

    try:
        index_path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        logger.exception("Failed to write pruned blockcache index at %s", index_path)
        return

    _emit_log(log, f"Pruned {removed} incompatible blockcache entries for blocksize={block_size}.")


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
    max_blocks = max(1, int(cache.get("max_blocks", 32)))
    disk_mode = str(cache.get("disk_mode", "Disabled")).strip().lower()
    disk_location = str(cache.get("disk_location", "")).strip() or "TMP"
    expiry_time = parse_disk_expiry_seconds(cache.get("disk_expiry"))
    disk_limit_gb = int(cache.get("disk_limit_gb", 0) or 0)

    if disk_mode in {"blocks", "files"}:
        import fsspec  # type: ignore

        cache_path = Path(disk_location).expanduser()
        prune_disk_cache(
            cache_path,
            limit_bytes=disk_limit_gb * 1024 * 1024 * 1024,
            expiry_seconds=expiry_time,
            log=log,
        )

        protocol = "blockcache" if disk_mode == "blocks" else "filecache"
        cache_kwargs: dict[str, Any] = {
            "fs": configured,
            "cache_storage": str(cache_path),
            "expiry_time": expiry_time,
            "check_files": False,
        }
        if disk_mode == "blocks":
            _prune_incompatible_blockcache_entries(cache_path, block_size=block_size, log=log)
            cache_kwargs["blocksize"] = block_size
            cache_kwargs["maxblocks"] = max_blocks

        configured = fsspec.filesystem(protocol, **cache_kwargs)
        disk_wrapped = True

    cache_type = _memory_cache_type(cache.get("cache_strategy"))
    if cache_type is not None and not disk_wrapped:
        configured = _ConfiguredRemoteFileSystem(
            configured,
            open_defaults={
                "cache_type": cache_type,
                "block_size": block_size,
            },
        )

    return configured


# ---------------------------------------------------------------------------
# Utility functions: filesystem construction
# ---------------------------------------------------------------------------


def _create_sftp_via_jump(spec: RemoteFilesystemSpec, log: Callable[[str], None] | None = None) -> Any:
    """Build an SFTP filesystem tunnelled through a ProxyJump host."""
    import fsspec  # type: ignore
    import paramiko  # type: ignore

    assert spec.proxy_jump is not None
    jump_user_override, jump_alias, jump_port = _parse_proxy_jump(spec.proxy_jump)

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
        except Exception:
            pass

    target_user = str(spec.storage_options.get("username", "")) or None
    target_key = str(spec.storage_options.get("key_filename", "")) or None
    explicit_jump_user = str(spec.storage_options.get("proxyjump_username", "")) or None
    explicit_jump_password = str(spec.storage_options.get("proxyjump_password", "")) or None
    effective_jump_user = explicit_jump_user or jump_user_override or jump_resolved_user or target_user
    effective_jump_key = jump_key_filename or target_key

    jump_connect: dict[str, Any] = {"hostname": jump_hostname, "port": jump_port}
    if effective_jump_user:
        jump_connect["username"] = effective_jump_user
    if explicit_jump_password:
        jump_connect["password"] = explicit_jump_password
    if effective_jump_key:
        jump_connect["key_filename"] = effective_jump_key

    jump_client = paramiko.SSHClient()
    jump_client.load_system_host_keys()
    jump_client.set_missing_host_key_policy(_XconvHostKeyPolicy(log=log))
    jump_client.connect(**jump_connect)

    transport = jump_client.get_transport()
    if transport is None:
        jump_client.close()
        raise RuntimeError(f"Could not establish transport to jump host {jump_hostname!r}")

    target_host = str(spec.storage_options["host"])
    channel = transport.open_channel("direct-tcpip", (target_host, 22), ("", 0))
    connect_kwargs = dict(spec.storage_options)
    connect_kwargs["sock"] = channel
    fs = fsspec.filesystem(spec.protocol, **connect_kwargs)
    fs._xconv_jump_client = jump_client
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

    import fsspec  # type: ignore

    base_filesystem = _wrap_filesystem_with_logging(
        fsspec.filesystem(spec.protocol, **spec.storage_options),
        label=f"{spec.protocol}:{spec.display_name}",
    )
    return _apply_cache_configuration(base_filesystem, cache=cache, log=log)
