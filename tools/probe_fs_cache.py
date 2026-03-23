#!/usr/bin/env python3
"""
Standalone probe: does fsspec blockcache make real underlying FS calls on warm opens?

Usage:
    python tools/probe_fs_cache.py /path/to/file.nc [--cache-dir /tmp/probe_cache]

The script subclasses LocalFileSystem to add logging, applies blockcache on top,
then does two cf.read() passes (cold, warm).  Every call that reaches the
underlying filesystem is printed, so you can see exactly what blockcache does on
the second (warm) read.
"""

import argparse
import logging
import shutil
import sys
import tempfile
import time
from pathlib import Path


_log = logging.getLogger("probe_fs")


# ---------------------------------------------------------------------------
# Logging filesystem subclass (no Qt / xconv2 dependencies)
#
# Subclassing LocalFileSystem (rather than using a plain proxy) means all
# required class-level attributes (async_impl, mirror_sync_methods, glob, …)
# are properly inherited so that fsspec's BlockCache.__getattribute__ does not
# raise AttributeError when it introspects type(fs).
# ---------------------------------------------------------------------------

def _make_logging_fs(label: str):
    """Return a LocalFileSystem subclass that logs every major I/O call."""
    from fsspec.implementations.local import LocalFileSystem  # noqa: PLC0415

    class _LoggingLocalFS(LocalFileSystem):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.__label = label

        def _t(self, method: str, path: str, elapsed_ms: int, **extra) -> None:
            extra_str = "  ".join(f"{k}={v!r}" for k, v in extra.items())
            _log.info("REMOTE_FS %s  label=%s  path=%r  elapsed_ms=%d  %s", method, self.__label, path, elapsed_ms, extra_str)

        def _open(self, path, mode="rb", **kwargs):
            t0 = time.perf_counter()
            result = super()._open(path, mode=mode, **kwargs)
            self._t("_open", path, int((time.perf_counter() - t0) * 1000), mode=mode)
            return result

        def info(self, path, **kwargs):
            t0 = time.perf_counter()
            result = super().info(path, **kwargs)
            self._t("info", path, int((time.perf_counter() - t0) * 1000))
            return result

        def ls(self, path, detail=True, **kwargs):
            t0 = time.perf_counter()
            result = super().ls(path, detail=detail, **kwargs)
            self._t("ls", path, int((time.perf_counter() - t0) * 1000), count=len(result) if result else 0)
            return result

        def glob(self, path, **kwargs):
            t0 = time.perf_counter()
            result = super().glob(path, **kwargs)
            self._t("glob", path, int((time.perf_counter() - t0) * 1000), count=len(result))
            return result

        def exists(self, path, **kwargs):
            t0 = time.perf_counter()
            result = super().exists(path, **kwargs)
            self._t("exists", path, int((time.perf_counter() - t0) * 1000), result=result)
            return result

        def cat_file(self, path, start=None, end=None, **kwargs):
            t0 = time.perf_counter()
            result = super().cat_file(path, start=start, end=end, **kwargs)
            self._t("cat_file", path, int((time.perf_counter() - t0) * 1000), size=len(result))
            return result

    return _LoggingLocalFS()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", help="Local .nc file to probe")
    parser.add_argument("--cache-dir", help="Cache directory (default: a temp dir that is cleaned up)")
    parser.add_argument("--block-size-mb", type=int, default=2, help="Block size in MiB (default: 2)")
    parser.add_argument("--keep-cache", action="store_true", help="Do not delete cache dir on exit")
    args = parser.parse_args()

    filepath = Path(args.file).resolve()
    if not filepath.exists():
        sys.exit(f"File not found: {filepath}")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    own_cache = args.cache_dir is None
    cache_dir = Path(args.cache_dir) if args.cache_dir else Path(tempfile.mkdtemp(prefix="probe_fs_cache_"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    _log.info("Cache dir: %s", cache_dir)
    _log.info("File:      %s", filepath)
    _log.info("Block size: %d MiB", args.block_size_mb)

    try:
        import cf  # noqa: PLC0415
        import fsspec  # noqa: PLC0415
    except ImportError as exc:
        sys.exit(f"Missing dependency: {exc}")

    def make_fs():
        base = _make_logging_fs(label="file")
        return fsspec.filesystem(
            "blockcache",
            fs=base,
            cache_storage=str(cache_dir),
            block_size=args.block_size_mb * 1024 * 1024,
            check_files=False,
        )

    def run_pass(label: str) -> tuple[int, float]:
        """Run one cf.read() pass; return (field_count, elapsed_s)."""
        print(f"\n{'=' * 60}")
        print(f"{label}")
        print("=" * 60)

        # Count REMOTE_FS log records emitted during this pass
        class _Counter(logging.Handler):
            def __init__(self):
                super().__init__()
                self.counts: dict[str, int] = {}

            def emit(self, record: logging.LogRecord) -> None:
                msg = record.getMessage()
                if msg.startswith("REMOTE_FS "):
                    method = msg.split()[1]
                    self.counts[method] = self.counts.get(method, 0) + 1

        counter = _Counter()
        logging.getLogger("probe_fs").addHandler(counter)
        try:
            t0 = time.perf_counter()
            fs = make_fs()
            fields = cf.read(path_str, filesystem=fs)
            elapsed = time.perf_counter() - t0
        finally:
            logging.getLogger("probe_fs").removeHandler(counter)

        total = sum(counter.counts.values())
        summary = "  ".join(f"{m}×{n}" for m, n in sorted(counter.counts.items()))
        print(f"\n  => {len(fields)} field(s) in {elapsed:.3f}s  |  {total} FS calls: {summary}")
        return len(fields), elapsed

    path_str = str(filepath)

    _, elapsed1 = run_pass("PASS 1 — cold read (cache empty)")
    _, elapsed2 = run_pass("PASS 2 — warm read (cache populated)")

    print(f"\nSpeedup: {elapsed1 / elapsed2:.1f}x  (cold={elapsed1:.3f}s  warm={elapsed2:.3f}s)")

    if own_cache and not args.keep_cache:
        shutil.rmtree(cache_dir, ignore_errors=True)
        _log.info("Cache dir cleaned up")
    else:
        _log.info("Cache dir kept: %s", cache_dir)


if __name__ == "__main__":
    main()
