from __future__ import annotations

import json
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
