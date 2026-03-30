import logging
from pathlib import Path
from time import perf_counter
from urllib.parse import urlparse

import fsspec
from fsspec.implementations.cached import CachingFileSystem


logger = logging.getLogger(__name__)


class ShimmyFS(fsspec.AbstractFileSystem):
    """Proxy a filesystem while forcing a default block size on open()."""

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

    def open(self, path, mode: str = "rb", **kwargs):
        requested_block_size = kwargs.get("block_size")
        if requested_block_size is None:
            kwargs["block_size"] = self.block_size
        elif requested_block_size != self.block_size:
            logger.warning(
                "Overriding block_size from %s to %s for %s",
                requested_block_size,
                self.block_size,
                path,
            )
            kwargs["block_size"] = self.block_size

        logger.info("Opening %s with block_size=%s", path, kwargs["block_size"])
        return self.fs.open(path, mode, **kwargs)

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
                parsed = urlparse(url)
                if not parsed.hostname:
                    raise ValueError(f"Missing SSH hostname in URL {url!r}")

                if "proxy_jump" not in storage_options and storage_options.get("proxyjump"):
                    storage_options["proxy_jump"] = storage_options["proxyjump"]

                storage_options["host"] = parsed.hostname
                if parsed.username and "username" not in storage_options:
                    storage_options["username"] = parsed.username
                if parsed.password and "password" not in storage_options:
                    storage_options["password"] = parsed.password
                if parsed.port and "port" not in storage_options:
                    storage_options["port"] = parsed.port

                self.root_path = parsed.path or "."

                if storage_options.get("proxy_jump"):
                    base_fs = _create_sftp_via_jump(storage_options)
                else:
                    base_fs = fsspec.filesystem("sftp", **storage_options)

            case _:
                raise NotImplementedError(f"Unsupported URL scheme {scheme!r} in URL {url!r}; expected one of {', '.join(valid_schemes)}")

        if self.use_cache:
            wrapped_fs = CachingFileSystem(
                fs=base_fs,
                cache_storage=cache_dir,
                blocksize=block_size,
                check_files=False,
            )
            logger.info("Caching enabled using cache dir: %s with blocksize=%s", cache_dir, block_size)
        else:
            wrapped_fs = base_fs
            logger.info("Caching disabled; using base filesystem directly")

        self.fs = ShimmyFS(
            wrapped_fs,
            block_size=block_size,
            root_path=self.root_path,
            cache_path=self.cache_path,
        )

    def __getattr__(self, name):
        return getattr(self.fs, name)

