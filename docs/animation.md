# Animation Design Draft

## Why this document exists

We need two things at once:

1. Correct animation rendering semantics in cf-plot (levels, title templates, map reuse, axis handling).
2. Reliable frame transport/playback semantics in xconv2 (worker to GUI streaming).

The design below keeps those concerns separate.

## Core architecture split

- cf-plot owns plot correctness and per-frame rendering decisions.
- xconv2 owns frame transport protocol and GUI playback.

This means we keep using cf-plot animation mode, but add explicit frame hooks so xconv2 can receive intermediate frames.

## Compatibility requirement

Existing cf-plot animation behavior that writes frame files for downstream tools must remain fully supported.

Specifically:

- Any current path that writes sequential animation frames to disk must continue to work unchanged.
- New callback kwargs must be additive only; they must not replace or disable file-writing behavior.
- If callbacks are provided, frame files should still be produced when existing options request them.
- If callbacks are omitted, output and side effects should match current behavior.

Non-goal:

- This proposal does not migrate users away from file-based animation workflows.

## Proposed minimal cf-plot hook API

### New optional kwargs on gopen for animation streaming

Instead of polluting `cfp.con()`, streaming configuration belongs in output setup:

```python
cfp.gopen(
    file="cfplot.png",
    user_plot=1,
    animation_session_id=session_id,
    animation_frame_callback=on_frame,
    animation_meta_callback=on_meta,
)
```

Then call con with existing animation kwargs unchanged:

```python
cfp.con(
    frame,
    ptype=1,
    animation=True,
    animation_reference=f,
    reuse_map_background=True,
    clear_previous_frame=True,
    animation_axis="auto",
    animation_title_template="{title} [{frame}]",
    lines=False,
)
```

### New kwargs (on gopen only)

Only these kwargs are new API additions (to gopen):

- animation_session_id
- animation_frame_callback
- animation_meta_callback

### Existing kwargs unchanged

On `cfp.con()`, these kwargs remain unchanged from today:

- ptype
- animation
- animation_reference
- reuse_map_background
- clear_previous_frame
- animation_axis
- animation_title_template
- lines

Suggested compatibility rule:

- If new gopen kwargs are omitted (or None), behavior is identical to current cf-plot animation behavior.
- If callbacks are provided on gopen, they are passive notifications and must not change plotting state.
- gopen can set these kwargs once before a contour animation loop; they persist for that session.

### Callback signatures

```python
def on_meta(meta: dict[str, object]) -> None:
    """Called once before first frame (or as soon as metadata is known)."""

def on_frame(frame_event: dict[str, object]) -> None:
    """Called after each frame is fully drawn to canvas."""
```

### Required meta payload keys

- session_id: str
- total_frames: int | None
- fps_hint: float | None
- title_template: str | None
- plot_kind: str  # "contour"
- levels_locked: bool

### Required frame_event payload keys

- session_id: str
- frame_index: int  # 0-based
- frame_value: object | None  # coordinate value if available
- canvas_ready: bool  # true when frame is fully rendered
- timestamp: float

Notes:

- cf-plot controls levels and map reuse exactly as today.
- callback is notification only; xconv2 captures the canvas image in the worker.

## Callback insertion points in cf-plot code

This section describes where callbacks should be invoked inside cf-plot so contour animation semantics remain unchanged.

### High-level placement

Callbacks are stored in gopen state and referenced during the animation branch of `cfp.con(...)` execution.
Callback wiring belongs in the animation branch of contour rendering only, not in non-animation contour paths.

### Proposed control flow

1. Enter `cfp.con(...)`.
2. Parse kwargs, including new optional callback kwargs.
3. If `animation` is false:
   - run current static contour logic unchanged.
4. If `animation` is true:
   - compute animation frame iterator exactly as current implementation does.
   - determine animation metadata once available (for example total frame count if known, title template, levels mode).
   - invoke `animation_meta_callback(meta)` once before first frame draw.
5. For each frame in the animation loop:
   - apply existing per-frame state updates (subspace, title token substitution, map background reuse, clear_previous_frame behavior, level handling).
   - perform the normal draw/render call.
   - after draw is complete, invoke `animation_frame_callback(frame_event)`.
6. Exit animation path with existing return behavior unchanged.

### Important ordering constraints

- `animation_meta_callback` must happen before the first `animation_frame_callback`.
- `animation_frame_callback` must be called after frame draw completes, not before.
- Exceptions raised by callbacks should be caught and logged so rendering continues unless a strict mode is explicitly enabled.
- Callback invocation must not mutate internal contour state.
- Existing frame-on-disk writes (when configured) must run as they do today; callback hooks cannot short-circuit or reorder them.

### Pseudocode sketch

```python
# In gopen(...)
def gopen(file, **kwargs):
    session_id = kwargs.get("animation_session_id")
    on_meta = kwargs.get("animation_meta_callback")
    on_frame = kwargs.get("animation_frame_callback")
    # Store callbacks in module/class state for later use
    _store_animation_callbacks(session_id, on_meta, on_frame)
    # ... rest of gopen logic

# In con(...) during animation
def con(field, **kwargs):
    animation = bool(kwargs.get("animation", False))

    if not animation:
        return _con_static(field, **kwargs)

    # Retrieve callbacks from gopen state
    session_id, on_meta, on_frame = _retrieve_animation_callbacks()

    frames = _resolve_animation_frames(field, kwargs)
    meta = {
        "session_id": session_id,
        "total_frames": _safe_len_or_none(frames),
        "title_template": kwargs.get("animation_title_template"),
        "plot_kind": "contour",
        "levels_locked": _levels_locked_state(),
    }
    if callable(on_meta):
        _safe_callback(on_meta, meta)

    for i, frame in enumerate(frames):
        _apply_existing_animation_state(frame, kwargs)
        _draw_frame(frame, kwargs)
        if callable(on_frame):
            evt = {
                "session_id": session_id,
                "frame_index": i,
                "frame_value": _frame_value(frame),
                "canvas_ready": True,
                "timestamp": time.time(),
            }
            _safe_callback(on_frame, evt)

    return _finish_animation()
```

### Where this helps xconv2

- Worker receives deterministic notifications from cf-plot without re-implementing contour internals.
- Worker can capture current Matplotlib canvas immediately after each callback and emit ANIM_FRAME.
- GUI remains decoupled from contour logic and only handles transport/playback.

## xconv2 worker protocol extension

Current static messages IMG_READY and STATUS remain unchanged.
Add these animation messages:

- ANIM_START:{base64_pickle(dict)}
- ANIM_FRAME:{base64_pickle(dict)}
- ANIM_END:{base64_pickle(dict)}
- ANIM_ERROR:{base64_pickle(dict)}

### ANIM_START payload

- request_id: str
- session_id: str
- total_frames: int | None
- fps_hint: float | None
- title_template: str | None
- started_at: float

### ANIM_FRAME payload

- request_id: str
- session_id: str
- frame_index: int
- total_frames: int | None
- png_bytes: bytes
- frame_value_label: str | None
- emitted_at: float

### ANIM_END payload

- request_id: str
- session_id: str
- frames_emitted: int
- completed_at: float

### ANIM_ERROR payload

- request_id: str
- session_id: str
- frame_index: int | None
- error: str
- failed_at: float

## Worker integration sketch

1. Generate request_id in GUI when sending animation task.
2. Worker sends ANIM_START when first meta callback arrives.
3. Worker handles each frame callback by:
   - grabbing current Matplotlib figure as PNG bytes,
   - sending ANIM_FRAME payload.
4. Worker sends ANIM_END when contour call completes.
5. Worker still sends STATUS:Task Complete at task end.

Important:

- Keep static plot path untouched.
- Animation path should be opt-in via task header (for example #ANIMATION:1).

## GUI behavior sketch

- On ANIM_START: create animation session state keyed by request_id.
- On ANIM_FRAME: decode png_bytes, append or display latest frame.
- Playback policy v1: live preview only (show newest frame immediately).
- Playback policy v2: buffered playback with QTimer and user controls.
- On ANIM_END: mark session complete and stop loading state.
- On new plot request: cancel or ignore stale request_id sessions.

## GUI playback lifecycle (replay, stop, save)

Yes, this architecture can support replay, stop, and save on the GUI side, but only if we keep frame data after first render.

### Session model

Each animation request should have a GUI session object keyed by request_id with:

- status: streaming | complete | stopped | cancelled | failed
- frames: list[QImage | bytes]
- fps: float
- current_index: int
- total_frames: int | None
- output_path: str | None  # optional persisted movie or frame directory

### Required GUI actions

- Play: advance current_index using QTimer and show next cached frame.
- Pause: stop timer only; preserve current_index.
- Stop: stop timer and reset current_index to 0.
- Replay: set current_index to 0 and restart timer.
- Seek: jump to a specific frame index if buffered.
- Save: write buffered frames to movie file or save/copy existing worker artifact.

### Two storage modes

1. Memory-buffered mode
    - GUI stores incoming frame images in memory.
    - Enables immediate replay/seek without worker round-trip.
    - Needed for smooth interactive controls.

2. Artifact-backed mode
    - Worker writes frames/movie to disk (existing compatible workflow).
    - GUI keeps metadata and optional keyframes in memory.
    - Replay can be done by reading artifact when needed.

Recommended default:

- Use memory-buffered mode for short/medium animations.
- Auto-fallback to artifact-backed mode when frame count or memory exceeds threshold.

### Save behavior options

- Save as movie: encode buffered frames to mp4/gif from GUI, or request worker encode.
- Save frames directory: write cached PNG frames with stable ordering.
- Save by reference: if worker already created an artifact, offer copy/export instead of re-encode.

### Protocol notes needed for these controls

Add optional ANIM_END fields:

- artifact_kind: none | frames_dir | gif | mp4
- artifact_path: str | None

This lets GUI present replay/save choices even when not all frames are kept in memory.

### Cancellation and re-run behavior

- New plot request should mark prior session as cancelled and ignore late frames by request_id.
- Stop should be local playback stop only.
- Cancel should optionally send worker cancel signal for active render (future enhancement).

## Backpressure and sizing guidance

- Start with max frame rate cap in worker emission (for example 8-12 fps preview).
- If frame production is faster, drop intermediate frames but keep last frame.
- Keep PNG first for fidelity; optionally add JPEG preview mode later.

## Rollout plan

1. Implement cf-plot callbacks in bnlawrence fork branch.
2. Add worker ANIM_* emit helpers and routing in xconv2.
3. Add GUI session state and live preview.
4. Add tests:
   - router tests for ANIM_START/ANIM_FRAME/ANIM_END/ANIM_ERROR,
   - worker integration test for multi-frame order,
   - GUI test that first frame displays before task completion.
5. Keep IMG_READY path as backward-compatible fallback.

## Open decisions

1. Should request_id be generated by GUI only, or also by worker fallback?
2. Do we need total_frames mandatory, or can None be first-class for streaming unknown length?
3. Should v1 include pause/seek controls, or live preview only?

