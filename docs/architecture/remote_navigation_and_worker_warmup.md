# Remote Navigation and Worker Warm-Up Design

This document captures:

1. The current remote-navigation behavior implemented in xconv2.
2. The currently observed constraints in a two-process architecture.
3. The implemented UI-worker IPC contract for latency-hiding warm-up.

Related sequence diagram:

- `docs/uml/remote_worker_warmup_sequence.puml`

## Scope and Context

xconv2 runs as two processes:

1. UI process (`CFVCore` / `CFVMain`), handling dialogs and interaction.
2. Worker process (`cf-worker`), handling file/data operations and plotting.

Remote navigation currently happens in the UI process via the Remote Configuration dialog and Remote File Navigator dialog.

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

Before navigator opens, UI shows a login-progress dialog:

1. Displays connection progress text.
2. Closes automatically on successful login and root listing validation.
3. Stays open on failure until user closes it.

SSH ProxyJump support is implemented in the UI-side remote connector and logs connection steps.

## Stability Fixes for Deep Trees

For deep or large trees (especially S3):

1. Refresh after filter toggles preserves context.
2. Context restore now targets only the active branch (selected item and ancestors), not every expanded node.
3. Tree traversal for state restore uses iterative stack walking, avoiding recursion-depth failures.

## Two-Process Constraint

Open remote client/session objects are process-local and are not safely transferable between UI and worker.

Practical implication:

1. UI and worker must each establish their own connection state.
2. Reuse across processes should be done by passing normalized connection descriptors, not live handles.

## Worker Warm-Up via IPC (Implemented)

Goal:

1. Keep current UI browser responsiveness.
2. Hide worker-side connect/auth latency by warming worker connection in parallel while user navigates.

## High-Level Flow

1. User accepts remote configuration in UI.
2. UI sends `REMOTE_PREPARE` request to worker with normalized descriptor and session id.
3. Worker starts/establishes filesystem connection and stores it in a descriptor-keyed pool.
4. User continues browsing in UI.
5. On final open, UI sends `REMOTE_OPEN` request with same descriptor hash/session id.
6. Worker reuses warm connection when available.

## Suggested Task Headers (UI -> Worker)

Reuse current `#...` preamble convention:

1. `#TASK_KIND:REMOTE_PREPARE`
2. `#TASK_KIND:REMOTE_RELEASE`
3. `#TASK_KIND:REMOTE_OPEN`
4. `#TASK_PAYLOAD_B64:<base64-json>`

Payload fields:

1. `session_id`
2. `descriptor_hash`
3. `descriptor`
4. `uri` (for open)
5. `path` (filesystem-native path for open)
6. `hint_path` (optional)

Descriptor fields (normalized):

1. protocol (`S3`/`SSH`)
2. endpoint/host
3. user
4. identity file
5. proxyjump
6. auth-relevant options
7. cache options
8. root path

## Suggested Worker Status Messages (Worker -> UI)

1. `REMOTE_STATUS`:
- `phase` in `{preparing, ready, failed, released}`
- `session_id`
- `descriptor_hash`
- `message`

2. `REMOTE_OPEN_RESULT`:
- `session_id`
- `uri`
- `ok`
- `error` (optional)

3. Existing data-path messages reused during remote open:
- `METADATA` with the same field-list payload used by local file opens.
- `STATUS` remains in use for legacy code-task completion and plotting updates.

## Worker Session Pool Policy (Suggested)

1. Key pool entries by `descriptor_hash`.
2. Track `created_at`, `last_used`, and owning `session_id`.
3. Use idle TTL eviction (for example 120-300s).
4. Cap pool size (for example 2-4) and evict LRU.
5. Use "most recent session id wins" to avoid stale races.

## URI-Open Path

For direct URI open (without UI browsing):

1. Parse URI into descriptor.
2. Send `REMOTE_PREPARE` immediately.
3. Send `REMOTE_OPEN` using same descriptor hash/session id.
4. Worker uses warm entry if ready, otherwise falls back to cold connect.

## Worker Protocol Strategy Matrix

When the worker receives a `REMOTE_OPEN` request it must ultimately call `cf.read()` (or equivalent) to load the file. The path to that call differs significantly by protocol.

Investigation of the installed cf-python 3.19.0 + cfdm source confirmed the following dispatch behaviour for `cf.read(uri)`:

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

**Option B — fsspec streaming read + manual staging:**

The worker opens the file via `fsspec`/`sshfs` and writes it to a temporary local file, then calls `cf.read()` on the temp path:

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

**Recommended approach:** Use Option B as a safe fallback (no system dependencies) and add Option A as an opt-in if `sshfs` is detected. The warm-up phase (`REMOTE_PREPARE`) is the natural point to authenticate and hold the `fsspec` SSH connection open so the staging transfer in Option B starts immediately on `REMOTE_OPEN`.

## Current Status

Implemented now:

1. UI-side remote configuration and navigator (S3/SSH behavior above).
2. UI-side login progress dialog and ProxyJump support.
3. Filtering, symlink handling, Zarr labeling, and large-tree stability improvements.
4. Worker connection warm-up and descriptor-keyed remote session reuse via typed IPC tasks.
5. Remote file open path wired through `REMOTE_PREPARE` / `REMOTE_OPEN` / `REMOTE_RELEASE`.
6. Worker open fallbacks by protocol:
- S3: direct `cf.read(uri, storage_options=...)`
- HTTP: direct URI open
- SSH/SFTP: staged temporary local file when direct filesystem-based `cf.read` is unavailable

Not yet implemented:

1. Optional `sshfs`/FUSE mount path for SSH/SFTP as an alternative to staging.
2. Optional migration of remote listing/open to worker-owned I/O for single-owner semantics.
3. Any richer worker-side status timing/reporting beyond the current `REMOTE_STATUS` messages.
