from __future__ import annotations

import logging
from pathlib import Path

import pytest

from xconv2 import remote_fs


class _DummyFS:
    def __init__(self) -> None:
        self.open_calls: list[tuple[str, str, dict]] = []
        self.glob_calls: list[str] = []
        self.existing_paths: set[str] = set()

    def open(self, path: str, mode: str = "rb", **kwargs):
        self.open_calls.append((path, mode, dict(kwargs)))
        return {"path": path, "mode": mode, "kwargs": kwargs}

    def glob(self, pattern: str):
        self.glob_calls.append(pattern)
        return []

    def exists(self, path: str) -> bool:
        return path in self.existing_paths


def test_shimmy_open_injects_default_block_size() -> None:
    base = _DummyFS()
    fs = remote_fs.ShimmyFS(base, block_size=8192)

    fs.open("some/path.nc")

    assert len(base.open_calls) == 1
    _, _, kwargs = base.open_calls[0]
    assert kwargs["block_size"] == 8192


def test_shimmy_open_overrides_non_default_block_size() -> None:
    base = _DummyFS()
    fs = remote_fs.ShimmyFS(base, block_size=16384)

    fs.open("some/path.nc", block_size=1024)

    _, _, kwargs = base.open_calls[0]
    assert kwargs["block_size"] == 16384


def test_shimmy_list_files_filters_globbed_paths() -> None:
    class _GlobFS(_DummyFS):
        def glob(self, pattern: str):
            super().glob(pattern)
            return [
                "data/a.nc",
                "data/b.h5",
                "data/c.hdf5",
                "data/d.txt",
                "data/e?.nc",
            ]

    fs = remote_fs.ShimmyFS(_GlobFS(), root_path="data/*")

    assert fs.list_files() == ["data/a.nc", "data/b.h5", "data/c.hdf5"]


def test_shimmy_list_files_returns_root_path_without_glob() -> None:
    fs = remote_fs.ShimmyFS(_DummyFS(), root_path="https://example/data.nc")
    assert fs.list_files() == ["https://example/data.nc"]


def test_shimmy_glob_falls_back_to_exists_for_exact_path() -> None:
    base = _DummyFS()
    base.existing_paths.add("bucket/file.nc")
    fs = remote_fs.ShimmyFS(base)

    assert fs.glob("bucket/file.nc") == ["bucket/file.nc"]


def test_shimmy_glob_does_not_fallback_for_patterns() -> None:
    base = _DummyFS()
    base.existing_paths.add("bucket/file.nc")
    fs = remote_fs.ShimmyFS(base)

    assert fs.glob("bucket/*.nc") == []


def test_shimmy_flush_cache_removes_cache_dir(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "cache-index").write_text("x", encoding="utf-8")

    fs = remote_fs.ShimmyFS(_DummyFS(), cache_path=str(cache_dir))
    fs.flush_cache()

    assert not cache_dir.exists()


def test_parse_proxy_jump_variants() -> None:
    assert remote_fs._parse_proxy_jump("alice@jump.example:2200") == ("alice", "jump.example", 2200)
    assert remote_fs._parse_proxy_jump("jump.example") == (None, "jump.example", 22)
    assert remote_fs._parse_proxy_jump("jump.example:bad") == (None, "jump.example:bad", 22)


def test_factory_http_without_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict]] = []

    base = _DummyFS()

    def _fake_filesystem(protocol: str, **kwargs):
        calls.append((protocol, dict(kwargs)))
        return base

    monkeypatch.setattr(remote_fs.fsspec, "filesystem", _fake_filesystem)

    factory = remote_fs.RemoteFileSystemFactory(
        url="https://example.org/path/file.nc",
        cache_dir=None,
    )

    assert calls == [("http", {})]
    assert isinstance(factory.fs, remote_fs.ShimmyFS)
    assert factory.root_path == "https://example.org/path/file.nc"
    assert factory.get_file_like()["path"] == "https://example.org/path/file.nc"


def test_factory_wraps_with_caching_filesystem(monkeypatch: pytest.MonkeyPatch) -> None:
    base = _DummyFS()
    cache_calls: list[dict] = []

    def _fake_filesystem(protocol: str, **kwargs):
        assert protocol == "http"
        return base

    class _FakeCachingFS(_DummyFS):
        pass

    wrapped = _FakeCachingFS()

    def _fake_caching_filesystem(**kwargs):
        cache_calls.append(dict(kwargs))
        return wrapped

    monkeypatch.setattr(remote_fs.fsspec, "filesystem", _fake_filesystem)
    monkeypatch.setattr(remote_fs, "CachingFileSystem", _fake_caching_filesystem)

    factory = remote_fs.RemoteFileSystemFactory(
        url="https://example.org/path/file.nc",
        cache_dir="/tmp/cache-dir",
    )

    assert len(cache_calls) == 1
    assert cache_calls[0]["fs"] is base
    assert cache_calls[0]["cache_storage"] == "/tmp/cache-dir/fsspec"
    assert cache_calls[0]["check_files"] is False
    assert factory.fs.fs is wrapped


def test_factory_s3_endpoint_style_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict]] = []
    base = _DummyFS()

    def _fake_filesystem(protocol: str, **kwargs):
        calls.append((protocol, dict(kwargs)))
        return base

    monkeypatch.setattr(remote_fs.fsspec, "filesystem", _fake_filesystem)

    factory = remote_fs.RemoteFileSystemFactory(
        url="s3://uor-aces-o.s3-ext.jc.rl.ac.uk/bucket/key.nc",
        credentials={"key": "abc", "secret": "def"},
        cache_dir=None,
    )

    assert factory.root_path == "bucket/key.nc"
    assert len(calls) == 1
    protocol, opts = calls[0]
    assert protocol == "s3"
    assert opts["key"] == "abc"
    assert opts["secret"] == "def"
    assert opts["client_kwargs"]["endpoint_url"] == "https://uor-aces-o.s3-ext.jc.rl.ac.uk"


def test_factory_s3_localhost_endpoint_with_explicit_endpoint_url(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict]] = []
    base = _DummyFS()

    def _fake_filesystem(protocol: str, **kwargs):
        calls.append((protocol, dict(kwargs)))
        return base

    monkeypatch.setattr(remote_fs.fsspec, "filesystem", _fake_filesystem)

    factory = remote_fs.RemoteFileSystemFactory(
        url="s3://localhost:50686/bucket/key.nc",
        credentials={
            "key": "abc",
            "secret": "def",
            "client_kwargs": {"endpoint_url": "http://localhost:50686"},
        },
        cache_dir=None,
    )

    assert factory.root_path == "bucket/key.nc"
    assert len(calls) == 1
    protocol, opts = calls[0]
    assert protocol == "s3"
    assert opts["client_kwargs"]["endpoint_url"] == "http://localhost:50686"


def test_factory_ssh_uses_p5rem_bootstrap(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bootstrap_calls: list[dict] = []

    class _FakeSession:
        def __init__(self) -> None:
            self._cache = None
            self.heartbeat_calls = 0

        def heartbeat(self):
            self.heartbeat_calls += 1
            return {"type": "HEARTBEAT"}

    fake_session = _FakeSession()

    def _fake_bootstrap_session(**kwargs):
        bootstrap_calls.append(dict(kwargs))
        return fake_session

    def _should_not_wrap(**kwargs):
        raise AssertionError("CachingFileSystem should not wrap p5rem filesystems")

    monkeypatch.setattr(remote_fs, "bootstrap_session", _fake_bootstrap_session)
    monkeypatch.setattr(remote_fs, "CachingFileSystem", _should_not_wrap)

    cache_dir = tmp_path / "cache"
    factory = remote_fs.RemoteFileSystemFactory(
        url="ssh://alice@myhost:2222/home/alice/data.nc",
        credentials={"password": "pw"},
        cache_dir=str(cache_dir),
    )

    assert factory.root_path == "/home/alice/data.nc"
    assert len(bootstrap_calls) == 1
    call = bootstrap_calls[0]
    assert call["host"] == "myhost"
    assert call["username"] == "alice"
    assert call["password"] == "pw"
    assert call["port"] == 2222
    assert call["use_cache"] is True

    assert isinstance(factory.fs.fs, remote_fs.P5RemFilesystem)
    assert fake_session._cache is None
    assert fake_session.heartbeat_calls == 1


def test_factory_ssh_without_cache_does_not_set_p5rem_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    bootstrap_calls: list[dict] = []

    class _FakeSession:
        def __init__(self) -> None:
            self._cache = None
            self.heartbeat_calls = 0

        def heartbeat(self):
            self.heartbeat_calls += 1
            return {"type": "HEARTBEAT"}

    fake_session = _FakeSession()

    def _fake_bootstrap_session(**kwargs):
        bootstrap_calls.append(dict(kwargs))
        return fake_session

    def _should_not_wrap(**kwargs):
        raise AssertionError("CachingFileSystem should not wrap p5rem filesystems")

    monkeypatch.setattr(remote_fs, "bootstrap_session", _fake_bootstrap_session)
    monkeypatch.setattr(remote_fs, "CachingFileSystem", _should_not_wrap)

    factory = remote_fs.RemoteFileSystemFactory(
        url="ssh://alice@myhost:2222/home/alice/data.nc",
        credentials={"password": "pw"},
        cache_dir=None,
    )

    assert factory.root_path == "/home/alice/data.nc"
    assert len(bootstrap_calls) == 1
    call = bootstrap_calls[0]
    assert call["host"] == "myhost"
    assert call["username"] == "alice"
    assert call["password"] == "pw"
    assert call["port"] == 2222
    assert call["use_cache"] is False

    assert isinstance(factory.fs.fs, remote_fs.P5RemFilesystem)
    assert fake_session._cache is None
    assert fake_session.heartbeat_calls == 1


def test_factory_ssh_passes_remote_python_and_login_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap_calls: list[dict] = []

    class _FakeSession:
        def heartbeat(self):
            return {"type": "HEARTBEAT"}

    def _fake_bootstrap_session(**kwargs):
        bootstrap_calls.append(dict(kwargs))
        return _FakeSession()

    monkeypatch.setattr(remote_fs, "bootstrap_session", _fake_bootstrap_session)

    remote_fs.RemoteFileSystemFactory(
        url="ssh://alice@myhost/home/alice/data.nc",
        credentials={
            "password": "pw",
            "remote_python": "conda run -n work26 python",
            "login_shell": "true",
        },
        cache_dir=None,
    )

    assert len(bootstrap_calls) == 1
    call = bootstrap_calls[0]
    assert call["remote_python"] == "conda run -n work26 python"
    assert call["login_shell"] is True


def test_factory_ssh_reports_startup_failure_with_remote_exit_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeStderr:
        def read(self):
            return b"Traceback: ModuleNotFoundError: No module named 'cbor2'"

    class _FakeProc:
        def poll(self):
            return 1

        stderr = _FakeStderr()

    class _FakeSession:
        def __init__(self) -> None:
            self.process = _FakeProc()

        def heartbeat(self):
            raise EOFError("unexpected end of stream while reading 4 bytes")

    def _fake_bootstrap_session(**kwargs):
        return _FakeSession()

    monkeypatch.setattr(remote_fs, "bootstrap_session", _fake_bootstrap_session)

    with pytest.raises(RuntimeError, match="remote server exited with status 1"):
        remote_fs.RemoteFileSystemFactory(
            url="ssh://alice@myhost:2222/home/alice/data.nc",
            credentials={"password": "pw"},
            cache_dir=None,
        )


def test_factory_ssh_forwards_paramiko_logs_to_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeSession:
        def heartbeat(self):
            return {"type": "HEARTBEAT"}

    def _fake_bootstrap_session(**kwargs):
        _ = kwargs
        logging.getLogger("paramiko.transport").info("KEXINIT received")
        logging.getLogger("p5rem.bootstrap").info("SSH connection established")
        return _FakeSession()

    monkeypatch.setattr(remote_fs, "bootstrap_session", _fake_bootstrap_session)

    streamed: list[str] = []
    remote_fs.RemoteFileSystemFactory(
        url="ssh://alice@myhost:2222/home/alice/data.nc",
        credentials={"password": "pw"},
        cache_dir=None,
        log_callback=streamed.append,
    )

    assert any("Starting SSH handshake" in line for line in streamed)
    assert any("[paramiko.transport]" in line and "KEXINIT received" in line for line in streamed)
    assert any("[p5rem.bootstrap]" in line and "SSH connection established" in line for line in streamed)


def test_p5rem_filesystem_ls_uses_structured_entries_without_stat() -> None:
    class _FakeSession:
        def list(self, path: str):
            assert path == "/remote"
            return [
                {
                    "name": "/remote/data.nc",
                    "type": "file",
                    "size": 1234,
                    "mtime": 1710000000.0,
                    "is_link": False,
                },
                {
                    "name": "/remote/subdir",
                    "type": "directory",
                    "size": 0,
                    "mtime": 1710000001.0,
                    "is_link": True,
                },
            ]

        def stat(self, path: str):
            raise AssertionError(f"stat() should not be called for structured entries: {path}")

    fs = remote_fs.P5RemFilesystem(_FakeSession())
    entries = fs.ls("/remote", detail=True)

    assert isinstance(entries, list)
    assert entries[0]["name"] == "/remote/data.nc"
    assert entries[0]["type"] == "file"
    assert entries[0]["size"] == 1234
    assert entries[0]["is_link"] is False
    assert entries[1]["name"] == "/remote/subdir"
    assert entries[1]["type"] == "directory"
    assert entries[1]["is_link"] is True


def test_p5rem_filesystem_ls_normalizes_string_entries_with_directory_types() -> None:
    class _FakeSession:
        def list(self, path: str):
            assert path == "/data"
            return ["folder", "file.nc"]

        def stat(self, path: str):
            if path == "/data/folder":
                return {"is_dir": True, "size": 0}
            if path == "/data/file.nc":
                return {"is_dir": False, "size": 123}
            raise FileNotFoundError(path)

    fs = remote_fs.P5RemFilesystem(_FakeSession())
    listing = fs.ls("/data", detail=True)

    assert listing == [
        {"name": "/data/folder", "size": 0, "type": "directory"},
        {"name": "/data/file.nc", "size": 123, "type": "file"},
    ]


def test_p5rem_filesystem_ls_detail_false_returns_full_paths_for_string_entries() -> None:
    class _FakeSession:
        def list(self, path: str):
            assert path == "/data"
            return ["folder", "file.nc"]

    fs = remote_fs.P5RemFilesystem(_FakeSession())
    names = fs.ls("/data", detail=False)

    assert names == ["/data/folder", "/data/file.nc"]


def test_factory_rejects_invalid_scheme() -> None:
    with pytest.raises(ValueError, match="Unsupported URL scheme"):
        remote_fs.RemoteFileSystemFactory(url="ftp://example.com/file.nc")


def test_factory_rejects_invalid_filesystem_mode() -> None:
    with pytest.raises(ValueError, match="Unsupported filesystem_mode"):
        remote_fs.RemoteFileSystemFactory(
            url="https://example.org/file.nc",
            filesystem_mode="filecache",
        )


def test_shimmy_ls_forwards_to_wrapped_filesystem() -> None:
    """Test that ShimmyFS.ls() properly forwards to the wrapped filesystem.
    
    This is critical for remote browsing to work with CachingFileSystem,
    as we need to bypass AbstractFileSystem.ls() and call directly to the
    wrapped filesystem's implementation.
    """
    class _LsTrackingFS(_DummyFS):
        def __init__(self) -> None:
            super().__init__()
            self.ls_calls: list[tuple[str, dict]] = []
        
        def ls(self, path: str, detail: bool = True, **kwargs):
            self.ls_calls.append((path, {"detail": detail, **kwargs}))
            return [
                {"name": f"{path}/file1.txt", "size": 100, "type": "file"},
                {"name": f"{path}/file2.txt", "size": 200, "type": "file"},
            ]
    
    base = _LsTrackingFS()
    fs = remote_fs.ShimmyFS(base)
    
    # Call ls() on ShimmyFS
    result = fs.ls("/test", detail=True)
    
    # Verify the wrapped filesystem's ls() was called directly
    assert len(base.ls_calls) == 1
    path, kwargs = base.ls_calls[0]
    assert path == "/test"
    assert kwargs["detail"] is True
    
    # Verify the result is correct
    assert len(result) == 2
    assert result[0]["name"] == "/test/file1.txt"
    assert result[1]["name"] == "/test/file2.txt"
