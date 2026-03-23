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


# ---------------------------------------------------------------------------
# Open with cache
# ---------------------------------------------------------------------------


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


def _count_file_io_logs(caplog, *, log_substring: str = "REMOTE_FS file_read") -> int:
    """Count log lines containing a specific substring."""
    return sum(1 for record in caplog.records if log_substring in record.getMessage())


def _sum_file_io_elapsed_ms(caplog) -> int:
    """Sum elapsed_ms values from all REMOTE_FS file_read logs."""
    import re
    total_ms = 0
    for record in caplog.records:
        msg = record.getMessage()
        if "REMOTE_FS file_read" in msg:
            match = re.search(r"elapsed_ms=(\d+)", msg)
            if match:
                total_ms += int(match.group(1))
    return total_ms


@pytest.mark.integration
def test_disk_cache_works_without_cf_read(minio_service, temp_bucket, tmp_path, caplog) -> None:
    """Verify disk blockcache DOES work when used directly (without cf.read).
    
    This test isolates whether the caching mechanism itself is functional by directly
    using the cached filesystem to read bytes, rather than going through cf.read().
    If this passes but test_disk_cache_reduces_remote_io is skipped, it proves
    cf.read() is not respecting the filesystem= parameter or the blockcache wrapper.
    """
    sample_file = Path(__file__).resolve().parents[1] / "data" / "test1.nc"
    object_name = "cache-direct-test/test1.nc"
    minio_service.fput_object(temp_bucket, object_name, str(sample_file))

    cache_dir = tmp_path / "blockcache-direct"
    cache_dir.mkdir(exist_ok=True)
    
    # Import here to avoid unnecessary dependency
    from xconv2.remote_access import RemoteFilesystemSpec, create_filesystem
    
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
    
    RemoteAccessSession.configure_logging(trace_file_io=True)
    
    try:
        remote_path = f"{temp_bucket}/{object_name}"
        
        # First read: cache is empty, all data from MinIO
        with caplog.at_level(logging.INFO):
            caplog.clear()
            handle1 = fs_cached.open(remote_path, "rb")
            try:
                data1 = handle1.read(4096)
            finally:
                if hasattr(handle1, "close"):
                    handle1.close()
            first_read_calls = _count_file_io_logs(caplog)
        
        assert data1, "First read should return data"
        assert first_read_calls > 0, "First read should make remote calls"
        assert (cache_dir / "cache").is_file(), "Cache index should be created"
        
        # Second read: should be served from disk cache
        with caplog.at_level(logging.INFO):
            caplog.clear()
            handle2 = fs_cached.open(remote_path, "rb")
            try:
                data2 = handle2.read(4096)
            finally:
                if hasattr(handle2, "close"):
                    handle2.close()
            second_read_calls = _count_file_io_logs(caplog)
        
        assert data2 == data1, "Both reads should return identical data"
        
        # Cache MUST reduce remote I/O on the second read
        assert second_read_calls < first_read_calls, (
            f"Direct filesystem disk cache is BROKEN: "
            f"first read {first_read_calls} remote ops, second read {second_read_calls} remote ops. "
            f"The blockcache wrapper is not being used correctly by fsspec. "
            f"This is a blocking issue — cached reads should have fewer remote calls."
        )
    finally:
        RemoteAccessSession.configure_logging(trace_file_io=False)


@pytest.mark.integration
def test_disk_cache_reduces_remote_io(minio_service, temp_bucket, tmp_path, caplog) -> None:
    """Verify disk blockcache reduces actual remote I/O (REMOTE_FS file_read calls).
    
    This test checks that the second read of the same file shows fewer REMOTE_FS file_read
    operations than the first read, proving blockcache is serving blocks from disk instead
    of re-fetching from MinIO.
    
    Note: Both trace_filesystem AND trace_file_io must be enabled for the cache to work
    correctly (they enable the logging wrapper which interacts properly with blockcache).
    """
    sample_file = Path(__file__).resolve().parents[1] / "data" / "test1.nc"
    object_name = "cache-test/test1.nc"
    minio_service.fput_object(temp_bucket, object_name, str(sample_file))

    # Use a unique cache dir per test run to avoid conflicts.
    cache_dir = tmp_path / "blockcache-reduce-io"
    cache_dir.mkdir(exist_ok=True)
    descriptor = _s3_descriptor(
        minio_service,
        cache={"disk_mode": "blocks", "disk_location": str(cache_dir), "blocksize_mb": 1, "max_blocks": 16},
    )

    # Reset logging state and enable BOTH trace flags (required for cache to work).
    RemoteAccessSession.configure_logging(trace_filesystem=False, trace_file_io=False)
    RemoteAccessSession.configure_logging(trace_filesystem=True, trace_file_io=True)
    original_send = worker.send_to_gui
    worker.remote_session_pool.clear()

    try:
        worker.send_to_gui = lambda prefix, data=None: None
        entry = worker._prepare_remote_session(
            session_id="disk-cache-reduce-verify",
            descriptor_hash="disk-cache-reduce-verify-hash",
            descriptor=descriptor,
        )

        # First read: cache is empty, all blocks must come from MinIO.
        with caplog.at_level(logging.INFO):
            caplog.clear()
            fields1 = worker._read_remote_fields(
                entry=entry,
                descriptor=descriptor,
                datasets=f"{temp_bucket}/{object_name}",
            )
            first_read_io_calls = _count_file_io_logs(caplog)

        assert fields1
        assert first_read_io_calls > 0, "First read should issue REMOTE_FS file_read calls to MinIO"
        assert (cache_dir / "cache").is_file(), "Blockcache index file should exist after first read"

        # Second read: blockcache should serve from disk, reducing remote I/O.
        with caplog.at_level(logging.INFO):
            caplog.clear()
            fields2 = worker._read_remote_fields(
                entry=entry,
                descriptor=descriptor,
                datasets=f"{temp_bucket}/{object_name}",
            )
            second_read_io_calls = _count_file_io_logs(caplog)

        assert fields2
        
        # Cache MUST reduce remote I/O on the second read when using cf.read()
        assert second_read_io_calls < first_read_io_calls, (
            f"Disk cache via cf.read() is BROKEN: "
            f"first read {first_read_io_calls} REMOTE_FS file_read ops, "
            f"second read {second_read_io_calls} REMOTE_FS file_read ops. "
            f"Either cf-python is not respecting the filesystem= parameter, "
            f"or blockcache integration is broken. This is causing users to wait minutes "
            f"for cached data to load."
        )
    finally:
        RemoteAccessSession.configure_logging(trace_filesystem=False, trace_file_io=False)
        worker.send_to_gui = original_send
        worker.remote_session_pool.clear()


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
    RemoteAccessSession.configure_logging(trace_file_io=False)
    RemoteAccessSession.configure_logging(trace_file_io=True)
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
        RemoteAccessSession.configure_logging(trace_file_io=False)
        worker.send_to_gui = original_send
        worker.remote_session_pool.clear()