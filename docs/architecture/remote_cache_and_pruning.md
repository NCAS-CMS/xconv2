# Remote Caching and Pruning Summary

This note summarizes the remote caching work in xconv2, how users should use it, and important caveats.

## Scope

The caching behavior described here applies to remote opens that go through the worker-side filesystem path and then call `cf.read(..., filesystem=fs)`.

## What Is Implemented

### 1) Cache configuration captured in the GUI

Remote Configuration includes cache controls:

- Memory cache strategy: `None`, `Block`, `Readahead`, `Whole-File`
- Block size (MB)
- RAM buffer (MB)
- Disk cache mode: `Disabled`, `Blocks`, `Files`
- Disk location
- Disk limit (GB)
- Disk expiry (`Never`, `1 day`, `7 days`, `30 days`)

These values are stored in the configuration state and passed through to the worker descriptor.

### 2) Worker-side cache application

When the worker prepares a remote session, it now applies selected cache options to the filesystem:

- Disk cache mode `Blocks` -> fsspec `blockcache`
- Disk cache mode `Files` -> fsspec `filecache`
- Memory strategy `Block` -> `cache_type="bytes"`
- Memory strategy `Readahead` -> `cache_type="readahead"`
- Memory strategy `Whole-File` -> `cache_type="all"`
- Memory strategy `None` -> no memory cache defaults injected

Disk cache wrapper options include:

- cache storage path from GUI
- expiry seconds derived from GUI expiry label
- block size (for blockcache)

### 3) Automatic pruning

Before applying disk cache wrappers, xconv2 now prunes the configured cache location using:

- expiry policy (remove files older than configured expiry)
- size policy (if still above configured limit, remove oldest files first)

After file removal, the fsspec cache metadata index file is rewritten so removed payload entries are dropped.

### 4) Cache Manager in xconv menu

A new xconv menu item opens a cache manager dialog.

The dialog provides:

- current cache configuration summary
- measured disk cache usage (bytes and file count)
- `Refresh`
- `Prune Cache` (apply policy-based pruning)
- `Flush Cache` (delete all cache payloads)

Both prune and flush release any active remote session first.

## How Users Should Use It

## Recommended defaults

- Start with memory strategy `Readahead` or `Block`.
- Use disk mode `Blocks` for random-access NetCDF/HDF-style workloads.
- Set a dedicated disk cache location with enough space.
- Set a realistic size limit and non-zero expiry (for example `7 days`).

## Operational workflow

1. Open xconv -> Configure Remote.
2. Set cache options in the Cache Configuration section.
3. Open remote data normally.
4. Use xconv -> Manage Cache... to inspect usage.
5. Use `Prune Cache` periodically, or `Flush Cache` when you want a hard reset.

## Caveats and Notes

- Disk limit enforcement is best-effort and file-based.
- Pruning uses payload file mtimes for age decisions.
- Metadata coherence is maintained for the writable cache index, but this is still a pragmatic cleanup approach.
- `disk_limit_gb` and expiry are now active for pruning, but eviction is not continuous/background; pruning occurs when sessions are prepared and when manually requested.
- `None` memory strategy disables injected read-cache defaults, but any lower-level library buffering still applies.
- If cache location is shared externally or modified concurrently, cache index consistency may degrade until next open/prune cycle.

## Developer Pointers

Main implementation points:

- `xconv2/cache_utils.py`: usage reporting, expiry parsing, pruning
- `xconv2/ui/remote_file_navigator.py`: cache strategy mapping and filesystem wrapping
- `xconv2/worker.py`: cache descriptor propagation into worker-side filesystem creation
- `xconv2/core_window.py`: cache manager UI and prune/flush actions
- `xconv2/ui/menu_controller.py`: xconv menu entry for cache manager
