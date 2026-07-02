# Remote Cache Misses: Quick Dev Guide

This note summarizes likely causes of unexpected remote cache misses and how to use the diagnostics toggle for A/B checks.

## Diagnostics Toggle

Environment variable:

- `XCONV2_REMOTE_CACHE_DIAGNOSTICS`

Values:

- Enabled (default): unset, `1`, `true`, `yes`, `on`
- Disabled: `0`, `false`, `no`, `off`

Examples:

```bash
# Disable cache diagnostics capture (A path)
export XCONV2_REMOTE_CACHE_DIAGNOSTICS=0
xconv2

# Enable cache diagnostics capture (B path)
export XCONV2_REMOTE_CACHE_DIAGNOSTICS=1
xconv2
```

When disabled, worker logs include:

- `REMOTE_CACHE_SUMMARY ... diagnostics=disabled`

When enabled, worker logs include per-open stats:

- `REMOTE_CACHE_SUMMARY ... delta_hits=... delta_misses=... http_requests=...`

## Why Misses May Happen

1. New byte ranges are requested.
- Block cache hits only apply to previously fetched ranges.
- Reopen can still miss if access jumps to unseen offsets.

2. Worker session is not reused.
- If session reuse fails (or TTL eviction occurs), per-handle mmap state starts cold.
- Check `session_reused=` in `REMOTE_CACHE_SUMMARY`.

3. Disk cache location changed.
- If cache directory differs between opens, prior blocks are not found.
- Check `cache_location=` in `REMOTE_CACHE_SUMMARY`.

4. Cache pruning removed prior blocks.
- `prune_disk_cache` runs during filesystem creation and may delete old data based on limits/expiry.

5. Block size mismatch.
- Cached metadata and active open path must agree on block size.
- Mismatch can force fallback fetches.

6. Cache mode effectively disabled.
- If disk mode is disabled, reopen relies on in-memory state only.
- Check `cache_mode=` in `REMOTE_CACHE_SUMMARY`.

7. Timing-sensitive behavior (Heisenbug).
- Added logging/handlers can change scheduling and hide or reveal races.
- Use the diagnostics toggle for A/B reproducibility.

## Server/Proxy Notes

For current HTTP configuration in this project:

- `CachingFileSystem` is used with `check_files=False`.
- So cache validation is not using per-request ETag checks.
- A pure ETag rotation issue is therefore unlikely to be the primary invalidation trigger.

Server-side behavior can still affect latency, but repeated misses here are more likely from client-side session/cache lifecycle, cache path consistency, pruning, or access-pattern differences.

## Fast Triage

1. Compare two consecutive opens of the same URI.
2. Confirm `session_reused=True` on second open.
3. Confirm `cache_mode` and `cache_location` are unchanged.
4. Compare `delta_misses` and `http_requests` on reopen.
5. If behavior differs with diagnostics on/off, treat as timing-sensitive and investigate lifecycle/race boundaries around remote open/close.
