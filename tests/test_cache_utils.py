from __future__ import annotations

import pytest

from xconv2.cache_utils import (
    estimate_chunk_count_for_write,
    estimate_hdf5_metadata_bytes_for_fields,
    estimate_hdf5_metadata_bytes_for_write,
)


def test_estimate_chunk_count_for_write_uses_ceiling_per_dimension() -> None:
    # ceil(100/32)=4, ceil(64/16)=4, ceil(10/4)=3 => 4*4*3 = 48
    assert estimate_chunk_count_for_write((100, 64, 10), (32, 16, 4)) == 48


def test_estimate_chunk_count_for_write_validates_shapes() -> None:
    with pytest.raises(ValueError, match="same rank"):
        estimate_chunk_count_for_write((10, 20), (5,))

    with pytest.raises(ValueError, match="data_shape"):
        estimate_chunk_count_for_write((-1, 20), (5, 5))

    with pytest.raises(ValueError, match="chunk_shape"):
        estimate_chunk_count_for_write((10, 20), (5, 0))


def test_estimate_chunk_count_for_write_zero_data_dim_returns_zero() -> None:
    assert estimate_chunk_count_for_write((10, 0, 20), (5, 1, 5)) == 0


def test_estimate_hdf5_metadata_bytes_for_write_derives_chunk_count() -> None:
    # chunk_count = ceil(100/32)*ceil(64/16) = 4*4 = 16
    # base = 32*16 = 512; +20% => 615; +2048 => 2663 (ceil)
    assert estimate_hdf5_metadata_bytes_for_write(32, (100, 64), (32, 16)) == 2663


def test_estimate_hdf5_metadata_bytes_for_write_validates_parameters() -> None:
    with pytest.raises(ValueError, match="btree_index_length_bytes"):
        estimate_hdf5_metadata_bytes_for_write(-1, (10, 10), (5, 5))

    with pytest.raises(ValueError, match="overhead_fraction"):
        estimate_hdf5_metadata_bytes_for_write(32, (10, 10), (5, 5), overhead_fraction=-0.1)

    with pytest.raises(ValueError, match="attribute_metadata_bytes"):
        estimate_hdf5_metadata_bytes_for_write(32, (10, 10), (5, 5), attribute_metadata_bytes=-1)


def test_estimate_hdf5_metadata_bytes_for_write_allows_custom_terms() -> None:
    # chunk_count = ceil(20/10)*ceil(20/10) = 4
    # base = 50*4 = 200; +10% => 220; +512 => 732
    assert estimate_hdf5_metadata_bytes_for_write(
        50,
        (20, 20),
        (10, 10),
        overhead_fraction=0.10,
        attribute_metadata_bytes=512,
    ) == 732


def test_estimate_hdf5_metadata_bytes_for_fields_uses_defaults() -> None:
    class _Field:
        def __init__(self, shape: tuple[int, ...], chunks: tuple[int, ...]) -> None:
            self.shape = shape
            self._chunks = chunks

        def nc_dataset_chunksizes(self):
            return self._chunks

    fields = [_Field((20, 20), (10, 10)), _Field((30, 21), (6, 7))]
    # entries: 4 + 15 = 19
    # bytes: 19*64=1216; *1.20 => 1459.2; +2048 => 3507.2; ceil => 3508
    assert estimate_hdf5_metadata_bytes_for_fields(fields) == 3508


def test_estimate_hdf5_metadata_bytes_for_fields_validates_parameters() -> None:
    with pytest.raises(ValueError, match="btree_entry_size_bytes"):
        estimate_hdf5_metadata_bytes_for_fields([], btree_entry_size_bytes=-1)

    with pytest.raises(ValueError, match="overhead_factor"):
        estimate_hdf5_metadata_bytes_for_fields([], overhead_factor=-1.0)

    with pytest.raises(ValueError, match="attribute_allowance_bytes"):
        estimate_hdf5_metadata_bytes_for_fields([], attribute_allowance_bytes=-1)
