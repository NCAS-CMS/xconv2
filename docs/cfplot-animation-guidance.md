# cf-plot Animation Hook Guidance

This document is a cf-plot focused implementation guide only. It is intended to be copied into the cf-plot repository and used to drive the callback-hook work needed by xconv2 streaming playback.

## Scope

In scope:

- Add callback hooks to cf-plot contour animation path.
- Preserve existing animation behavior and output semantics.
- Keep existing frame-writing workflows for external tools unchanged.

Out of scope:

- xconv2 worker protocol and GUI playback implementation.
- Reworking contour level logic, map logic, or animation semantics.

## Non-negotiable compatibility requirements

1. Existing behavior remains default.
- If no new callback kwargs are provided, behavior and output must match current behavior.

2. Existing frame-on-disk workflows remain intact.
- Current options that write frame sequences for downstream tools must continue to do so.
- New callbacks are additive and cannot disable, reorder, or replace frame file writing.

3. Existing animation kwargs keep current meaning.
- animation
- animation_reference
- reuse_map_background
- clear_previous_frame
- animation_axis
- animation_title_template

## Proposed public API additions

Add these optional kwargs to `cfp.gopen()` (not con):

- animation_session_id: str | None = None
- animation_meta_callback: Callable[[dict[str, object]], None] | None = None
- animation_frame_callback: Callable[[dict[str, object]], None] | None = None

No changes to `cfp.con()` kwargs are required for v1.

Rationale:
- gopen is where animation output behavior is configured.
- con remains focused on plot semantics only.
- Callbacks persist in gopen state across multiple con calls in the animation loop.

## Callback contracts

### Meta callback

Called once, before the first frame callback.

Suggested payload:

- session_id: str | None
- total_frames: int | None
- fps_hint: float | None
- title_template: str | None
- plot_kind: str  # contour
- levels_locked: bool

### Frame callback

Called after each frame has been fully drawn.

Suggested payload:

- session_id: str | None
- frame_index: int  # 0-based
- frame_value: object | None
- canvas_ready: bool  # always true in callback
- timestamp: float

## Where to wire callbacks in cf-plot

Callbacks are stored during gopen and retrieved during con animation.

### gopen change

Store callbacks in module state for later access:

```python
def gopen(file, **kwargs):
    session_id = kwargs.get("animation_session_id")
    on_meta = kwargs.get("animation_meta_callback")
    on_frame = kwargs.get("animation_frame_callback")
    
    # Store in module state to be used by con()
    _ANIMATION_SESSION = {"session_id": session_id, "on_meta": on_meta, "on_frame": on_frame}
    # ... existing gopen logic
```

### con changes

Retrieve and use callbacks during animation:

```python
def con(field, **kwargs):
    animation = bool(kwargs.get("animation", False))
    
    if not animation:
        return _existing_static_con(field, **kwargs)
    
    # Retrieve callbacks from gopen state
    animation_ctx = _ANIMATION_SESSION or {}
    session_id = animation_ctx.get("session_id")
    on_meta = animation_ctx.get("on_meta")
    on_frame = animation_ctx.get("on_frame")
    
    # ... rest of animation loop with callbacks
```

## Pseudocode reference

```python
from collections.abc import Callable
import logging
import time

logger = logging.getLogger(__name__)

# Module-level state to pass callbacks from gopen to con
_ANIMATION_SESSION = None


def _safe_callback(cb: Callable[[dict[str, object]], None] | None, payload: dict[str, object]) -> None:
    if not callable(cb):
        return
    try:
        cb(payload)
    except Exception:
        logger.exception("animation callback failed")


def gopen(file, **kwargs):
    global _ANIMATION_SESSION
    _ANIMATION_SESSION = {
        "session_id": kwargs.get("animation_session_id"),
        "on_meta": kwargs.get("animation_meta_callback"),
        "on_frame": kwargs.get("animation_frame_callback"),
    }
    # ... existing gopen behavior unchanged


def con(field, **kwargs):
    global _ANIMATION_SESSION
    animation = bool(kwargs.get("animation", False))
    
    if not animation:
        return _existing_static_con(field, **kwargs)

    animation_ctx = _ANIMATION_SESSION or {}
    session_id = animation_ctx.get("session_id")
    on_meta = animation_ctx.get("on_meta")
    on_frame = animation_ctx.get("on_frame")

    frames = _existing_resolve_animation_frames(field, kwargs)

    _safe_callback(
        on_meta,
        {
            "session_id": session_id,
            "total_frames": _safe_len_or_none(frames),
            "fps_hint": None,
            "title_template": kwargs.get("animation_title_template"),
            "plot_kind": "contour",
            "levels_locked": _existing_levels_locked_state(),
        },
    )

    for i, frame in enumerate(frames):
        _existing_apply_animation_state(frame, kwargs)
        _existing_draw_frame(frame, kwargs)
        _existing_write_frame_if_configured(frame, kwargs)
        _safe_callback(
            on_frame,
            {
                "session_id": session_id,
                "frame_index": i,
                "frame_value": _existing_frame_value(frame),
                "canvas_ready": True,
                "timestamp": time.time(),
            },
        )

    return _existing_finalize_animation()
```

## Recommended implementation steps

1. Add module-level state variable to hold animation callback context.
2. Add gopen kwargs for session_id and callbacks; store them in module state.
3. Update con to retrieve callbacks from module state at animation start.
4. Add internal _safe_callback helper.
5. Wire meta callback before first frame draw in animation loop.
6. Wire frame callback after draw and after any existing frame-file write.
7. Add logging around callback invocation points.
8. Add docs/changelog entry marking API as additive and backward-compatible.

## Suggested tests in cf-plot

1. Backward compatibility: no callbacks
   - Existing animation output (including frame files, when configured) remains unchanged.

2. Meta callback invocation
   - Called once per animation sequence started via gopen.
   - Called before first frame callback.

3. Frame callback invocation
   - Called once per rendered frame.
   - frame_index monotonic from 0 to N-1.

4. Ordering with frame writes
   - If frame files are enabled, write occurs before frame callback.

5. Callback exception resilience
   - If callback raises, animation continues and output remains valid.

6. Existing kwargs interactions
   - Test combinations of reuse_map_background, clear_previous_frame, animation_axis, and title template with callbacks enabled.

7. gopen state persistence
   - Callbacks stored in gopen should be retrievable in subsequent con calls without re-registering.

## Integration note for xconv2

xconv2 worker will:

1. Generate a session_id for this animation request.
2. Call cfp.gopen with animation_session_id, animation_meta_callback, and animation_frame_callback.
3. Call cfp.con with animation=True and existing animation kwargs.
4. In each callback, capture the active Matplotlib canvas and stream frames to the GUI.

This design preserves native cf-plot animation semantics while allowing xconv2 to implement streaming playback.
