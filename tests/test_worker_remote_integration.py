from __future__ import annotations

import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlparse

import pytest

from xconv2.cf_templates import coordinate_list, plot_from_selection
from xconv2.remote_access import RemoteAccessSession
from xconv2.ui.settings_store import SettingsStore
import xconv2.worker as worker



@pytest.mark.integration
def test_worker_remote_open_from_minio_emits_metadata(minio_service, temp_bucket) -> None:
    sample_file = Path(__file__).resolve().parents[1] / "data" / "test1.nc"
    object_name = "nested/test1.nc"

    minio_service.fput_object(temp_bucket, object_name, str(sample_file))

    descriptor = {
        "protocol": "s3",
        "storage_options": {
            "key": "minioadmin",
            "secret": "minioadmin",
            "client_kwargs": {"endpoint_url": minio_service.endpoint_url},
        },
        "root_path": "",
        "display_name": "fake-alias",
        "uri_scheme": "s3",
        "uri_authority": "",
        "proxy_jump": None,
    }
    descriptor_hash = "integration-hash"
    session_id = "integration-session"
    uri = f"s3://{temp_bucket}/{object_name}"
    path = f"{temp_bucket}/{object_name}"

    messages: list[tuple[str, object]] = []
    original_send_to_gui = worker.send_to_gui
    worker.remote_session_pool.clear()

    try:
        worker.send_to_gui = lambda prefix, data=None: messages.append((prefix, data))

        worker._handle_control_task(
            "REMOTE_PREPARE",
            {
                "session_id": session_id,
                "descriptor_hash": descriptor_hash,
                "descriptor": descriptor,
            },
        )
        worker._handle_control_task(
            "REMOTE_OPEN",
            {
                "session_id": session_id,
                "descriptor_hash": descriptor_hash,
                "descriptor": descriptor,
                "uri": uri,
                "path": path,
            },
        )
    finally:
        worker.send_to_gui = original_send_to_gui
        worker.remote_session_pool.clear()

    prefixes = [prefix for prefix, _ in messages]
    assert "METADATA" in prefixes
    assert "REMOTE_OPEN_RESULT" in prefixes

    metadata_payload = next(data for prefix, data in messages if prefix == "METADATA")
    assert isinstance(metadata_payload, list)
    assert metadata_payload

    open_result = next(data for prefix, data in messages if prefix == "REMOTE_OPEN_RESULT")
    assert open_result == {
        "session_id": session_id,
        "uri": uri,
        "ok": True,
    }


@pytest.mark.skip(reason="S3/minio integration tests hanging temporarily")
@pytest.mark.integration
def test_worker_open_recent_s3_netcdf_from_minio(minio_service, temp_bucket) -> None:
    """Round-trip an S3 URI through recent storage and reopen successfully from MinIO."""
    sample_file = Path(__file__).resolve().parents[1] / "data" / "test1.nc"
    object_name = "nested/test1.nc"
    minio_service.fput_object(temp_bucket, object_name, str(sample_file))

    uri = f"s3://{temp_bucket}/{object_name}"
    parsed = urlparse(uri)
    path = f"{parsed.netloc}{parsed.path}".lstrip("/")

    with TemporaryDirectory(prefix="xconv2-recent-") as tmpdir:
        base = Path(tmpdir)
        store = SettingsStore(
            settings_path=base / "settings.json",
            recent_log_path=base / "recent.log",
            settings_version=1,
            default_max_recent_files=10,
        )
        store.data = store.default_settings()
        store.record_recent_file(uri)
        recent_uri = store.load_recent_files()[0]

    assert recent_uri == uri

    descriptor = {
        "protocol": "s3",
        "storage_options": {
            "key": "minioadmin",
            "secret": "minioadmin",
            "client_kwargs": {"endpoint_url": minio_service.endpoint_url},
        },
        "root_path": "",
        "display_name": "fake-alias",
        "uri_scheme": "s3",
        "uri_authority": "",
        "proxy_jump": None,
    }
    descriptor_hash = "integration-recent-hash"
    session_id = "integration-recent-session"

    messages: list[tuple[str, object]] = []
    original_send_to_gui = worker.send_to_gui
    worker.remote_session_pool.clear()

    try:
        worker.send_to_gui = lambda prefix, data=None: messages.append((prefix, data))

        worker._handle_control_task(
            "REMOTE_PREPARE",
            {
                "session_id": session_id,
                "descriptor_hash": descriptor_hash,
                "descriptor": descriptor,
            },
        )
        worker._handle_control_task(
            "REMOTE_OPEN",
            {
                "session_id": session_id,
                "descriptor_hash": descriptor_hash,
                "descriptor": descriptor,
                "uri": recent_uri,
                "path": path,
            },
        )
    finally:
        worker.send_to_gui = original_send_to_gui
        worker.remote_session_pool.clear()

    prefixes = [prefix for prefix, _ in messages]
    assert "METADATA" in prefixes
    assert "REMOTE_OPEN_RESULT" in prefixes

    open_result = next(data for prefix, data in messages if prefix == "REMOTE_OPEN_RESULT")
    assert open_result == {
        "session_id": session_id,
        "uri": recent_uri,
        "ok": True,
    }


@pytest.mark.integration
@pytest.mark.xfail(
    reason="Upstream cfdm/cf remote S3 PP read path currently fails; enable when upstream fix lands",
    strict=False,
)
def test_worker_remote_pp_from_minio_can_plot_default_contour_with_time_collapse(
    minio_service,
    temp_bucket,
) -> None:
    sample_file = Path(__file__).resolve().parents[1] / "data" / "test2.pp"
    object_name = "nested/test2.pp"

    minio_service.fput_object(temp_bucket, object_name, str(sample_file))

    descriptor = {
        "protocol": "s3",
        "storage_options": {
            "key": "minioadmin",
            "secret": "minioadmin",
            "client_kwargs": {"endpoint_url": minio_service.endpoint_url},
        },
        "root_path": "",
        "display_name": "fake-alias",
        "uri_scheme": "s3",
        "uri_authority": "",
        "proxy_jump": None,
    }
    descriptor_hash = "integration-pp-hash"
    session_id = "integration-pp-session"
    uri = f"s3://{temp_bucket}/{object_name}"
    path = f"{temp_bucket}/{object_name}"

    messages: list[tuple[str, object]] = []
    original_send_to_gui = worker.send_to_gui
    worker.remote_session_pool.clear()

    try:
        worker.send_to_gui = lambda prefix, data=None: messages.append((prefix, data))

        worker._handle_control_task(
            "REMOTE_PREPARE",
            {
                "session_id": session_id,
                "descriptor_hash": descriptor_hash,
                "descriptor": descriptor,
            },
        )
        worker._handle_control_task(
            "REMOTE_OPEN",
            {
                "session_id": session_id,
                "descriptor_hash": descriptor_hash,
                "descriptor": descriptor,
                "uri": uri,
                "path": path,
            },
        )

        # Match GUI flow: choose first field before plotting.
        exec(coordinate_list(0), worker.worker_globals)

        contour_code = plot_from_selection(
            selections={},
            collapse_by_coord={"time": "mean"},
            plot_kind="contour",
            plot_options=None,
        )
        exec(contour_code, worker.worker_globals)
        worker._emit_latest_plot_image()
    finally:
        worker.send_to_gui = original_send_to_gui
        worker.remote_session_pool.clear()

    prefixes = [prefix for prefix, _ in messages]
    assert "REMOTE_OPEN_RESULT" in prefixes
    assert "IMG_READY" in prefixes

    open_result = next(data for prefix, data in messages if prefix == "REMOTE_OPEN_RESULT")
    assert open_result == {
        "session_id": session_id,
        "uri": uri,
        "ok": True,
    }

    image_payload = next(data for prefix, data in messages if prefix == "IMG_READY")
    assert isinstance(image_payload, bytes)
    assert image_payload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _s3_descriptor(minio_service, *, cache: dict | None = None) -> dict:
    descriptor: dict = {
        "protocol": "s3",
        "storage_options": {
            "key": "minioadmin",
            "secret": "minioadmin",
            "client_kwargs": {"endpoint_url": minio_service.endpoint_url},
        },
        "root_path": "",
        "display_name": "minio-test",
        "uri_scheme": "s3",
        "uri_authority": "",
        "proxy_jump": None,
    }
    if cache is not None:
        descriptor["cache"] = cache
    return descriptor


def _run_remote_open(descriptor: dict, *, session_id: str, descriptor_hash: str, uri: str, path: str) -> list:
    """Run REMOTE_PREPARE + REMOTE_OPEN and return all captured (prefix, data) messages."""
    messages: list[tuple[str, object]] = []
    original_send = worker.send_to_gui
    worker.remote_session_pool.clear()
    try:
        worker.send_to_gui = lambda prefix, data=None: messages.append((prefix, data))
        worker._handle_control_task("REMOTE_PREPARE", {
            "session_id": session_id,
            "descriptor_hash": descriptor_hash,
            "descriptor": descriptor,
        })
        worker._handle_control_task("REMOTE_OPEN", {
            "session_id": session_id,
            "descriptor_hash": descriptor_hash,
            "descriptor": descriptor,
            "uri": uri,
            "path": path,
        })
    finally:
        worker.send_to_gui = original_send
        worker.remote_session_pool.clear()
    return messages


def _assert_successful_open(messages: list, *, session_id: str, uri: str) -> None:
    prefixes = [p for p, _ in messages]
    assert "METADATA" in prefixes
    assert "REMOTE_OPEN_RESULT" in prefixes
    metadata = next(d for p, d in messages if p == "METADATA")
    assert isinstance(metadata, list) and metadata
    result = next(d for p, d in messages if p == "REMOTE_OPEN_RESULT")
    assert result == {"session_id": session_id, "uri": uri, "ok": True}


def test_read_remote_fields_passes_prepared_filesystem_to_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prove worker._read_remote_fields opens prepared remote datasets before cf.read."""

    class _FakeFilesystem:
        def __init__(self) -> None:
            self.open_calls: list[tuple[str, str]] = []

        def open(self, path: str, mode: str):
            from io import BytesIO

            self.open_calls.append((path, mode))
            return BytesIO(b"remote-bytes")

    sentinel_fs = _FakeFilesystem()
    entry = worker.RemoteSessionEntry(
        session_id="sid",
        descriptor_hash="hash",
        descriptor={"protocol": "s3"},
        filesystem=sentinel_fs,
    )

    calls: dict[str, object] = {}

    def fake_reader(datasets, *, filesystem=None):
        calls["datasets"] = datasets
        calls["filesystem"] = filesystem
        return ["ok"]

    monkeypatch.setattr(worker.cf, "read", fake_reader)

    result = worker._read_remote_fields(
        entry=entry,
        descriptor={"protocol": "s3"},
        datasets="bucket/path/file.nc",
    )

    assert result == ["ok"]
    assert sentinel_fs.open_calls == [("bucket/path/file.nc", "rb")]
    assert getattr(calls["datasets"], "read", None) is not None
    assert calls["filesystem"] is None


# ---------------------------------------------------------------------------
# Open with cache
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="S3/minio integration tests hanging temporarily")
@pytest.mark.integration
def test_worker_remote_open_s3_with_memory_block_cache(minio_service, temp_bucket) -> None:
    """REMOTE_OPEN succeeds when an in-memory block cache is configured."""
    sample_file = Path(__file__).resolve().parents[1] / "data" / "test1.nc"
    object_name = "cached/test1.nc"
    minio_service.fput_object(temp_bucket, object_name, str(sample_file))

    uri = f"s3://{temp_bucket}/{object_name}"
    path = f"{temp_bucket}/{object_name}"
    descriptor = _s3_descriptor(
        minio_service,
        cache={"cache_strategy": "block", "blocksize_mb": 1, "max_blocks": 8},
    )

    messages = _run_remote_open(
        descriptor,
        session_id="open-mem-cache",
        descriptor_hash="open-mem-cache-hash",
        uri=uri,
        path=path,
    )
    _assert_successful_open(messages, session_id="open-mem-cache", uri=uri)


@pytest.mark.skip(reason="S3/minio integration tests hanging temporarily")
@pytest.mark.integration
def test_worker_remote_open_s3_with_disk_block_cache(minio_service, temp_bucket, tmp_path) -> None:
    """REMOTE_OPEN succeeds with a disk block cache and writes cache artefacts to disk."""
    sample_file = Path(__file__).resolve().parents[1] / "data" / "test1.nc"
    object_name = "cached/test1.nc"
    minio_service.fput_object(temp_bucket, object_name, str(sample_file))

    uri = f"s3://{temp_bucket}/{object_name}"
    path = f"{temp_bucket}/{object_name}"
    cache_dir = tmp_path / "blockcache"
    descriptor = _s3_descriptor(
        minio_service,
        cache={"disk_mode": "blocks", "disk_location": str(cache_dir), "blocksize_mb": 1, "max_blocks": 8},
    )

    messages = _run_remote_open(
        descriptor,
        session_id="open-disk-cache",
        descriptor_hash="open-disk-cache-hash",
        uri=uri,
        path=path,
    )
    _assert_successful_open(messages, session_id="open-disk-cache", uri=uri)
    # The blockcache index file must exist after reading real data.
    assert (cache_dir / "cache").is_file()


# ---------------------------------------------------------------------------
# Read with cache
# ---------------------------------------------------------------------------

# NOTE FOR FUTURE DEBUGGING:
# These two cf.read()-path tests are functional integration checks only:
# they prove remote reads work with the configured cache wrappers, but they
# do not measure wire traffic and therefore cannot alone prove cache hits.


@pytest.mark.skip(reason="S3/minio integration tests hanging temporarily")
@pytest.mark.integration
def test_read_remote_fields_s3_with_memory_cache(minio_service, temp_bucket) -> None:
    """_read_remote_fields returns real fields when a memory block cache wraps the S3 filesystem."""
    sample_file = Path(__file__).resolve().parents[1] / "data" / "test1.nc"
    object_name = "cached-read/test1.nc"
    minio_service.fput_object(temp_bucket, object_name, str(sample_file))

    descriptor = _s3_descriptor(
        minio_service,
        cache={"cache_strategy": "block", "blocksize_mb": 1, "max_blocks": 8},
    )

    original_send = worker.send_to_gui
    worker.remote_session_pool.clear()
    try:
        worker.send_to_gui = lambda prefix, data=None: None
        entry = worker._prepare_remote_session(
            session_id="read-mem-cache",
            descriptor_hash="read-mem-cache-hash",
            descriptor=descriptor,
        )
        fields = worker._read_remote_fields(
            entry=entry,
            descriptor=descriptor,
            datasets=f"{temp_bucket}/{object_name}",
        )
    finally:
        worker.send_to_gui = original_send
        worker.remote_session_pool.clear()

    assert fields


@pytest.mark.skip(reason="S3/minio integration tests hanging temporarily")
@pytest.mark.integration
def test_read_remote_fields_s3_with_disk_cache(minio_service, temp_bucket, tmp_path) -> None:
    """_read_remote_fields returns real fields and writes blockcache artefacts for a disk-cached session."""
    sample_file = Path(__file__).resolve().parents[1] / "data" / "test1.nc"
    object_name = "cached-read/test1.nc"
    minio_service.fput_object(temp_bucket, object_name, str(sample_file))

    cache_dir = tmp_path / "blockcache"
    descriptor = _s3_descriptor(
        minio_service,
        cache={"disk_mode": "blocks", "disk_location": str(cache_dir), "blocksize_mb": 1, "max_blocks": 8},
    )

    original_send = worker.send_to_gui
    worker.remote_session_pool.clear()
    try:
        worker.send_to_gui = lambda prefix, data=None: None
        entry = worker._prepare_remote_session(
            session_id="read-disk-cache",
            descriptor_hash="read-disk-cache-hash",
            descriptor=descriptor,
        )
        fields = worker._read_remote_fields(
            entry=entry,
            descriptor=descriptor,
            datasets=f"{temp_bucket}/{object_name}",
        )
    finally:
        worker.send_to_gui = original_send
        worker.remote_session_pool.clear()

    assert fields
    assert (cache_dir / "cache").is_file()


# ---------------------------------------------------------------------------
# Cache effectiveness verification
# ---------------------------------------------------------------------------

# NOTE FOR FUTURE DEBUGGING:
# test_disk_cache_works_without_cf_read is the authoritative cache-effectiveness
# oracle. It checks MinIO's minio_s3_traffic_sent_bytes counter, which directly
# measures server->client bytes on the wire and discriminates cache-hit vs miss.


def _minio_bytes_sent(metrics_url: str) -> float:
    """Read minio_s3_traffic_sent_bytes from the MinIO Prometheus endpoint.

    MinIO updates this counter on a roughly 10-second cadence, so callers
    must allow adequate settling time between the operation under test and
    sampling this value.
    """
    import urllib.request

    with urllib.request.urlopen(metrics_url, timeout=10) as resp:
        for line in resp.read().decode().splitlines():
            if line.startswith("minio_s3_traffic_sent_bytes{"):
                return float(line.split()[-1])
    raise RuntimeError(f"minio_s3_traffic_sent_bytes not found in {metrics_url}")


def _count_file_io_logs(caplog, *, log_substring: str = "REMOTE_FS file_read") -> int:
    """Count log lines containing a specific substring.

    Used only by tests that compare two reads of the same file against each other
    (i.e. relative comparisons), where counting user-level read() calls is valid.
    Do NOT use this to prove cache effectiveness — use _minio_bytes_sent() for that.
    """
    return sum(1 for record in caplog.records if log_substring in record.getMessage())


@pytest.mark.skip(reason="S3/minio integration tests hanging temporarily")
@pytest.mark.integration
def test_disk_cache_works_without_cf_read(minio_service, temp_bucket, tmp_path) -> None:
    """Verify fsspec blockcache actually reduces wire traffic to MinIO on the second read.

    Uses MinIO's Prometheus endpoint (minio_s3_traffic_sent_bytes) as the oracle —
    this measures real bytes sent by the server, so it is unambiguous regardless of
    how many Python-level read() calls are made.

    MinIO updates the Prometheus counters roughly every 10 seconds, so the test
    sleeps after each read to let the scrape interval tick.  The test is slow by
    design (~30 s) but definitive.
    """
    import time
    from xconv2.remote_access import RemoteFilesystemSpec, create_filesystem

    sample_file = Path(__file__).resolve().parents[1] / "data" / "test1.nc"
    object_name = "cache-direct-test/test1.nc"
    minio_service.fput_object(temp_bucket, object_name, str(sample_file))

    cache_dir = tmp_path / "blockcache-direct"
    cache_dir.mkdir()

    spec = RemoteFilesystemSpec(
        protocol="s3",
        storage_options={
            "key": "minioadmin",
            "secret": "minioadmin",
            "client_kwargs": {"endpoint_url": minio_service.endpoint_url},
        },
        root_path="",
        display_name="minio-direct",
        uri_scheme="s3",
        uri_authority="",
        proxy_jump=None,
    )
    cache_config = {"disk_mode": "blocks", "disk_location": str(cache_dir), "blocksize_mb": 1}
    fs_cached = create_filesystem(spec, cache=cache_config)
    remote_path = f"{temp_bucket}/{object_name}"

    # ------------------------------------------------------------------ #
    # Baseline: record bytes-sent before we touch anything.               #
    # ------------------------------------------------------------------ #
    before_first = _minio_bytes_sent(minio_service.metrics_url)

    # First read — cache is empty, all data must travel over the wire.
    handle1 = fs_cached.open(remote_path, "rb")
    try:
        data1 = handle1.read()
    finally:
        handle1.close()

    assert data1, "First read returned no data"
    assert (cache_dir / "cache").is_file(), "Blockcache index file not created after first read"

    # Wait for MinIO's Prometheus scrape interval to tick (~10 s).
    time.sleep(12)
    after_first = _minio_bytes_sent(minio_service.metrics_url)
    first_read_bytes = after_first - before_first

    assert first_read_bytes > 0, (
        f"MinIO reported 0 bytes sent after first read — "
        f"metrics endpoint may not be working (metrics_url={minio_service.metrics_url})"
    )

    # ------------------------------------------------------------------ #
    # Second read — blockcache should serve entirely from disk.           #
    # ------------------------------------------------------------------ #
    before_second = _minio_bytes_sent(minio_service.metrics_url)

    handle2 = fs_cached.open(remote_path, "rb")
    try:
        data2 = handle2.read()
    finally:
        handle2.close()

    assert data2 == data1, "Second read returned different data from first"

    time.sleep(12)
    after_second = _minio_bytes_sent(minio_service.metrics_url)
    second_read_bytes = after_second - before_second

    # A cached read may still cause a small HEAD/metadata request, so we allow
    # a generous tolerance of 4 KB for HTTP overhead — but NOT re-fetching the
    # full file body (~311 KB).
    assert second_read_bytes < 4096, (
        f"Disk blockcache is NOT serving from cache: "
        f"first read sent {first_read_bytes:,.0f} bytes, "
        f"second read sent {second_read_bytes:,.0f} bytes from MinIO. "
        f"Expected <4 KB on the second read (overhead only)."
    )


@pytest.mark.skip(reason="S3/minio integration tests hanging temporarily")
@pytest.mark.integration
def test_disk_cache_persists_across_filesystem_recreation(minio_service, temp_bucket, tmp_path) -> None:
    """Verify disk blockcache remains effective after creating a NEW filesystem instance.

    This mirrors app behavior when a worker remote session is evicted/recreated:
    create filesystem A, read once, then create filesystem B with the same disk
    cache location and read again. The second read should be served from disk.
    """
    import time
    from xconv2.remote_access import RemoteFilesystemSpec, create_filesystem

    sample_file = Path(__file__).resolve().parents[1] / "data" / "test1.nc"
    object_name = "cache-recreate-test/test1.nc"
    minio_service.fput_object(temp_bucket, object_name, str(sample_file))

    cache_dir = tmp_path / "blockcache-recreate"
    cache_dir.mkdir()
    remote_path = f"{temp_bucket}/{object_name}"

    spec = RemoteFilesystemSpec(
        protocol="s3",
        storage_options={
            "key": "minioadmin",
            "secret": "minioadmin",
            "client_kwargs": {"endpoint_url": minio_service.endpoint_url},
        },
        root_path="",
        display_name="minio-recreate",
        uri_scheme="s3",
        uri_authority="",
        proxy_jump=None,
    )
    cache_config = {"disk_mode": "blocks", "disk_location": str(cache_dir), "blocksize_mb": 1}

    fs1 = create_filesystem(spec, cache=cache_config)
    before_first = _minio_bytes_sent(minio_service.metrics_url)
    h1 = fs1.open(remote_path, "rb")
    try:
        data1 = h1.read()
    finally:
        h1.close()

    assert data1
    assert (cache_dir / "cache").is_file(), "Blockcache index missing after first read"

    time.sleep(12)
    after_first = _minio_bytes_sent(minio_service.metrics_url)
    first_read_bytes = after_first - before_first
    assert first_read_bytes > 0, "First read should transfer bytes from MinIO"

    # Recreate filesystem/session as the app does when a worker session is rebuilt.
    fs2 = create_filesystem(spec, cache=cache_config)
    before_second = _minio_bytes_sent(minio_service.metrics_url)
    h2 = fs2.open(remote_path, "rb")
    try:
        data2 = h2.read()
    finally:
        h2.close()

    assert data2 == data1

    time.sleep(12)
    after_second = _minio_bytes_sent(minio_service.metrics_url)
    second_read_bytes = after_second - before_second

    assert second_read_bytes < 4096, (
        f"Disk cache did not survive filesystem recreation: "
        f"first read sent {first_read_bytes:,.0f} bytes, "
        f"second read sent {second_read_bytes:,.0f} bytes after new filesystem instance."
    )


@pytest.mark.skip(reason="S3/minio integration tests hanging temporarily")
@pytest.mark.integration
def test_disk_cache_still_hits_with_file_io_tracing(minio_service, temp_bucket, tmp_path) -> None:
    """Verify trace_file_io logging does not break disk blockcache semantics."""
    import time
    from xconv2.remote_access import RemoteFilesystemSpec, create_filesystem

    sample_file = Path(__file__).resolve().parents[1] / "data" / "test1.nc"
    object_name = "cache-trace-file-io/test1.nc"
    minio_service.fput_object(temp_bucket, object_name, str(sample_file))

    cache_dir = tmp_path / "blockcache-trace-file-io"
    cache_dir.mkdir()
    remote_path = f"{temp_bucket}/{object_name}"

    spec = RemoteFilesystemSpec(
        protocol="s3",
        storage_options={
            "key": "minioadmin",
            "secret": "minioadmin",
            "client_kwargs": {"endpoint_url": minio_service.endpoint_url},
        },
        root_path="",
        display_name="minio-trace-file-io",
        uri_scheme="s3",
        uri_authority="",
        proxy_jump=None,
    )
    cache_config = {"disk_mode": "blocks", "disk_location": str(cache_dir), "blocksize_mb": 1}
    original_logging = RemoteAccessSession.logging_configuration()
    RemoteAccessSession.configure_logging(
        scope_levels={
            "all": "WARNING",
            "xconv2": "DEBUG",
        }
    )

    try:
        fs_cached = create_filesystem(spec, cache=cache_config)

        before_first = _minio_bytes_sent(minio_service.metrics_url)
        h1 = fs_cached.open(remote_path, "rb")
        try:
            data1 = h1.read()
        finally:
            h1.close()

        assert data1
        time.sleep(12)
        after_first = _minio_bytes_sent(minio_service.metrics_url)
        first_read_bytes = after_first - before_first
        assert first_read_bytes > 0

        before_second = _minio_bytes_sent(minio_service.metrics_url)
        h2 = fs_cached.open(remote_path, "rb")
        try:
            data2 = h2.read()
        finally:
            h2.close()

        assert data2 == data1
        time.sleep(12)
        after_second = _minio_bytes_sent(minio_service.metrics_url)
        second_read_bytes = after_second - before_second

        assert second_read_bytes < 4096, (
            f"Disk cache was broken by trace_file_io logging: "
            f"first read sent {first_read_bytes:,.0f} bytes, "
            f"second read sent {second_read_bytes:,.0f} bytes."
        )
    finally:
        RemoteAccessSession.configure_logging(
            scope_levels=original_logging.scope_levels,
        )


@pytest.mark.skip(reason="S3/minio integration tests hanging temporarily")
@pytest.mark.integration
def test_worker_remote_open_disk_cache_survives_release_recreate(minio_service, temp_bucket, tmp_path) -> None:
    """Verify disk cache effectiveness survives worker session release + recreation.

    This is the closest test to real app flow: REMOTE_PREPARE -> REMOTE_OPEN,
    then REMOTE_RELEASE, then REMOTE_PREPARE -> REMOTE_OPEN again with the same
    descriptor and disk cache directory.
    """
    import time

    sample_file = Path(__file__).resolve().parents[1] / "data" / "test1.nc"
    object_name = "worker-recreate-cache/test1.nc"
    minio_service.fput_object(temp_bucket, object_name, str(sample_file))

    cache_dir = tmp_path / "worker-lifecycle-blockcache"
    cache_dir.mkdir()

    descriptor = _s3_descriptor(
        minio_service,
        cache={
            "disk_mode": "blocks",
            "disk_location": str(cache_dir),
            "blocksize_mb": 1,
            "max_blocks": 8,
        },
    )
    descriptor_hash = "worker-recreate-cache-hash"
    session_id = "worker-recreate-cache-session"
    uri = f"s3://{temp_bucket}/{object_name}"
    path = f"{temp_bucket}/{object_name}"

    messages: list[tuple[str, object]] = []
    original_send_to_gui = worker.send_to_gui
    worker.remote_session_pool.clear()

    try:
        worker.send_to_gui = lambda prefix, data=None: messages.append((prefix, data))

        # First prepare/open: should fetch over wire and populate disk cache.
        before_first = _minio_bytes_sent(minio_service.metrics_url)
        worker._handle_control_task(
            "REMOTE_PREPARE",
            {
                "session_id": session_id,
                "descriptor_hash": descriptor_hash,
                "descriptor": descriptor,
            },
        )
        worker._handle_control_task(
            "REMOTE_OPEN",
            {
                "session_id": session_id,
                "descriptor_hash": descriptor_hash,
                "descriptor": descriptor,
                "uri": uri,
                "path": path,
            },
        )
        time.sleep(12)
        after_first = _minio_bytes_sent(minio_service.metrics_url)
        first_open_bytes = after_first - before_first

        assert first_open_bytes > 0, "First REMOTE_OPEN should transfer bytes from MinIO"
        assert (cache_dir / "cache").is_file(), "Blockcache index missing after first REMOTE_OPEN"

        # Release current worker session to force true recreation.
        worker._handle_control_task(
            "REMOTE_RELEASE",
            {
                "session_id": session_id,
                "descriptor_hash": descriptor_hash,
            },
        )

        # Re-prepare and re-open with same descriptor/cache dir.
        before_second = _minio_bytes_sent(minio_service.metrics_url)
        worker._handle_control_task(
            "REMOTE_PREPARE",
            {
                "session_id": session_id,
                "descriptor_hash": descriptor_hash,
                "descriptor": descriptor,
            },
        )
        worker._handle_control_task(
            "REMOTE_OPEN",
            {
                "session_id": session_id,
                "descriptor_hash": descriptor_hash,
                "descriptor": descriptor,
                "uri": uri,
                "path": path,
            },
        )
        time.sleep(12)
        after_second = _minio_bytes_sent(minio_service.metrics_url)
        second_open_bytes = after_second - before_second

        assert second_open_bytes < 4096, (
            f"Disk cache ineffective after worker release/recreate: "
            f"first open sent {first_open_bytes:,.0f} bytes, "
            f"second open sent {second_open_bytes:,.0f} bytes."
        )

        # Sanity: we should still emit an open result in the recreated session.
        open_results = [
            data for prefix, data in messages
            if prefix == "REMOTE_OPEN_RESULT"
        ]
        assert open_results
        assert open_results[-1] == {
            "session_id": session_id,
            "uri": uri,
            "ok": True,
        }
    finally:
        worker.send_to_gui = original_send_to_gui
        worker.remote_session_pool.clear()


@pytest.mark.integration
def test_worker_remote_open_large_logged_s3_key_cache_hits_on_second_open(minio_service, temp_bucket, tmp_path) -> None:
    """Mirror the app's logged S3 path and verify wire bytes collapse on second open.

    Uses the same URI/key shape seen in app logs (s3://bnl/da193a_25_3hr__198808-198808.nc),
    but against the test MinIO endpoint and a local representative file payload.
    """
    import time

    data_dir = Path(__file__).resolve().parents[1] / "data"
    # Prefer exact filename when present; fall back to the similar local sample.
    source_file = data_dir / "da193a_25_3hr__198808-198808.nc"
    if not source_file.is_file():
        source_file = data_dir / "da193a_25_3hr__198807-198807.nc"

    assert source_file.is_file(), "Expected a local da193a sample in data/ for this integration test"

    object_name = "da193a_25_3hr__198808-198808.nc"
    minio_service.fput_object(temp_bucket, object_name, str(source_file))

    cache_dir = tmp_path / "worker-large-da193a-blockcache"
    cache_dir.mkdir()

    descriptor = _s3_descriptor(
        minio_service,
        cache={
            "disk_mode": "blocks",
            "disk_location": str(cache_dir),
            "blocksize_mb": 2,
            "max_blocks": 512,
        },
    )
    descriptor_hash = "worker-large-da193a-cache-hash"
    session_id = "worker-large-da193a-cache-session"
    uri = f"s3://{temp_bucket}/{object_name}"
    path = f"{temp_bucket}/{object_name}"

    messages: list[tuple[str, object]] = []
    original_send_to_gui = worker.send_to_gui
    worker.remote_session_pool.clear()

    try:
        worker.send_to_gui = lambda prefix, data=None: messages.append((prefix, data))

        # First open: expected to transfer payload over the wire and populate blockcache.
        before_first = _minio_bytes_sent(minio_service.metrics_url)
        worker._handle_control_task(
            "REMOTE_PREPARE",
            {
                "session_id": session_id,
                "descriptor_hash": descriptor_hash,
                "descriptor": descriptor,
            },
        )
        worker._handle_control_task(
            "REMOTE_OPEN",
            {
                "session_id": session_id,
                "descriptor_hash": descriptor_hash,
                "descriptor": descriptor,
                "uri": uri,
                "path": path,
            },
        )
        time.sleep(12)
        after_first = _minio_bytes_sent(minio_service.metrics_url)
        first_open_bytes = after_first - before_first

        assert first_open_bytes > 0, "First large-file REMOTE_OPEN should transfer bytes from MinIO"
        assert (cache_dir / "cache").is_file(), "Blockcache index missing after first large-file REMOTE_OPEN"

        # Release/recreate to mimic app lifecycle exactly before second open.
        worker._handle_control_task(
            "REMOTE_RELEASE",
            {
                "session_id": session_id,
                "descriptor_hash": descriptor_hash,
            },
        )

        before_second = _minio_bytes_sent(minio_service.metrics_url)
        worker._handle_control_task(
            "REMOTE_PREPARE",
            {
                "session_id": session_id,
                "descriptor_hash": descriptor_hash,
                "descriptor": descriptor,
            },
        )
        worker._handle_control_task(
            "REMOTE_OPEN",
            {
                "session_id": session_id,
                "descriptor_hash": descriptor_hash,
                "descriptor": descriptor,
                "uri": uri,
                "path": path,
            },
        )
        time.sleep(12)
        after_second = _minio_bytes_sent(minio_service.metrics_url)
        second_open_bytes = after_second - before_second

        # Allow small request overhead but not object-body re-download.
        assert second_open_bytes < 4096, (
            f"Large-file disk cache ineffective on second worker open: "
            f"first open sent {first_open_bytes:,.0f} bytes, "
            f"second open sent {second_open_bytes:,.0f} bytes."
        )

        open_results = [data for prefix, data in messages if prefix == "REMOTE_OPEN_RESULT"]
        assert open_results
        assert open_results[-1] == {
            "session_id": session_id,
            "uri": uri,
            "ok": True,
        }
    finally:
        worker.send_to_gui = original_send_to_gui
        worker.remote_session_pool.clear()


@pytest.mark.skip(reason="S3/minio integration tests hanging temporarily")
@pytest.mark.integration
def test_large_logged_s3_key_direct_read_cache_hits_on_second_open(minio_service, temp_bucket, tmp_path) -> None:
    """Isolate fsspec blockcache behavior for the large logged key without worker/cf.read."""
    import time
    from xconv2.remote_access import RemoteFilesystemSpec, create_filesystem

    data_dir = Path(__file__).resolve().parents[1] / "data"
    source_file = data_dir / "da193a_25_3hr__198808-198808.nc"
    if not source_file.is_file():
        source_file = data_dir / "da193a_25_3hr__198807-198807.nc"

    assert source_file.is_file(), "Expected a local da193a sample in data/ for this integration test"

    object_name = "da193a_25_3hr__198808-198808.nc"
    minio_service.fput_object(temp_bucket, object_name, str(source_file))
    remote_path = f"{temp_bucket}/{object_name}"

    cache_dir = tmp_path / "direct-large-da193a-blockcache"
    cache_dir.mkdir()

    spec = RemoteFilesystemSpec(
        protocol="s3",
        storage_options={
            "key": "minioadmin",
            "secret": "minioadmin",
            "client_kwargs": {"endpoint_url": minio_service.endpoint_url},
        },
        root_path="",
        display_name="minio-direct-large",
        uri_scheme="s3",
        uri_authority="",
        proxy_jump=None,
    )
    cache_config = {
        "disk_mode": "blocks",
        "disk_location": str(cache_dir),
        "blocksize_mb": 2,
        "max_blocks": 512,
    }

    fs1 = create_filesystem(spec, cache=cache_config)
    before_first = _minio_bytes_sent(minio_service.metrics_url)
    h1 = fs1.open(remote_path, "rb")
    try:
        data1 = h1.read()
    finally:
        h1.close()

    assert data1
    assert (cache_dir / "cache").is_file(), "Blockcache index missing after first direct open"

    time.sleep(12)
    after_first = _minio_bytes_sent(minio_service.metrics_url)
    first_open_bytes = after_first - before_first
    assert first_open_bytes > 0

    fs2 = create_filesystem(spec, cache=cache_config)
    before_second = _minio_bytes_sent(minio_service.metrics_url)
    h2 = fs2.open(remote_path, "rb")
    try:
        data2 = h2.read()
    finally:
        h2.close()

    assert data2 == data1

    time.sleep(12)
    after_second = _minio_bytes_sent(minio_service.metrics_url)
    second_open_bytes = after_second - before_second

    assert second_open_bytes < 4096, (
        f"Direct fsspec cache ineffective for large logged key: "
        f"first open sent {first_open_bytes:,.0f} bytes, "
        f"second open sent {second_open_bytes:,.0f} bytes."
    )


@pytest.mark.integration
def test_worker_prepared_filesystem_large_key_direct_read_cache_hits_on_second_open(
    minio_service,
    temp_bucket,
    tmp_path,
) -> None:
    """Use worker session lifecycle but read directly from entry.filesystem (no cf.read)."""
    import time

    data_dir = Path(__file__).resolve().parents[1] / "data"
    source_file = data_dir / "da193a_25_3hr__198808-198808.nc"
    if not source_file.is_file():
        source_file = data_dir / "da193a_25_3hr__198807-198807.nc"

    assert source_file.is_file(), "Expected a local da193a sample in data/ for this integration test"

    object_name = "da193a_25_3hr__198808-198808.nc"
    minio_service.fput_object(temp_bucket, object_name, str(source_file))
    remote_path = f"{temp_bucket}/{object_name}"

    cache_dir = tmp_path / "worker-prepared-direct-large-da193a-blockcache"
    cache_dir.mkdir()

    descriptor = _s3_descriptor(
        minio_service,
        cache={
            "disk_mode": "blocks",
            "disk_location": str(cache_dir),
            "blocksize_mb": 2,
            "max_blocks": 512,
        },
    )
    session_id = "worker-prepared-direct-large-session"
    descriptor_hash = "worker-prepared-direct-large-hash"

    original_send = worker.send_to_gui
    worker.remote_session_pool.clear()
    try:
        worker.send_to_gui = lambda prefix, data=None: None

        before_first = _minio_bytes_sent(minio_service.metrics_url)
        entry1 = worker._prepare_remote_session(
            session_id=session_id,
            descriptor_hash=descriptor_hash,
            descriptor=descriptor,
        )
        h1 = entry1.filesystem.open(remote_path, "rb")
        try:
            data1 = h1.read()
        finally:
            h1.close()

        assert data1
        assert (cache_dir / "cache").is_file(), "Blockcache index missing after worker-prepared direct read"

        time.sleep(12)
        after_first = _minio_bytes_sent(minio_service.metrics_url)
        first_open_bytes = after_first - before_first
        assert first_open_bytes > 0

        worker._release_remote_session(session_id=session_id, descriptor_hash=descriptor_hash)

        before_second = _minio_bytes_sent(minio_service.metrics_url)
        entry2 = worker._prepare_remote_session(
            session_id=session_id,
            descriptor_hash=descriptor_hash,
            descriptor=descriptor,
        )
        h2 = entry2.filesystem.open(remote_path, "rb")
        try:
            data2 = h2.read()
        finally:
            h2.close()

        assert data2 == data1

        time.sleep(12)
        after_second = _minio_bytes_sent(minio_service.metrics_url)
        second_open_bytes = after_second - before_second

        assert second_open_bytes < 4096, (
            f"Worker-prepared direct read unexpectedly re-downloaded large key: "
            f"first open sent {first_open_bytes:,.0f} bytes, "
            f"second open sent {second_open_bytes:,.0f} bytes."
        )
    finally:
        worker.send_to_gui = original_send
        worker.remote_session_pool.clear()


@pytest.mark.integration
def test_cf_read_large_logged_key_dataset_form_matrix_uses_disk_cache_on_second_open(
    minio_service,
    temp_bucket,
    tmp_path,
) -> None:
    """Demonstrate handle-based cf.read forms hit disk cache on second open.

    This is intended as a compact upstream-ready reproducer: same file, same
    cache config, same filesystem construction, differing only in the handle
    argument shape supplied to cf.read(...).
    """
    import time
    import cf
    from xconv2.remote_access import RemoteFilesystemSpec, create_filesystem

    data_dir = Path(__file__).resolve().parents[1] / "data"
    source_file = data_dir / "da193a_25_3hr__198808-198808.nc"
    if not source_file.is_file():
        source_file = data_dir / "da193a_25_3hr__198807-198807.nc"

    assert source_file.is_file(), "Expected a local da193a sample in data/ for this integration test"

    object_name = "da193a_25_3hr__198808-198808.nc"
    minio_service.fput_object(temp_bucket, object_name, str(source_file))
    remote_path = f"{temp_bucket}/{object_name}"
    remote_uri = f"s3://{remote_path}"

    spec = RemoteFilesystemSpec(
        protocol="s3",
        storage_options={
            "key": "minioadmin",
            "secret": "minioadmin",
            "client_kwargs": {"endpoint_url": minio_service.endpoint_url},
        },
        root_path="",
        display_name="minio-cf-matrix",
        uri_scheme="s3",
        uri_authority="",
        proxy_jump=None,
    )

    # Keep cache location separate per case to avoid cross-case contamination.
    cases = [
        ("handle-single", remote_path),
        ("handle-list", [remote_path]),
    ]
    case_results: dict[str, dict[str, float]] = {}

    for case_name, datasets in cases:
        cache_dir = tmp_path / f"cf-read-matrix-{case_name}"
        cache_dir.mkdir()
        cache_config = {
            "disk_mode": "blocks",
            "disk_location": str(cache_dir),
            "blocksize_mb": 2,
            "max_blocks": 512,
        }

        fs1 = create_filesystem(spec, cache=cache_config)
        before_first = _minio_bytes_sent(minio_service.metrics_url)
        if isinstance(datasets, list):
            handles1 = [fs1.open(path, "rb") for path in datasets]
            try:
                fields1 = cf.read(handles1)
            finally:
                for handle in handles1:
                    handle.close()
        else:
            with fs1.open(datasets, "rb") as handle1:
                fields1 = cf.read(handle1)
        assert fields1
        time.sleep(12)
        after_first = _minio_bytes_sent(minio_service.metrics_url)
        first_open_bytes = after_first - before_first

        fs2 = create_filesystem(spec, cache=cache_config)
        before_second = _minio_bytes_sent(minio_service.metrics_url)
        if isinstance(datasets, list):
            handles2 = [fs2.open(path, "rb") for path in datasets]
            try:
                fields2 = cf.read(handles2)
            finally:
                for handle in handles2:
                    handle.close()
        else:
            with fs2.open(datasets, "rb") as handle2:
                fields2 = cf.read(handle2)
        assert fields2
        time.sleep(12)
        after_second = _minio_bytes_sent(minio_service.metrics_url)
        second_open_bytes = after_second - before_second

        case_results[case_name] = {
            "first_open_bytes": first_open_bytes,
            "second_open_bytes": second_open_bytes,
        }

    failing_cases = {
        name: metrics
        for name, metrics in case_results.items()
        if metrics["second_open_bytes"] >= 4096
    }

    assert not failing_cases, (
        "cf.read did not consistently use disk cache on second open for the "
        f"large logged key; failing cases: {failing_cases}; all results: {case_results}"
    )


@pytest.mark.skip(reason="S3/minio integration tests hanging temporarily")
@pytest.mark.integration
def test_memory_cache_per_handle_no_reuse_across_opens(minio_service, temp_bucket, caplog) -> None:
    """Verify memory block cache does NOT persist across separate file opens (per-handle cache)."""
    sample_file = Path(__file__).resolve().parents[1] / "data" / "test1.nc"
    object_name = "mem-cache-test-per-handle/test1.nc"
    minio_service.fput_object(temp_bucket, object_name, str(sample_file))

    descriptor = _s3_descriptor(
        minio_service,
        cache={"cache_strategy": "block", "blocksize_mb": 1, "max_blocks": 8},
    )

    # Reset logging state first, in case previous tests left it dirty.
    RemoteAccessSession.configure_logging(
        scope_levels={
            "all": "WARNING",
            "xconv2": "WARNING",
        }
    )
    RemoteAccessSession.configure_logging(
        scope_levels={
            "all": "WARNING",
            "xconv2": "DEBUG",
        }
    )
    original_send = worker.send_to_gui
    worker.remote_session_pool.clear()

    try:
        worker.send_to_gui = lambda prefix, data=None: None
        entry = worker._prepare_remote_session(
            session_id="mem-cache-per-handle",
            descriptor_hash="mem-cache-per-handle-hash",
            descriptor=descriptor,
        )

        # First read with memory cache.
        with caplog.at_level(logging.INFO):
            caplog.clear()
            fields1 = worker._read_remote_fields(
                entry=entry,
                descriptor=descriptor,
                datasets=f"{temp_bucket}/{object_name}",
            )
            first_read_io_calls = _count_file_io_logs(caplog)

        # Second read: memory cache is per-handle, so each open() gets a fresh cache.
        # We expect roughly the same amount of remote I/O (perhaps slightly less due to
        # OS-level page cache, but not significantly).
        with caplog.at_level(logging.INFO):
            caplog.clear()
            fields2 = worker._read_remote_fields(
                entry=entry,
                descriptor=descriptor,
                datasets=f"{temp_bucket}/{object_name}",
            )
            second_read_io_calls = _count_file_io_logs(caplog)

        assert fields1 and fields2
        assert first_read_io_calls > 0, "First read should log file I/O"
        # Memory cache doesn't help across opens; we expect similar I/O.
        # Allow some variance (20%) due to OS caching, but should be roughly equal.
        assert (
            abs(second_read_io_calls - first_read_io_calls) / max(first_read_io_calls, 1) < 0.3
        ), (
            f"Memory cache is per-handle; should see similar I/O across opens. "
            f"First: {first_read_io_calls} calls, Second: {second_read_io_calls} calls"
        )
    finally:
        RemoteAccessSession.configure_logging(
            scope_levels={
                "all": "WARNING",
                "xconv2": "WARNING",
            }
        )
        worker.send_to_gui = original_send
        worker.remote_session_pool.clear()