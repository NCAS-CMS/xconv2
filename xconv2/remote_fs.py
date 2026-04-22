import logging
from contextlib import contextmanager
from pathlib import PurePosixPath
from pathlib import Path
from time import perf_counter
from typing import Any, Callable
from urllib.parse import urlparse

import fsspec
from fsspec.implementations.cached import CachingFileSystem

try:
    from p5rem import bootstrap_session
except ImportError:
    bootstrap_session = None


logger = logging.getLogger(__name__)


class _CacheAwareCatRangesFSProxy:
    """Per-handle FS adapter that serves cat_ranges from the handle cache.

    fsspec CachingFileSystem returns the underlying remote file handle and
    patches ``handle.cache`` for normal read/seek calls, but ``handle.fs`` still
    points at the raw backend filesystem. pyfive's remote bulk path calls
    ``handle.fs.cat_ranges(...)`` directly, which bypasses the patched cache.

    This adapter preserves all FS behavior via delegation and only intercepts
    cat_ranges for the current handle/path.
    """

    def __init__(self, fs: Any, handle: Any) -> None:
        self._fs = fs
        self._handle = handle
        self.protocol = getattr(fs, "protocol", None)

    def cat_ranges(
        self,
        paths,
        starts,
        ends,
        max_gap: Any = None,
        on_error: str = "return",
        **kwargs,
    ):
        if max_gap is not None:
            return self._fs.cat_ranges(
                paths,
                starts,
                ends,
                max_gap=max_gap,
                on_error=on_error,
                **kwargs,
            )

        if not isinstance(paths, list):
            raise TypeError
        if not isinstance(starts, list):
            starts = [starts] * len(paths)
        if not isinstance(ends, list):
            ends = [ends] * len(paths)
        if len(starts) != len(paths) or len(ends) != len(paths):
            raise ValueError(
                "cat_ranges argument length mismatch: "
                f"len(paths)={len(paths)}, len(starts)={len(starts)}, len(ends)={len(ends)}"
            )

        cache = getattr(self._handle, "cache", None)
        fetch = getattr(cache, "_fetch", None)
        handle_path = getattr(self._handle, "path", None)

        if not callable(fetch) or not isinstance(handle_path, str):
            return self._fs.cat_ranges(paths, starts, ends, on_error=on_error, **kwargs)

        out = []
        for path, start, end in zip(paths, starts, ends):
            try:
                if path == handle_path:
                    out.append(fetch(start, end))
                else:
                    out.append(self._fs.cat_file(path, start, end, **kwargs))
            except Exception as exc:
                if on_error == "return":
                    out.append(exc)
                else:
                    raise
        return out

    def __getattr__(self, name: str) -> Any:
        return getattr(self._fs, name)


@contextmanager
def _forward_logger_output(
    logger_names: list[str],
    callback: Callable[[str], None] | None,
) -> None:
    """Forward logs from the named loggers to a callback for live UI updates."""
    if callback is None:
        yield
        return

    handlers: list[tuple[logging.Logger, logging.Handler, int]] = []

    class _CallbackHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            try:
                message = self.format(record)
                if message:
                    callback(message)
            except Exception:
                return

    # Also track child loggers to ensure propagation
    child_loggers_to_enable: list[logging.Logger] = []

    for name in logger_names:
        target_logger = logging.getLogger(name)
        handler = _CallbackHandler()
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
        previous_level = target_logger.level
        target_logger.addHandler(handler)
        target_logger.setLevel(logging.DEBUG)
        target_logger.propagate = True  # Ensure propagation is enabled
        handlers.append((target_logger, handler, previous_level))
        
        # Also enable propagation on common child loggers
        for child_name in ["paramiko.transport", "p5rem.bootstrap"]:
            if child_name.startswith(name + "."):
                child_logger = logging.getLogger(child_name)
                child_logger.propagate = True
                child_logger.setLevel(logging.DEBUG)
                child_loggers_to_enable.append(child_logger)

    try:
        yield
    finally:
        for target_logger, handler, previous_level in handlers:
            target_logger.removeHandler(handler)
            target_logger.setLevel(previous_level)


class ShimmyFS(fsspec.AbstractFileSystem):
    """
    Proxy a filesystem while forcing a default block size on open().
    This ensures user code and fsspec's CachingFileSystem stay in sync on block metadata, 
    so that when users select their own blocksize parameters, they stay consistent with
    the cache's expectations and avoid silent cache misses or redundant reads.     
    """

    def __init__(
        self,
        fs,
        block_size: int = 2 * 1024 * 1024,
        root_path: str | None = None,
        cache_path: str | None = None,
    ):
        self.fs = fs
        self.block_size = block_size
        self.root_path = root_path
        self.cache_path = cache_path
        # Ensure protocol is exposed for compatibility
        self.protocol = getattr(fs, "protocol", None)

    def _apply_block_size(self, path: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Force a consistent block_size for cache coherence across code paths."""
        adjusted = dict(kwargs)
        requested_block_size = adjusted.get("block_size")
        if requested_block_size is None:
            adjusted["block_size"] = self.block_size
        elif requested_block_size != self.block_size:
            logger.warning(
                "Overriding block_size from %s to %s for %s",
                requested_block_size,
                self.block_size,
                path,
            )
            adjusted["block_size"] = self.block_size
        return adjusted

    def open(self, path, mode: str = "rb", **kwargs):
        adjusted_kwargs = self._apply_block_size(path, kwargs)
        logger.info("Opening %s with block_size=%s", path, adjusted_kwargs["block_size"])
        handle = self.fs.open(path, mode, **adjusted_kwargs)

        # CachingFileSystem patches handle.cache but leaves handle.fs pointing
        # at the raw backend FS; pyfive calls handle.fs.cat_ranges directly.
        fs = getattr(handle, "fs", None)
        if fs is not None and not isinstance(fs, _CacheAwareCatRangesFSProxy):
            cache = getattr(handle, "cache", None)
            if callable(getattr(cache, "_fetch", None)):
                handle.fs = _CacheAwareCatRangesFSProxy(fs, handle)

        return handle

    def cat_ranges(
        self,
        paths,
        starts,
        ends,
        max_gap: Any = None,
        on_error: str = "return",
        **kwargs,
    ):
        """Read byte ranges while preserving kwargs for cat_file/open.

        fsspec's AbstractFileSystem.cat_ranges currently drops kwargs when
        calling cat_file(). We need block_size to flow through to keep cache
        block metadata consistent across serial and cat_ranges code paths.
        This is a bug in fsspec: https://github.com/fsspec/filesystem_spec/issues/2016
        """
        if max_gap is not None:
            raise NotImplementedError
        if not isinstance(paths, list):
            raise TypeError
        if not isinstance(starts, list):
            starts = [starts] * len(paths)
        if not isinstance(ends, list):
            ends = [ends] * len(paths)
        if len(starts) != len(paths) or len(ends) != len(paths):
            raise ValueError(
                "cat_ranges argument length mismatch: "
                f"len(paths)={len(paths)}, len(starts)={len(starts)}, len(ends)={len(ends)}"
            )

        out = []
        for path, start, end in zip(paths, starts, ends):
            adjusted_kwargs = self._apply_block_size(path, kwargs)
            try:
                out.append(self.fs.cat_file(path, start, end, **adjusted_kwargs))
            except Exception as exc:
                if on_error == "return":
                    out.append(exc)
                else:
                    raise
        return out

    def glob(self, path, **kwargs):
        matches = self.fs.glob(path, **kwargs)
        if matches:
            return matches

        # cfdm/cf probes datasets via filesystem.glob even for exact paths.
        # Some wrapped remote filesystems return [] for exact keys unless a
        # wildcard is present, so fall back to exists(path) in that case.
        if any(token in str(path) for token in "*?["):
            return matches

        try:
            if self.fs.exists(path):
                return [path]
        except Exception:
            return matches

        return matches

    def ls(self, path, detail=True, **kwargs):
        """List objects at path, forwarding directly to the wrapped filesystem.
        
        This explicit implementation ensures we don't use AbstractFileSystem's
        implementation, which breaks with CachingFileSystem. Instead, we directly
        forward to the wrapped filesystem's ls() method.
        """
        return self.fs.ls(path, detail=detail, **kwargs)

    def __getattr__(self, name):
        return getattr(self.fs, name)

    def list_files(self):
        """Returns a list of paths matching the glob pattern in the URL."""
        if not self.root_path:
            return []

        if "*" in self.root_path:
            start = perf_counter()
            paths = self.fs.glob(self.root_path)
            paths = [
                p
                for p in paths
                if "?" not in p and p.lower().endswith((".nc", ".h5", ".hdf5"))
            ]
            logger.info("Globbed %d files in %.2f seconds", len(paths), perf_counter() - start)
            return paths

        return [self.root_path]

    def flush_cache(self):
        """Flushes the local cache for the current file. This is a no-op if caching is disabled."""
        if self.cache_path:
            import os
            import shutil

            if os.path.exists(self.cache_path):
                shutil.rmtree(self.cache_path)

            logger.info("Cache flushed.")
        else:
            logger.warning("Caching is disabled; nothing to flush.")

    def get_file_like(self, path=None):
        """Returns a file-like object for the given path."""
        target_path = path or self.root_path


        return self.fs.open(target_path, "rb")


class P5RemFilesystem:
    """Filesystem wrapper around a p5rem session for SSH/SFTP access."""

    protocol = "ssh"

    def __init__(self, session):
        """Initialize with a p5remSession instance."""
        self.session = session

    @staticmethod
    def _entry_path(parent: str, child: str) -> str:
        """Join remote POSIX-like paths without introducing double slashes."""
        child_clean = str(child).strip()
        if not child_clean:
            return str(parent)
        if child_clean.startswith("/"):
            return child_clean
        base = str(parent).strip()
        if base in {"", "."}:
            return child_clean
        if base == "/":
            return f"/{child_clean}"
        return str(PurePosixPath(base) / child_clean)

    @staticmethod
    def _is_dir_from_stat(info: dict[str, object]) -> bool:
        """Best-effort directory detection from heterogeneous stat payloads."""
        type_value = str(info.get("type", "")).strip().lower()
        if type_value in {"dir", "directory"}:
            return True
        if type_value == "file":
            return False
        return bool(info.get("is_dir", False))

    def ls(self, path, detail=True, **kwargs):
        """List directory contents."""
        entries = self.session.list(path)
        if not detail:
            names: list[str] = []
            for entry in entries:
                if isinstance(entry, dict) and "name" in entry:
                    names.append(str(entry["name"]))
                else:
                    names.append(self._entry_path(str(path), str(entry)))
            return names

        normalized: list[dict[str, object]] = []
        for entry in entries:
            if isinstance(entry, dict) and "name" in entry:
                normalized.append(entry)
                continue

            entry_path = self._entry_path(str(path), str(entry))
            stat_info: dict[str, object] = {}
            try:
                stat_payload = self.session.stat(entry_path)
                if isinstance(stat_payload, dict):
                    stat_info = stat_payload
            except Exception:
                stat_info = {}

            is_dir = self._is_dir_from_stat(stat_info)
            size_value = stat_info.get("size")
            size: int | None = int(size_value) if isinstance(size_value, int) else None
            normalized.append(
                {
                    "name": entry_path,
                    "size": size,
                    "type": "directory" if is_dir else "file",
                }
            )

        return normalized

    def open(self, path, mode="rb", **kwargs):
        """Open a file for reading."""
        if "w" in mode or "a" in mode:
            raise ValueError("p5rem filesystem is read-only")
        return self.session.open(path)

    def exists(self, path, **kwargs):
        """Check if a path exists."""
        try:
            self.session.stat(path)
            return True
        except Exception:
            return False

    def isdir(self, path, **kwargs):
        """Check if a path is a directory."""
        try:
            info = self.session.stat(path)
            return info.get("type") == "dir"
        except Exception:
            return False

    def isfile(self, path, **kwargs):
        """Check if a path is a file."""
        try:
            info = self.session.stat(path)
            return info.get("type") == "file"
        except Exception:
            return False

    def stat(self, path, **kwargs):
        """Get file information."""
        return self.session.stat(path)

    def glob(self, path, **kwargs):
        """Glob is not implemented for p5rem."""
        raise NotImplementedError("glob is not implemented for p5rem filesystem")

    def close(self):
        """Close the session."""
        if hasattr(self.session, "close"):
            self.session.close()


def _p5rem_startup_error_details(session: object, exc: Exception) -> str:
    """Build an actionable startup error string when the remote p5rem server exits."""
    parts = [f"{exc.__class__.__name__}: {exc}"]
    proc = getattr(session, "process", None)
    if proc is None:
        return "; ".join(parts)

    poll = getattr(proc, "poll", None)
    rc: int | None = None
    if callable(poll):
        try:
            rc = poll()
        except Exception:
            rc = None
    if rc is not None:
        parts.append(f"remote server exited with status {rc}")

        stderr = getattr(proc, "stderr", None)
        if stderr is not None:
            try:
                raw = stderr.read()
                if isinstance(raw, bytes):
                    text = raw.decode("utf-8", errors="replace").strip()
                else:
                    text = str(raw).strip()
                if text:
                    parts.append(f"remote stderr: {text}")
            except Exception:
                pass

    return "; ".join(parts)


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


def _create_sftp_via_jump(storage_options: dict) -> object:
    """Create an SFTP filesystem tunnelled through a ProxyJump host."""
    import paramiko  # type: ignore

    proxy_jump = str(storage_options.get("proxy_jump") or "").strip()
    jump_user_override, jump_alias, jump_port = _parse_proxy_jump(proxy_jump)

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

    target_user = str(storage_options.get("username", "")) or None
    target_key = str(storage_options.get("key_filename", "")) or None
    explicit_jump_user = str(storage_options.get("proxyjump_username", "")) or None
    explicit_jump_password = str(storage_options.get("proxyjump_password", "")) or None
    effective_jump_user = explicit_jump_user or jump_user_override or jump_resolved_user or target_user
    effective_jump_key = jump_key_filename or target_key

    jump_connect: dict = {"hostname": jump_hostname, "port": jump_port}
    if effective_jump_user:
        jump_connect["username"] = effective_jump_user
    if explicit_jump_password:
        jump_connect["password"] = explicit_jump_password
    if effective_jump_key:
        jump_connect["key_filename"] = effective_jump_key

    jump_client = paramiko.SSHClient()
    jump_client.load_system_host_keys()
    jump_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    jump_client.connect(**jump_connect)

    transport = jump_client.get_transport()
    if transport is None:
        jump_client.close()
        raise RuntimeError(f"Could not establish transport to jump host {jump_hostname!r}")

    target_host = str(storage_options["host"])
    channel = transport.open_channel("direct-tcpip", (target_host, 22), ("", 0))

    connect_kwargs = dict(storage_options)
    connect_kwargs.pop("proxy_jump", None)
    connect_kwargs.pop("proxyjump_username", None)
    connect_kwargs.pop("proxyjump_password", None)
    connect_kwargs["sock"] = channel
    fs = fsspec.filesystem("sftp", **connect_kwargs)
    fs._xconv_jump_client = jump_client
    return fs


class RemoteFileSystemFactory:
    """
    Wrap key remote filesystem access behavior.
    """

    def __init__(self,
        url: str,
        block_size: int = 2 * 1024 * 1024,
        cache_dir: str | None = None,
        credentials: dict | None = None,
        filesystem_mode: str = "CachingFileSystem",
        loglevel: int = logging.WARNING ,
        log_callback: Callable[[str], None] | None = None,
    ) -> None:
        """
        Instantiate a RemoteFileSystemFactory for a given URL, 
        which may be a local path or a remote URL (e.g. S3).

        :param url: A remote URL using one of S3/ HTTP/ or SSHFS
        :type url: str

        :param block_size: fsspec block read size, defaults to 2MiB.
        :type block_size: int, optional
        
        :param cache_dir: The location of an on disk cache for remote files,
        defaults to None (no caching). If provided, remote files will be cached locally
        to speed up repeated access. The cache directory will be created if it does not exist.
        :type cache_dir: str, optional
        
        :param credentials: These should be any credentials required for accessing the remote 
        filesystem, defaults to None (expect anonymous access).
        For S3, this should be a dict with 'key' and 'secret' entries.
        :type credentials: dict | None, optional
        
        :param filesystem_mode: type of fsspec filesystem caching, defaults to "CachingFileSystem".
        :type filesystem_mode: str, optional
        :raises ValueError: If an unsupported filesystem_mode is provided.

        :param loglevel: Logging level for this class, defaults to logging.WARNING.
        :type loglevel: int, optional
        
        """
        logger.setLevel(loglevel)
        self._log_callback = log_callback

        self.url = url
        self.block_size = block_size
        self.use_cache = cache_dir is not None
        self.cache_path = cache_dir

        mode = filesystem_mode.lower()
        fstypes = {"cachingfilesystem"}
        if mode not in fstypes:
            raise ValueError(
                f"Unsupported filesystem_mode={filesystem_mode!r}; expected {fstypes}'"
            )
        self.filesystem_mode = mode

        scheme = urlparse(url).scheme.lower()
        valid_schemes = {"s3", "s3a", "http", "https", "ssh", "sftp"}
        if scheme not in valid_schemes:
            raise ValueError(
                f"Unsupported URL scheme {scheme!r} in URL {url!r}; expected one of {', '.join(valid_schemes)}"
            )

        storage_options = dict(credentials or {})

        match scheme:

            case "s3" | "s3a":
                if credentials is None:
                    storage_options = {"anon": True}
                else:
                    storage_options = dict(credentials)

                parsed = urlparse(url)
                host = parsed.netloc
                hostname = (parsed.hostname or "").strip().lower()
                path_parts = [part for part in parsed.path.split("/") if part]

                # For endpoint-style URLs like:
                #   s3://<endpoint-host>/<bucket>/<key>
                # normalize to:
                #   <bucket>/<key>
                # and pass the full endpoint host separately.
                client_kwargs = dict(storage_options.get("client_kwargs", {}))
                explicit_endpoint = bool(str(client_kwargs.get("endpoint_url", "")).strip())
                localhost_like = hostname in {"localhost", "127.0.0.1", "::1"}

                # Accept endpoint-style URLs for cloud/object stores and local S3 emulators.
                # We still require at least one path segment for bucket extraction.
                is_endpoint_style = bool(host) and len(path_parts) >= 1 and (
                    explicit_endpoint
                    or localhost_like
                    or ("." in hostname and "s3" in hostname)
                )
                if is_endpoint_style:
                    bucket = path_parts[0]
                    key = "/".join(path_parts[1:])
                    resolved_url = f"{bucket}/{key}" if key else f"{bucket}"
                    client_kwargs.setdefault("endpoint_url", f"https://{host}")
                    storage_options["client_kwargs"] = client_kwargs
                    msg = f"Normalized S3 URL {url} -> {resolved_url} with endpoint_url={storage_options['client_kwargs']['endpoint_url']}"
                    logger.info(msg)
                else:
                    logging.critical(f"URL {url} does not appear to be in endpoint-style format; only endpoint-style S3 URLs are supported.")
                    raise NotImplementedError("Only endpoint-style S3 URLs are supported in this implementation.")  
                self.resolved_url = resolved_url
                self.end_point_url = storage_options["client_kwargs"]["endpoint_url"]
                base_fs = fsspec.filesystem("s3", **storage_options)
                self.root_path = resolved_url

            case "http" | "https":
                base_fs = fsspec.filesystem("http", **storage_options)
                self.root_path = url

            case "ssh" | "sftp":
                if bootstrap_session is None:
                    raise ImportError("p5rem is required for SSH/SFTP support. Install it with: pip install p5rem")
                
                parsed = urlparse(url)
                if not parsed.hostname:
                    raise ValueError(f"Missing SSH hostname in URL {url!r}")

                host = parsed.hostname
                username = parsed.username or str(storage_options.get("username", "")).strip() or None
                password = parsed.password or str(storage_options.get("password", "")).strip() or None
                port = parsed.port or storage_options.get("port")
                key_filename = str(storage_options.get("key_filename", "")).strip() or None
                remote_python = str(storage_options.get("remote_python", "")).strip() or "python3"
                login_shell_raw = storage_options.get("login_shell")
                if isinstance(login_shell_raw, str):
                    login_shell = login_shell_raw.strip().lower() in {"1", "true", "yes", "on"}
                else:
                    login_shell = bool(login_shell_raw)

                self.root_path = parsed.path or "."
                
                # Bootstrap p5rem session with the hostname and credentials.
                logger.info(
                    "Bootstrapping p5rem session to %s:%s (use_cache=%s, remote_python=%s, login_shell=%s)",
                    host,
                    port or 22,
                    self.use_cache,
                    remote_python,
                    login_shell,
                )
                if self._log_callback is not None:
                    self._log_callback(
                        f"Starting SSH handshake to {host}:{port or 22} as {username or '<default-user>'}"
                    )
                    self._log_callback("Checking/starting remote worker process...")
                try:
                    bootstrap_started = perf_counter()
                    with _forward_logger_output(["paramiko", "p5rem"], self._log_callback):
                        session = bootstrap_session(
                            host=host,
                            username=username,
                            password=password,
                            port=port,
                            key_filename=key_filename,
                            remote_python=remote_python,
                            login_shell=login_shell,
                            use_cache=self.use_cache,
                            timeout=10.0,
                        )
                    bootstrap_elapsed = max(0.0, perf_counter() - bootstrap_started)
                    if self._log_callback is not None:
                        self._log_callback(
                            f"Remote worker bootstrap complete in {bootstrap_elapsed:.2f}s"
                        )

                    # Validate the remote server immediately so startup failures
                    # (e.g. missing deps/remote interpreter issues) surface as
                    # clear connection errors instead of deferred EOF during ls().
                    session.heartbeat()
                    
                    base_fs = P5RemFilesystem(session)
                except Exception as exc:
                    detail = _p5rem_startup_error_details(locals().get("session"), exc)
                    raise RuntimeError(f"Failed to bootstrap p5rem session to {host}: {detail}") from exc

            case _:
                raise NotImplementedError(f"Unsupported URL scheme {scheme!r} in URL {url!r}; expected one of {', '.join(valid_schemes)}")

        # Apply CachingFileSystem wrapping only for non-p5rem filesystems
        # P5RemFilesystem has its own caching via diskcache in separate directory
        is_p5rem = isinstance(base_fs, P5RemFilesystem)
        
        if self.use_cache and not is_p5rem:
            # Use dedicated fsspec subdirectory for clarity
            fsspec_cache_dir = str(Path(cache_dir) / "fsspec")
            Path(fsspec_cache_dir).mkdir(parents=True, exist_ok=True)
            wrapped_fs = CachingFileSystem(
                fs=base_fs,
                cache_storage=fsspec_cache_dir,
                blocksize=block_size,
                check_files=False,
            )
            logger.info("Caching enabled for fsspec filesystems in: %s (blocksize=%s)", fsspec_cache_dir, block_size)
        else:
            wrapped_fs = base_fs
            if not is_p5rem:
                logger.info("Caching disabled; using base filesystem directly")
            else:
                logger.info("Caching disabled in p5rem session")

        self.fs = ShimmyFS(
            wrapped_fs,
            block_size=block_size,
            root_path=self.root_path,
            cache_path=self.cache_path,
        )

    def __getattr__(self, name):
        return getattr(self.fs, name)

