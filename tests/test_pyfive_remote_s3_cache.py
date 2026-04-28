"""Test pyfive remote S3 caching behavior with cat_ranges toggle.

This test validates that the fsspec CachingFileSystem properly caches remote
S3 reads when used with pyfive, comparing serial read strategy with cat_ranges.

This test is somewhat personalised and relies on a specific dataset and S3 configuration, 
so it's marked as an integration test. It checks that:
1. The dataset can be read fully with both cat_ranges enabled and disabled.
2. The expected caching strategy is used in each case, as indicated by debug logs.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from xconv2.remote_access import RemoteFilesystemSpec, create_filesystem


@pytest.mark.integration
@pytest.mark.parametrize(
    ("cat_range_allowed", "expected_strategy"),
    [
        (True, "fsspec_cat_ranges"),
        (False, "serial"),
    ],
    ids=["cat-ranges-on", "cat-ranges-off"],
)
def test_pyfive_remote_s3_read_full_dataset_with_cache_and_cat_range_toggle(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    minio_service,
    temp_bucket: str,
    cat_range_allowed: bool,
    expected_strategy: str,
) -> None:
    """Test that pyfive uses fsspec CachingFileSystem for both read strategies.

    Validates that:
    1. Serial path (cat_ranges=False) works with proper cache reuse
    2. cat_ranges path (cat_ranges=True) should also cache but currently fails
       due to BlocksizeMismatchError in fsspec's CachingFileSystem.cat_ranges()
    """
    pyfive = pytest.importorskip("pyfive")

    sample_candidates = [
        Path(__file__).resolve().parents[1] / "data" / "da193a_25_3hr__198808-198808.nc",
        Path(__file__).resolve().parents[1] / "data" / "da193a_25_3hr__198807-198807.nc",
    ]
    sample_file = next((path for path in sample_candidates if path.is_file()), None)
    if sample_file is None:
        pytest.skip("S3 integration test requires da193a sample data in data/")

    object_name = sample_file.name
    minio_service.fput_object(temp_bucket, object_name, str(sample_file))

    endpoint_url = str(getattr(minio_service, "endpoint_url", "")).strip()
    access_key = "minioadmin"
    secret_key = "minioadmin"

    dataset_path = f"{temp_bucket}/{object_name}"
    spec = RemoteFilesystemSpec(
        protocol="s3",
        storage_options={
            "key": access_key,
            "secret": secret_key,
            "client_kwargs": {"endpoint_url": endpoint_url},
        },
        root_path="",
        display_name="minio-s3",
        uri_scheme="s3",
        uri_authority="",
    )
    cache_dir = tmp_path / "remote-cache"

    logger_names = ["fsspec", "fsspec.cached", "fsspec.caching", "pyfive", "s3fs"]
    previous_levels = {name: logging.getLogger(name).level for name in logger_names}

    try:
        for name in logger_names:
            logging.getLogger(name).setLevel(logging.DEBUG)

        def _read_once() -> tuple[tuple[int, int, int], str, list[logging.LogRecord]]:
            caplog.clear()
            with caplog.at_level(logging.DEBUG):
                filesystem = create_filesystem(
                    spec,
                    cache={
                        "disk_mode": "Blocks",
                        "disk_location": str(cache_dir),
                        "disk_expiry": "1 day",
                        "disk_limit_gb": 1,
                    },
                )

                with filesystem.open(dataset_path, "rb") as handle:
                    with pyfive.File(handle) as remote_file:
                        dataset = remote_file["m01s00i507_10"]
                        dataset.id.set_parallelism(
                            thread_count=0,
                            cat_range_allowed=cat_range_allowed,
                            btree_parallel=False,
                        )
                        data = dataset[:]

            return data.shape, str(data.dtype), list(caplog.records)

        first_shape, first_dtype, first_records = _read_once()
        second_shape, second_dtype, second_records = _read_once()

        assert first_shape == (240, 324, 432)
        assert second_shape == (240, 324, 432)
        assert first_dtype == "float32"
        assert second_dtype == "float32"

        first_messages = [record.getMessage() for record in first_records]
        second_messages = [record.getMessage() for record in second_records]

        assert any("Creating local sparse file" in message for message in first_messages)
        assert any(expected_strategy in message for message in first_messages)
        assert any(expected_strategy in message for message in second_messages)

        first_remote_reads = sum(
            1
            for record in first_records
            if record.name.startswith("s3fs") and "CALL: get_object" in record.getMessage()
        )
        second_remote_reads = sum(
            1
            for record in second_records
            if record.name.startswith("s3fs") and "CALL: get_object" in record.getMessage()
        )

        assert first_remote_reads > 0

        assert any(
            "Opening partially cached copy" in message or "Opening local copy" in message
            for message in second_messages
        )
        assert second_remote_reads == 0

        cached_files = [path for path in cache_dir.rglob("*") if path.is_file()]
        assert cached_files
    finally:
        for name, level in previous_levels.items():
            logging.getLogger(name).setLevel(level)
