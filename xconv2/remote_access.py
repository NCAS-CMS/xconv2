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
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import urlparse

from xconv2.cache_utils import parse_disk_expiry_seconds, prune_disk_cache
from xconv2.logging_utils import normalize_scope_levels
from xconv2.remote_fs import RemoteFileSystemFactory


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
    """Runtime logging controls for module scopes."""

    scope_levels: dict[str, int]

    def scope_level(self, scope: str) -> int:
        return int(self.scope_levels.get(scope, self.scope_levels.get("all", logging.WARNING)))

    def should_trace_filesystem(self) -> bool:
        return self.scope_level("xconv2") <= logging.INFO

    def should_trace_file_io(self) -> bool:
        return self.scope_level("xconv2") <= logging.DEBUG


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

    _logging_configuration = RemoteLoggingConfiguration(scope_levels=normalize_scope_levels(None))

    def __init__(self, filesystem: Any) -> None:
        self.filesystem = filesystem
        self._open_handles: list[Any] = []

    def _close_open_handles(self) -> None:
        """Close any file handles retained for lazy remote field access."""
        while self._open_handles:
            handle = self._open_handles.pop()
            close = getattr(handle, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    @classmethod
    def configure_logging(
        cls,
        *,
        scope_levels: dict[str, int | str] | None = None,
    ) -> RemoteLoggingConfiguration:
        """Update shared runtime logging settings for remote access."""
        if scope_levels is None:
            return cls._logging_configuration
        cls._logging_configuration = RemoteLoggingConfiguration(
            scope_levels=normalize_scope_levels(scope_levels),
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

        All remote reads are performed from open file handles so downstream
        readers do not need to rediscover datasets via filesystem-specific
        glob/list operations.
        """
        normalized = normalize_remote_datasets_for_cf_read(
            descriptor=descriptor,
            datasets=datasets,
        )
        logging.info(f'Attempting to open remote dataset(s) with reader: {normalized}')
        self._close_open_handles()
        try:
            if isinstance(normalized, list):
                self._open_handles = [self.filesystem.open(path, "rb") for path in normalized]
                opened: Any = list(self._open_handles)
            else:
                handle = self.filesystem.open(normalized, "rb")
                self._open_handles = [handle]
                opened = handle
            logging.info(f'Attempting to open remote dataset(s) with reader: {opened}')
            return reader(opened)
        except Exception:
            self._close_open_handles()
            raise

    def close(self) -> None:
        """Best-effort cleanup for filesystem and jump-host resources."""
        self._close_open_handles()

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
# Utility functions: filesystem construction
# ---------------------------------------------------------------------------


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
        endpoint_url = _value_from_keys(details, "url") or _value_from_keys(remote, "url")
        if not endpoint_url:
            raise ValueError("S3 remote configuration requires an endpoint URL")

        key = _value_from_keys(details, "accessKey", "access_key") or _value_from_keys(remote, "access_key")
        secret = _value_from_keys(details, "secretKey", "secret_key") or _value_from_keys(remote, "secret_key")

        storage_options: dict[str, Any] = {"anon": not (key and secret)}
        if key and secret:
            storage_options["key"] = key
            storage_options["secret"] = secret
        storage_options["client_kwargs"] = {"endpoint_url": endpoint_url}

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
        remote_python = (
            _value_from_keys(details, "remote_python", "python", "python_command")
            or _value_from_keys(remote, "remote_python", "python", "python_command")
        )
        login_shell_value: Any = None
        if "login_shell" in details:
            login_shell_value = details.get("login_shell")
        elif "login_shell" in remote:
            login_shell_value = remote.get("login_shell")
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
        if remote_python:
            storage_options["remote_python"] = remote_python
        if login_shell_value is not None:
            if isinstance(login_shell_value, str):
                storage_options["login_shell"] = login_shell_value.strip().lower() in {"1", "true", "yes", "on"}
            else:
                storage_options["login_shell"] = bool(login_shell_value)

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
    cache_dir: str | None = None
    if isinstance(cache, dict):
        disk_mode = str(cache.get("disk_mode", "Disabled")).strip().lower()
        if disk_mode in {"blocks", "files"}:
            raw_location = str(cache.get("disk_location", "")).strip() or "TMP"
            cache_path = Path(raw_location).expanduser()
            prune_disk_cache(
                cache_path,
                limit_bytes=int(cache.get("disk_limit_gb", 0) or 0) * 1024 * 1024 * 1024,
                expiry_seconds=parse_disk_expiry_seconds(cache.get("disk_expiry")),
                log=log,
            )
            cache_dir = str(cache_path)

    if spec.protocol == "http":
        return RemoteFileSystemFactory(
            url=spec.root_path,
            cache_dir=cache_dir,
            credentials=dict(spec.storage_options),
        ).fs

    if spec.protocol == "sftp":
        host = str(spec.storage_options.get("host", "")).strip()
        if not host:
            raise ValueError("SFTP spec is missing host")

        user = str(spec.storage_options.get("username", "")).strip()
        port = spec.storage_options.get("port")
        authority = f"{user + '@' if user else ''}{host}"
        if isinstance(port, int):
            authority = f"{authority}:{port}"

        root_path = spec.root_path or "."
        remote_path = root_path if root_path.startswith("/") else f"/{root_path}"
        credentials = dict(spec.storage_options)
        if spec.proxy_jump:
            credentials["proxy_jump"] = spec.proxy_jump

        return RemoteFileSystemFactory(
            url=f"ssh://{authority}{remote_path}",
            cache_dir=cache_dir,
            credentials=credentials,
        ).fs

    if spec.protocol == "s3":
        endpoint_url = str(spec.storage_options.get("client_kwargs", {}).get("endpoint_url", "")).strip()
        if not endpoint_url:
            raise ValueError("S3 filesystem requires storage_options.client_kwargs.endpoint_url")

        # Extract endpoint host and optional path-style bucket hint.
        normalized_endpoint = endpoint_url if "://" in endpoint_url else f"https://{endpoint_url}"
        parsed_endpoint = urlparse(normalized_endpoint)

        endpoint_host = (parsed_endpoint.netloc or "").strip()
        endpoint_path_parts = [part for part in parsed_endpoint.path.split("/") if part]

        if not endpoint_host:
            # Handle schemeless values such as "host:port/bucket".
            raw_parts = [part for part in endpoint_url.strip("/").split("/") if part]
            if raw_parts:
                endpoint_host = raw_parts[0]
                endpoint_path_parts = raw_parts[1:]

        if not endpoint_host:
            raise ValueError(f"Invalid S3 endpoint URL: {endpoint_url!r}")

        # Delegate to factory which handles caching and ShimmyFS wrapping consistently.
        bucket_hint = endpoint_path_parts[0] if endpoint_path_parts else "bucket"
        synthetic_url = f"s3://{endpoint_host}/{bucket_hint}"
        return RemoteFileSystemFactory(
            url=synthetic_url,
            cache_dir=cache_dir,
            credentials=dict(spec.storage_options),
        ).fs

    raise ValueError(f"Unsupported filesystem protocol for create_filesystem: {spec.protocol!r}")
