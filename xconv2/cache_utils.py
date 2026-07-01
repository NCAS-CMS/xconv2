from __future__ import annotations

import json
import math
import pickle
import time
from pathlib import Path
from typing import Any, Callable


def parse_disk_expiry_seconds(raw: object) -> int:
    """Translate UI expiry labels into seconds."""
    text = str(raw or "").strip().lower()
    if text == "1 day":
        return 24 * 60 * 60
    if text == "7 days":
        return 7 * 24 * 60 * 60
    if text == "30 days":
        return 30 * 24 * 60 * 60
    return 0


def estimate_chunk_count_for_write(
    data_shape: tuple[int, ...],
    chunk_shape: tuple[int, ...],
) -> int:
    """Estimate the number of chunks produced by writing data with ``chunk_shape``.

    The count is computed per dimension as ``ceil(data_dim / chunk_dim)`` and
    multiplied across dimensions.
    """
    if len(data_shape) != len(chunk_shape):
        raise ValueError("data_shape and chunk_shape must have the same rank")

    if any(dim < 0 for dim in data_shape):
        raise ValueError("data_shape dimensions must be >= 0")

    if any(dim <= 0 for dim in chunk_shape):
        raise ValueError("chunk_shape dimensions must be > 0")

    if any(dim == 0 for dim in data_shape):
        return 0

    chunk_count = 1
    for data_dim, chunk_dim in zip(data_shape, chunk_shape):
        chunk_count *= int(math.ceil(float(data_dim) / float(chunk_dim)))

    return int(chunk_count)


def estimate_hdf5_metadata_bytes_for_write(
    btree_index_length_bytes: int,
    data_shape: tuple[int, ...],
    chunk_shape: tuple[int, ...],
    *,
    overhead_fraction: float = 0.20,
    attribute_metadata_bytes: int = 2048,
) -> int:
    """Estimate metadata bytes for a planned write layout.

    Uses the projected number of output chunks (from data/chunk shapes), then
    applies the metadata heuristic:
    ``btree_index_length_bytes * chunk_count`` plus overhead and attribute
    allowance.
    """
    if btree_index_length_bytes < 0:
        raise ValueError("btree_index_length_bytes must be >= 0")
    if overhead_fraction < 0:
        raise ValueError("overhead_fraction must be >= 0")
    if attribute_metadata_bytes < 0:
        raise ValueError("attribute_metadata_bytes must be >= 0")

    chunk_count = estimate_chunk_count_for_write(data_shape, chunk_shape)
    base_index_bytes = btree_index_length_bytes * chunk_count
    with_overhead = base_index_bytes * (1.0 + float(overhead_fraction))
    total = with_overhead + attribute_metadata_bytes
    return int(math.ceil(total))


def estimate_hdf5_metadata_bytes_for_fields(
    fields: list[object],
    *,
    btree_entry_size_bytes: int = 64,
    overhead_factor: float = 1.20,
    attribute_allowance_bytes: int = 2048,
) -> int:
    """Estimate HDF5 metadata bytes by looping over selected fields.

    For each field, this derives chunk count from ``field.shape`` and
    ``field.nc_dataset_chunksizes()`` and treats each chunk as one B-tree entry.
    """
    if btree_entry_size_bytes < 0:
        raise ValueError("btree_entry_size_bytes must be >= 0")
    if overhead_factor < 0:
        raise ValueError("overhead_factor must be >= 0")
    if attribute_allowance_bytes < 0:
        raise ValueError("attribute_allowance_bytes must be >= 0")

    def _normalize_chunk_shape(value: object) -> tuple[int, ...] | None:
        if value is None:
            return None

        if hasattr(value, "tolist") and callable(getattr(value, "tolist", None)):
            try:
                value = value.tolist()
            except Exception:
                return None

        if isinstance(value, int):
            return (int(value),)

        if isinstance(value, (tuple, list)):
            if all(isinstance(v, int) for v in value):
                return tuple(int(v) for v in value)

            if all(isinstance(v, (tuple, list)) and len(v) > 0 for v in value):
                first_chunks: list[int] = []
                for axis_chunks in value:
                    head = axis_chunks[0]
                    if not isinstance(head, int):
                        return None
                    first_chunks.append(int(head))
                return tuple(first_chunks)

        return None

    total_entries = 0
    for field in fields:
        raw_shape = getattr(field, "shape", None)
        if not isinstance(raw_shape, tuple):
            continue

        try:
            shape = tuple(int(dim) for dim in raw_shape)
        except Exception:
            continue

        get_chunks = getattr(field, "nc_dataset_chunksizes", None)
        if not callable(get_chunks):
            continue

        try:
            chunk_shape = _normalize_chunk_shape(get_chunks())
        except Exception:
            chunk_shape = None

        if chunk_shape is None:
            continue

        try:
            total_entries += estimate_chunk_count_for_write(shape, chunk_shape)
        except ValueError:
            continue

    base_bytes = btree_entry_size_bytes * total_entries
    total = (base_bytes * float(overhead_factor)) + attribute_allowance_bytes
    return int(math.ceil(total))


def disk_cache_usage(location: Path) -> tuple[int, int]:
    """Return total bytes and file count under a cache directory."""
    if not location.exists():
        return 0, 0

    total_bytes = 0
    total_files = 0
    for child in location.rglob("*"):
        if not child.is_file():
            continue
        total_files += 1
        try:
            total_bytes += child.stat().st_size
        except OSError:
            continue
    return total_bytes, total_files


def prune_disk_cache(
    location: Path,
    *,
    limit_bytes: int = 0,
    expiry_seconds: int = 0,
    log: Callable[[str], None] | None = None,
) -> dict[str, int]:
    """Prune expired and oversized cache files, keeping fsspec metadata coherent."""
    location = location.expanduser()
    if not location.exists():
        return {"removed_files": 0, "removed_bytes": 0, "total_bytes": 0, "total_files": 0}

    metadata_path = location / "cache"
    now = time.time()
    removed_files = 0
    removed_bytes = 0

    def _emit(message: str) -> None:
        if log is not None:
            log(message)

    def _load_metadata() -> tuple[dict[str, Any], str]:
        if not metadata_path.exists():
            return {}, "json"
        try:
            return json.loads(metadata_path.read_text(encoding="utf-8")), "json"
        except (OSError, json.JSONDecodeError):
            try:
                with metadata_path.open("rb") as handle:
                    return pickle.load(handle), "pickle"
            except Exception:
                return {}, "json"

    def _save_metadata(metadata: dict[str, Any], fmt: str) -> None:
        serializable: dict[str, Any] = {}
        for key, detail in metadata.items():
            if not isinstance(detail, dict):
                continue
            item = dict(detail)
            blocks = item.get("blocks")
            if isinstance(blocks, set):
                item["blocks"] = sorted(blocks)
            serializable[key] = item
        if fmt == "pickle":
            with metadata_path.open("wb") as handle:
                pickle.dump(serializable, handle)
        else:
            metadata_path.write_text(json.dumps(serializable), encoding="utf-8")

    def _iter_payload_files() -> list[tuple[Path, int, float]]:
        files: list[tuple[Path, int, float]] = []
        for child in location.rglob("*"):
            if not child.is_file() or child == metadata_path:
                continue
            try:
                stat = child.stat()
            except OSError:
                continue
            files.append((child, stat.st_size, stat.st_mtime))
        return files

    def _remove_file(path: Path, size: int) -> None:
        nonlocal removed_files, removed_bytes
        try:
            path.unlink()
        except OSError:
            return
        removed_files += 1
        removed_bytes += size

    payload_files = _iter_payload_files()
    if expiry_seconds > 0:
        for path, size, mtime in payload_files:
            if now - mtime > expiry_seconds:
                _remove_file(path, size)

    payload_files = _iter_payload_files()
    total_payload_bytes = sum(size for _, size, _ in payload_files)
    if limit_bytes > 0 and total_payload_bytes > limit_bytes:
        for path, size, _mtime in sorted(payload_files, key=lambda item: item[2]):
            if total_payload_bytes <= limit_bytes:
                break
            _remove_file(path, size)
            total_payload_bytes -= size

    metadata, fmt = _load_metadata()
    if metadata:
        filtered: dict[str, Any] = {}
        for key, detail in metadata.items():
            if not isinstance(detail, dict):
                continue
            fn = str(detail.get("fn", "")).strip()
            if not fn:
                continue
            if (location / fn).exists():
                filtered[key] = detail
        _save_metadata(filtered, fmt)

    for child in sorted(location.rglob("*"), reverse=True):
        if child.is_dir():
            try:
                child.rmdir()
            except OSError:
                pass

    total_bytes, total_files = disk_cache_usage(location)
    if removed_files:
        _emit(
            f"Pruned cache: removed {removed_files} files ({removed_bytes} bytes), now {total_bytes} bytes"
        )
    return {
        "removed_files": removed_files,
        "removed_bytes": removed_bytes,
        "total_bytes": total_bytes,
        "total_files": total_files,
    }
