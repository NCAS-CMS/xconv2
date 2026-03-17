# Remote Navigation and Worker Design

This document captures:

1. The current remote-navigation behavior implemented in xconv2.
2. The single-login architecture via worker-owned IPC.
3. The UI-worker IPC contract for login, navigation, and file open.

Related sequence diagram:

- `docs/uml/remote_worker_warmup_sequence.puml`

## Scope and Context

xconv2 runs as two processes:

1. UI process (`CFVCore` / `CFVMain`), handling dialogs and interaction.
2. Worker process (`cf-worker`), handling file/data operations and plotting.

Remote login and navigation are owned exclusively by the worker process. The UI holds no live filesystem connection; all directory listing and file reading travel through the IPC pipe.

## Current Remote Navigation (Implemented)

## Configuration Dialog

The remote configuration dialog supports:

1. S3 tab:
- Existing configuration selection.
- Add new configuration and save to MinIO-style config file.

2. SSH tab:
- Existing host selection from `~/.ssh/config`.
- Add new host with:
  - short name (alias)
  - hostname
  - user
  - identity file
  - optional ProxyJump

3. HTTP tab:
- Placeholder only.

Configuration state is persisted in settings under `last_remote_configuration`.

## Navigator Dialog

Remote file navigator behavior includes:

1. Lazy directory loading on expand.
2. Default filtering to directories plus `.nc` and `.pp` files.
3. Toggle: `Show all files`.
4. SSH-only toggle: `Show hidden files`.
5. Human-readable file sizes in `B`, `KB`, `MB`, `GB`, `TB`.
6. Name column default width of roughly 40 characters.
7. Symlink handling:
- Explicit type labels (`Link to file`, `Link to folder`).
- Directory symlinks are navigable.
8. Zarr labeling:
- Any directory ending in `.zarr` is labeled `Zarr` immediately.
- If a directory listing contains Zarr metadata files (`.zgroup`, `.zarray`, `.zmetadata`, `zarr.json`), that directory is relabeled `Zarr` after expansion.
- Zarr detection uses unfiltered directory contents (so hidden/default filters do not suppress detection).

## Connection/Login UX

After the configuration dialog is accepted:

1. A `RemoteLoginLogDialog` is shown immediately, displaying connection progress lines forwarded from the worker.
2. `REMOTE_PREPARE` is sent to the worker and a `QEventLoop` in `CFVMain._choose_remote` blocks until the worker emits `REMOTE_STATUS {phase=ready}` or `REMOTE_STATUS {phase=failed}`.
3. On success the login dialog closes automatically and the file navigator opens.
4. On failure the login dialog remains open until the user closes it.

No filesystem is ever created in the UI process. SSH ProxyJump tunnelling is handled entirely in the worker, which logs each connection step back to the login dialog via `REMOTE_STATUS` messages.

## Worker-Backed Directory Listing

`RemoteFileNavigatorDialog` accepts an optional `list_callback` parameter — a callable `(path: str) -> list[RemoteEntry]`. When provided:

- The dialog does not create a local filesystem.
- Each tree node expansion calls `list_callback(path)`.
- `CFVMain._make_worker_list_callback()` returns a closure that issues `REMOTE_LIST` to the worker and waits on a nested `QEventLoop` until `REMOTE_LIST_RESULT` arrives.
- The worker reuses the already-warm session from its pool, performs `fs.ls()`, normalizes entries, and resolves symlinks before replying.

Round-trip IPC cost per `ls()` call is 0.1–2 ms, negligible against the 30–500 ms remote server latency.

## Stability Fixes for Deep Trees

For deep or large trees (especially S3):

1. Refresh after filter toggles preserves context.
2. Context restore now targets only the active branch (selected item and ancestors), not every expanded node.
3. Tree traversal for state restore uses iterative stack walking, avoiding recursion-depth failures.

## IPC Task Headers (UI -> Worker)

All tasks use the existing `#...` preamble convention:

1. `#TASK_KIND:REMOTE_PREPARE`
2. `#TASK_KIND:REMOTE_LIST`
3. `#TASK_KIND:REMOTE_OPEN`
4. `#TASK_KIND:REMOTE_RELEASE`
5. `#TASK_PAYLOAD_B64:<base64-pickle>`

Common payload fields:

1. `session_id`
2. `descriptor_hash`
3. `descriptor`

Additional fields for `REMOTE_LIST`:

- `path` (filesystem-native path to list)

Additional fields for `REMOTE_OPEN`:

- `uri` (user-facing URI string)
- `path` (filesystem-native path for open)

Descriptor fields (normalized):

1. protocol (`S3`/`SSH`)
2. endpoint/host
3. user
4. identity file
5. proxyjump
6. auth-relevant options
7. cache options
8. root path

## IPC Messages (Worker -> UI)

1. `REMOTE_STATUS`:
- `phase` in `{preparing, ready, failed, released}`
- `session_id`
- `descriptor_hash`
- `message`

2. `REMOTE_LIST_RESULT`:
- `path`
- `entries` — `list[RemoteEntry]` (normalized, symlinks resolved)
- `error` — error string or `None`

3. `REMOTE_OPEN_RESULT`:
- `session_id`
- `uri`
- `ok`
- `error` (optional)

4. Existing data-path messages reused during remote open:
- `METADATA` with the same field-list payload used by local file opens.
- `STATUS` remains in use for legacy code-task completion and plotting updates.

## Worker Session Pool Policy

1. Key pool entries by `descriptor_hash`.
2. Track `created_at`, `last_used`, and owning `session_id`.
3. Use idle TTL eviction (for example 120-300s).
4. Cap pool size (for example 2-4) and evict LRU.
5. Use "most recent session id wins" to avoid stale races.

By the time `REMOTE_OPEN` arrives, the session pool always contains a warm entry for the current descriptor (established during `REMOTE_PREPARE` and kept alive by the `REMOTE_LIST` calls during navigation). Cold-open fallback in `REMOTE_OPEN` remains as a safety net.

## URI-Open Path

For direct URI open (without UI browsing):

1. Parse URI into descriptor.
2. Send `REMOTE_PREPARE` and wait for `REMOTE_STATUS {phase=ready}`.
3. Send `REMOTE_OPEN` using same descriptor hash/session id.
4. Worker reuses warm entry.

## Worker Protocol Strategy Matrix

When the worker receives a `REMOTE_OPEN` request it calls `cf.read()` (or equivalent). The path differs by protocol.

| URI scheme | cf/cfdm handling | Outcome |
|---|---|---|
| `file://` or plain path | Passed directly to h5netcdf / netCDF4 backends | Works for local files |
| `s3://` | Opens via `s3fs.S3FileSystem(**storage_options)`, yields fsspec path object to backend | Works natively |
| `http://` / `https://` | Yielded as-is to backends; h5netcdf recognises OPeNDAP URLs | Works for OPeNDAP servers |
| `ssh://` / `sftp://` | No handler — passed as a string to h5netcdf/netCDF4, which raise `FileNotFoundError` / `OSError` | **Fails with `DatasetTypeError`** |

### Per-Protocol Worker Strategy

#### S3

Pass URI directly to `cf.read()` with `storage_options`:

```python
cf.read("s3://bucket/path/to/file.nc", storage_options={"key": ..., "secret": ..., "endpoint_url": ...})
```

No pre-staging required. Worker can reuse an `s3fs.S3FileSystem` from its session pool by passing it inside `storage_options`.

#### HTTP / OPeNDAP

Pass URI directly to `cf.read()`:

```python
cf.read("https://server/opendap/path/to/file.nc")
```

Works for any OPeNDAP-compliant server. No credentials or pre-staging required for public servers; private OPeNDAP servers may require `storage_options` with session tokens depending on the backend.

#### SSH / SFTP

`cf.read("ssh://...")` is **not supported** — confirmed from source code and runtime (`DatasetTypeError`). Two viable strategies exist:

**Option A — OS-level FUSE mount (sshfs):**

The worker (or a pre-launch hook) mounts the remote filesystem locally via `sshfs`:

```
sshfs user@host:/remote/root /tmp/xconv2-mounts/hostname
```

The worker then calls `cf.read()` with a plain local path:

```python
cf.read(f"/tmp/xconv2-mounts/{hostname}{remote_path}")
```

Pros: Transparent to cf-python; all cf features work.
Cons: Requires `sshfs`/FUSE on the worker host; mount lifecycle must be managed; may not be available in all deployment environments.

**Option B — fsspec streaming read + manual staging (implemented):**

The worker opens the file via `fsspec` and writes it to a temporary local file, then calls `cf.read()` on the temp path:

```python
import fsspec, tempfile, os

fs = fsspec.filesystem("ssh", host=host, username=user, key_filename=identity)
with fs.open(remote_path, "rb") as remote_f:
    with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as tmp:
        tmp.write(remote_f.read())
        tmp_path = tmp.name
try:
    fields = cf.read(tmp_path)
finally:
    os.unlink(tmp_path)
```

Pros: No FUSE dependency; works in any Python environment.
Cons: Full file must be transferred before `cf.read()` begins; slow for large files.

Option B is currently in use as the default SSH/SFTP strategy.

## Current Status

Implemented now:

1. Remote configuration dialog (S3/SSH/HTTP-placeholder).
2. Worker-owned login: single `REMOTE_PREPARE` establishes the session; login progress forwarded to UI via `REMOTE_STATUS` messages and a nested `QEventLoop`.
3. Worker-backed directory listing via `REMOTE_LIST` / `REMOTE_LIST_RESULT` and a nested `QEventLoop` per tree node expansion in `RemoteFileNavigatorDialog`.
4. Filtering, symlink handling, Zarr labeling, and large-tree stability improvements (all in the UI, applied to entries received from the worker).
5. Remote file open path via `REMOTE_OPEN`; session always warm by the time open is requested.
6. Worker open fallbacks by protocol:
- S3: direct `cf.read(uri, storage_options=...)`
- HTTP: direct URI open
- SSH/SFTP: staged temporary local file when direct filesystem-based `cf.read` is unavailable
7. `REMOTE_RELEASE` on target change or window close.

Not yet implemented:

1. Optional `sshfs`/FUSE mount path for SSH/SFTP as an alternative to staging.
2. Any richer worker-side status timing/reporting beyond the current `REMOTE_STATUS` messages.
