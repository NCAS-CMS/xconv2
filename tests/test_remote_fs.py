from __future__ import annotations

from pathlib import Path

import pytest

from xconv2 import remote_fs


class _DummyFS:
    def __init__(self) -> None:
        self.open_calls: list[tuple[str, str, dict]] = []
        self.glob_calls: list[str] = []

    def open(self, path: str, mode: str = "rb", **kwargs):
        self.open_calls.append((path, mode, dict(kwargs)))
        return {"path": path, "mode": mode, "kwargs": kwargs}

    def glob(self, pattern: str):
        self.glob_calls.append(pattern)
        return []


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
    assert cache_calls[0]["cache_storage"] == "/tmp/cache-dir"
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


def test_factory_ssh_without_proxy_uses_sftp(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict]] = []
    base = _DummyFS()

    def _fake_filesystem(protocol: str, **kwargs):
        calls.append((protocol, dict(kwargs)))
        return base

    monkeypatch.setattr(remote_fs.fsspec, "filesystem", _fake_filesystem)

    factory = remote_fs.RemoteFileSystemFactory(
        url="ssh://alice@myhost:2222/home/alice/data.nc",
        credentials={"password": "pw"},
        cache_dir=None,
    )

    assert factory.root_path == "/home/alice/data.nc"
    assert len(calls) == 1
    protocol, options = calls[0]
    assert protocol == "sftp"
    assert options["host"] == "myhost"
    assert options["username"] == "alice"
    assert options["password"] == "pw"
    assert options["port"] == 2222


def test_factory_ssh_proxyjump_routes_through_jump_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []
    base = _DummyFS()

    def _fake_create_sftp_via_jump(storage_options: dict):
        calls.append(dict(storage_options))
        return base

    monkeypatch.setattr(remote_fs, "_create_sftp_via_jump", _fake_create_sftp_via_jump)

    factory = remote_fs.RemoteFileSystemFactory(
        url="ssh://bob@target.example/data.nc",
        credentials={"proxyjump": "jumphost", "password": "pw"},
        cache_dir=None,
    )

    assert factory.root_path == "/data.nc"
    assert len(calls) == 1
    options = calls[0]
    assert options["proxy_jump"] == "jumphost"
    assert options["host"] == "target.example"
    assert options["username"] == "bob"


def test_factory_rejects_invalid_scheme() -> None:
    with pytest.raises(ValueError, match="Unsupported URL scheme"):
        remote_fs.RemoteFileSystemFactory(url="ftp://example.com/file.nc")


def test_factory_rejects_invalid_filesystem_mode() -> None:
    with pytest.raises(ValueError, match="Unsupported filesystem_mode"):
        remote_fs.RemoteFileSystemFactory(
            url="https://example.org/file.nc",
            filesystem_mode="filecache",
        )
