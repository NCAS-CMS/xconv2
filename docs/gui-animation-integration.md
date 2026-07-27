# GUI Animation Integration Strategy

## Overview

Animation support integrates with existing xconv2 architecture:
- **Task Invocation**: Mark tasks with `#ANIMATION:1` header to enable animation mode
- **Widget Placement**: `AnimationPlaybackWidget` replaces `plot_frame` in the stacked layout
- **Message Routing**: Worker sends `ANIM_*` messages → `worker_message_router` → GUI handlers
- **State Management**: `AnimationSessionController` tracks playback state and buffers
- **Layout Transitions**: Switch between static plot display ↔ animation playback

---

## 1. Task Invocation & Marking

### Current Task System
Tasks are sent via `main_window._send_worker_task(code)` with optional headers:
```python
def _send_worker_task(
    self,
    code: str,
    save_code_path: str | None = None,
    emit_image: bool = True,
) -> None:
    # Builds headers like:
    # #SAVE_TASK_CODE_PATH_B64:...
    # #EMIT_IMAGE:0
    # code...
    # #END_TASK
```

### Adding Animation Header
Extend `_send_worker_task()` with new `animation_enabled: bool` parameter:
```python
def _send_worker_task(
    self,
    code: str,
    save_code_path: str | None = None,
    emit_image: bool = True,
    animation_enabled: bool = False,  # NEW
) -> None:
    headers: list[str] = []
    if save_code_path:
        encoded_path = base64.b64encode(save_code_path.encode("utf-8")).decode("ascii")
        headers.append(f"#SAVE_TASK_CODE_PATH_B64:{encoded_path}")
    if not emit_image:
        headers.append("#EMIT_IMAGE:0")
    if animation_enabled:  # NEW
        headers.append("#ANIMATION:1")
    
    # ... rest of method
```

### Invoking Animations
Create a new helper method in `main_window.py`:
```python
def _send_animation_task(self, code: str) -> None:
    """Send a task that will emit animation frames."""
    self._send_worker_task(code, emit_image=False, animation_enabled=True)
```

Usage examples:
```python
# From plot button or other trigger:
self._send_animation_task(plot_from_selection(...))

# From keyboard shortcut or menu:
plot_code = build_animation_plot_code(...)
self._send_animation_task(plot_code)
```

---

## 2. Worker-Side: Parsing Animation Header

### Task Header Parsing
Worker's `_extract_task_headers()` already parses headers. Add animation flag:

```python
class TaskHeaders(NamedTuple):
    """..."""
    save_path: str | None
    emit_image: bool
    animation_enabled: bool  # NEW
    task_kind: str | None
    task_payload: dict[str, Any] | None
    code: str

def _extract_task_headers(code: str) -> TaskHeaders:
    """..."""
    save_path: str | None = None
    emit_image = True
    animation_enabled = False  # NEW
    # ... existing header parsing ...
    
    elif header.startswith("#ANIMATION:"):
        animation_enabled = header[len("#ANIMATION:"):] == "1"  # NEW
    
    return TaskHeaders(
        save_path=save_path,
        emit_image=emit_image,
        animation_enabled=animation_enabled,  # NEW
        task_kind=task_kind,
        task_payload=task_payload,
        code=code,
    )
```

### Task Execution
In worker's main execution loop (where code is executed):

```python
def _execute_user_task(code: str, headers: TaskHeaders) -> None:
    """Execute user code with animation callbacks if enabled."""
    
    # If animation is enabled, set up module-level animation state for cf-plot
    if headers.animation_enabled:
        setup_animation_callbacks()
    
    try:
        # Execute user code (which calls cf-plot's con())
        exec(code, worker_globals)
    finally:
        if headers.animation_enabled:
            cleanup_animation_callbacks()
    
    # Always emit final image if requested (unchanged)
    if headers.emit_image:
        _emit_latest_plot_image()

def setup_animation_callbacks() -> None:
    """Store animation session metadata and callbacks for cf-plot to use."""
    # Module-level state in worker.py (persists across cf-plot calls)
    global _animation_session_active
    global _animation_session_id
    
    _animation_session_active = True
    _animation_session_id = str(uuid.uuid4())
    
    # cf-plot will retrieve these via our gopen() wrapper when it detects animation=True
    # Details in cfplot-animation-guidance.md

def cleanup_animation_callbacks() -> None:
    """Clear animation session state."""
    global _animation_session_active
    _animation_session_active = False
```

---

## 3. GUI-Side: Widget Integration

### Step 1: Create AnimationPlaybackWidget
New file: `xconv2/ui/animation_playback_widget.py`

```python
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSlider, QFrame, QProgressBar, QComboBox
)
from xconv2.animation_session import AnimationSessionController, AnimationPlaybackState
from xconv2.animation_protocol import AnimationFrame

class AnimationPlaybackWidget(QWidget):
    """Display animation frames and provide playback controls."""
    
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session_controller = AnimationSessionController()
        self.current_session_id: str | None = None
        
        # Playback timer
        self.play_timer = QTimer()
        self.play_timer.timeout.connect(self._on_frame_timer)
        self.play_fps = 8  # Default preview speed
        
        # Build UI
        self._setup_ui()
    
    def _setup_ui(self) -> None:
        """Create widget layout."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        # Frame display
        self.frame_display = QLabel("Buffering animation...")
        self.frame_display.setAlignment(Qt.AlignCenter)
        self.frame_display.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.frame_display.setMinimumSize(120, 120)
        self.frame_display.setStyleSheet("background-color: #222; color: #888; border: 1px solid #444;")
        main_layout.addWidget(self.frame_display)
        
        # Playback info + controls
        controls_layout = QHBoxLayout()
        
        self.frame_info_label = QLabel("Frame 0/0")
        self.frame_info_label.setMinimumWidth(100)
        controls_layout.addWidget(self.frame_info_label)
        
        # Seek slider (disabled while streaming)
        self.seek_slider = QSlider(Qt.Horizontal)
        self.seek_slider.setRange(0, 100)
        self.seek_slider.setEnabled(False)
        self.seek_slider.sliderMoved.connect(self._on_seek)
        controls_layout.addWidget(self.seek_slider)
        
        # Play/Pause button
        self.play_pause_btn = QPushButton("Play")
        self.play_pause_btn.setMaximumWidth(60)
        self.play_pause_btn.setEnabled(False)
        self.play_pause_btn.clicked.connect(self._on_play_pause)
        controls_layout.addWidget(self.play_pause_btn)
        
        # Stop button
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setMaximumWidth(60)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop)
        controls_layout.addWidget(self.stop_btn)
        
        # FPS selector
        self.fps_combo = QComboBox()
        self.fps_combo.addItems(["4 fps", "6 fps", "8 fps", "12 fps", "15 fps"])
        self.fps_combo.setCurrentIndex(2)  # 8 fps default
        self.fps_combo.currentIndexChanged.connect(self._on_fps_changed)
        controls_layout.addWidget(self.fps_combo)
        
        # Save frame button
        self.save_frame_btn = QPushButton("Save Frame")
        self.save_frame_btn.setMaximumWidth(100)
        self.save_frame_btn.setEnabled(False)
        self.save_frame_btn.clicked.connect(self._on_save_frame)
        controls_layout.addWidget(self.save_frame_btn)
        
        main_layout.addLayout(controls_layout)
        
        # Progress bar
        self.buffer_progress = QProgressBar()
        self.buffer_progress.setRange(0, 100)
        self.buffer_progress.setValue(0)
        self.buffer_progress.setMaximumHeight(6)
        self.buffer_progress.setTextVisible(False)
        main_layout.addWidget(self.buffer_progress)
    
    # --- Message handlers (called by worker_message_router) ---
    
    def handle_animation_start(
        self, 
        request_id: str, 
        session_id: str, 
        total_frames: int, 
        fps_hint: float,
        title_template: str,
    ) -> None:
        """Start buffering a new animation session."""
        session = self.session_controller.create_session(
            request_id, session_id, total_frames, fps_hint
        )
        self.current_session_id = session_id
        self.play_fps = int(fps_hint)
        
        self.frame_display.setPixmap(QPixmap())
        self.frame_display.setText("Buffering animation...")
        self.seek_slider.setEnabled(False)
        self.play_pause_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.save_frame_btn.setEnabled(False)
        self.frame_info_label.setText(f"Buffering... (0/{total_frames})")
        self.buffer_progress.setValue(0)
    
    def handle_animation_frame(
        self, 
        request_id: str, 
        session_id: str, 
        frame_index: int, 
        png_bytes: bytes,
    ) -> None:
        """Add frame to buffer and update display."""
        session = self.session_controller.get_session(session_id)
        if not session:
            return
        
        frame = AnimationFrame(
            request_id=request_id,
            session_id=session_id,
            frame_index=frame_index,
            total_frames=session.total_frames,
            png_bytes=png_bytes,
            frame_value_label="",
            emitted_at=time.time(),
        )
        session.add_frame(frame)
        
        # Display frame
        pixmap = QPixmap()
        if pixmap.loadFromData(png_bytes, "PNG"):
            self.frame_display.setPixmap(pixmap)
        
        # Update progress
        progress = int((frame_index + 1) / session.total_frames * 100)
        self.buffer_progress.setValue(progress)
        self.frame_info_label.setText(f"Buffering... ({frame_index + 1}/{session.total_frames})")
    
    def handle_animation_end(
        self, 
        request_id: str, 
        session_id: str, 
        frames_emitted: int,
    ) -> None:
        """Animation streaming complete, enable playback."""
        session = self.session_controller.get_session(session_id)
        if not session:
            return
        
        session.mark_completed()
        
        # Enable playback controls
        self.seek_slider.setEnabled(True)
        self.seek_slider.setMaximum(frames_emitted - 1)
        self.play_pause_btn.setEnabled(True)
        self.stop_btn.setEnabled(True)
        self.save_frame_btn.setEnabled(True)
        self.frame_info_label.setText(f"Ready: {frames_emitted} frames")
        self.buffer_progress.setValue(100)
    
    def handle_animation_error(
        self, 
        request_id: str, 
        session_id: str, 
        frame_index: int, 
        error_message: str,
    ) -> None:
        """Handle animation error."""
        session = self.session_controller.get_session(session_id)
        if session:
            session.mark_failed(error_message)
        
        self.frame_display.setText(f"Animation error:\n{error_message}")
        self.frame_display.setStyleSheet("background-color: #400; color: #f88; border: 1px solid #f44;")
    
    # --- Playback control handlers ---
    
    def _on_play_pause(self) -> None:
        """Toggle playback."""
        session = self.session_controller.get_session(self.current_session_id)
        if not session:
            return
        
        if session.playback_state == AnimationPlaybackState.PLAYING:
            session.pause_playback()
            self.play_timer.stop()
            self.play_pause_btn.setText("Resume")
        elif session.playback_state == AnimationPlaybackState.PAUSED:
            session.resume_playback()
            self.play_timer.start(int(1000 / self.play_fps))
            self.play_pause_btn.setText("Pause")
        else:  # BUFFERED or COMPLETED
            session.start_playback()
            self.play_timer.start(int(1000 / self.play_fps))
            self.play_pause_btn.setText("Pause")
    
    def _on_stop(self) -> None:
        """Stop playback and reset."""
        session = self.session_controller.get_session(self.current_session_id)
        if session:
            session.stop_playback()
            self.play_timer.stop()
            self.play_pause_btn.setText("Play")
            self.play_pause_btn.setEnabled(True)
            self.seek_slider.setValue(0)
            self._update_display()
    
    def _on_seek(self, value: int) -> None:
        """Seek to frame."""
        session = self.session_controller.get_session(self.current_session_id)
        if session:
            session.seek_to_frame(value)
            self._update_display()
    
    def _on_fps_changed(self, index: int) -> None:
        """Update playback speed."""
        fps_values = [4, 6, 8, 12, 15]
        self.play_fps = fps_values[index]
        
        if self.play_timer.isActive():
            self.play_timer.setInterval(int(1000 / self.play_fps))
    
    def _on_frame_timer(self) -> None:
        """Advance to next frame during playback."""
        session = self.session_controller.get_session(self.current_session_id)
        if not session:
            return
        
        frame = session.next_frame()
        if frame:
            self._update_display()
        else:
            # Playback complete
            self.play_timer.stop()
            self.play_pause_btn.setText("Play")
            self.play_pause_btn.setEnabled(True)
    
    def _on_save_frame(self) -> None:
        """Save current frame as PNG."""
        # TODO: Implement file save dialog
        pass
    
    def _update_display(self) -> None:
        """Update frame display and info label."""
        session = self.session_controller.get_session(self.current_session_id)
        if not session or session.current_frame_index < 0:
            return
        
        frame = session.get_frame(session.current_frame_index)
        if frame and frame.png_bytes:
            pixmap = QPixmap()
            if pixmap.loadFromData(frame.png_bytes, "PNG"):
                self.frame_display.setPixmap(pixmap)
        
        self.frame_info_label.setText(
            f"Frame {session.current_frame_index + 1}/{session.total_frames}"
        )
        self.seek_slider.blockSignals(True)
        self.seek_slider.setValue(session.current_frame_index)
        self.seek_slider.blockSignals(False)
```

### Step 2: Integrate into PlotViewController
Modify `xconv2/ui/plot_view_controller.py`:

```python
# In create_plot_area():

# Add animation widget to stacked layout (alongside existing plot_frame)
from xconv2.ui.animation_playback_widget import AnimationPlaybackWidget

self.host.animation_widget = AnimationPlaybackWidget()

plot_stack = QStackedLayout()
plot_stack.setStackingMode(QStackedLayout.StackAll)
plot_stack.addWidget(self.host.plot_frame)                    # Index 0: static plot
plot_stack.addWidget(self.host.animation_widget)               # Index 1: animation
plot_stack.addWidget(self.host.plot_loading_overlay)           # Index 2: loading

# Switch to static plot by default
plot_stack.setCurrentIndex(0)
```

### Step 3: Wire Message Router to Animation Widget
Modify `xconv2/worker_message_router.py`:

The router already has `_handle_anim_*` methods. Add delegation to animation widget:

```python
def _handle_anim_start(self, payload: dict[str, Any]) -> None:
    """..."""
    # Existing code: decode and validate
    # ...
    
    # NEW: Delegate to animation widget if available
    if hasattr(self.host, "animation_widget"):
        self.host.animation_widget.handle_animation_start(
            request_id, session_id, total_frames, fps_hint, title_template
        )
    
    # Also call host handler if defined (for backwards compat)
    if hasattr(self.host, "_handle_animation_start"):
        self.host._handle_animation_start(...)

# Similar for _handle_anim_frame(), _handle_anim_end(), _handle_anim_error()
```

---

## 4. Layout State Management

### Switching Between Plot Modes

In `main_window.py`:

```python
def _show_static_plot(self) -> None:
    """Display static plot image."""
    self.plot_view_controller.show_plot_stack(0)  # plot_frame

def _show_animation(self) -> None:
    """Display animation playback widget."""
    self.plot_view_controller.show_plot_stack(1)  # animation_widget

def _show_loading(self) -> None:
    """Display loading overlay."""
    self.plot_view_controller.show_plot_stack(2)  # loading_overlay
```

In `plot_view_controller.py`:

```python
def show_plot_stack(self, index: int) -> None:
    """Switch active plot display mode (0=static, 1=animation, 2=loading)."""
    plot_stack = getattr(self.host, "plot_stack", None)
    if plot_stack:
        plot_stack.setCurrentIndex(index)
```

### Task Completion Flow

Current flow (unchanged for non-animation):
```
_send_worker_task(code, emit_image=True)
  → worker executes code
  → worker emits IMG_READY
  → router calls host._handle_plot_image()
  → plot_view_controller.set_plot_image()  # shows static plot
```

Animation flow (new):
```
_send_animation_task(code)
  → worker parses #ANIMATION:1 header
  → worker sets up module-level callback state
  → worker executes code (cf-plot detects animation, calls callbacks)
  → worker emits ANIM_START
  → router calls animation_widget.handle_animation_start()
  → for each frame: worker emits ANIM_FRAME
    → router calls animation_widget.handle_animation_frame()
  → worker emits ANIM_END
    → router calls animation_widget.handle_animation_end()
    → playback controls become enabled
```

---

## 5. Edge Cases & Error Handling

### User Cancels Task During Animation
- Worker receives task cancellation → emits ANIM_ERROR with "cancelled"
- Router calls `animation_widget.handle_animation_error()`
- Animation widget shows error state, buffers partial frames for inspection

### Animation Task with `emit_image=True`
- Worker emits ANIM_* messages during animation
- After completion, also emits final IMG_READY with static plot
- GUI shows animation playback; final IMG_READY is queued/ignored

### Rapid Task Succession
- Each task gets unique request_id
- Router looks up session by session_id, not request_id
- Multiple animations can be buffered (though only one displayed at a time)
- SessionController manages concurrent sessions

### Low-Latency Display
- During STREAMING state, show each frame as it arrives
- Don't wait for all frames before showing first frame
- Allow seeking only into buffered region

---

## 6. Implementation Checklist

### Phase 1: Task Marking & Worker Integration
- [ ] Add `animation_enabled` parameter to `_send_worker_task()`
- [ ] Create `_send_animation_task()` helper method
- [ ] Extend `_extract_task_headers()` to parse `#ANIMATION:1`
- [ ] Add animation flag to `TaskHeaders` namedtuple
- [ ] Handle animation flag in main execution loop (setup/cleanup callbacks)
- [ ] Test: Verify animation header is parsed correctly

### Phase 2: GUI Widget Integration
- [ ] Create `animation_playback_widget.py` with full implementation
- [ ] Add animation widget to `plot_view_controller.create_plot_area()`
- [ ] Wire animation widget message handlers to `worker_message_router`
- [ ] Implement `show_plot_stack()` switching mechanism
- [ ] Test: Manual frame reception and display

### Phase 3: Playback Controls
- [ ] Implement play/pause/stop/seek state machine
- [ ] Test pause/resume transitions
- [ ] Test seek to arbitrary frame during BUFFERED state
- [ ] Verify slider interaction doesn't interfere with playback timer

### Phase 4: Integration Testing
- [ ] End-to-end animation task (from plot button click to playback)
- [ ] Verify backwards compat (non-animation tasks unchanged)
- [ ] Test error handling (task cancellation, cf-plot exceptions)
- [ ] Performance: 8-12 fps sustained playback, frame latency < 50ms

---

## 7. Future Enhancements

- Memory-backed storage mode: Automatic PNG compression for large animations
- Artifact-backed storage: Save to disk if animation exceeds memory threshold
- Frame export: Save animation as sequence or video (ffmpeg)
- Advanced seek UI: Scrubber with thumbnail preview on hover
- Frame rate sync: Adaptive FPS based on frame arrival rate
- Recording: Capture user interactions during playback for reproducibility
