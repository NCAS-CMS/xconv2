"""Backend animation session controller for xconv2."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class AnimationPlaybackState(Enum):
    """Playback state for an animation session."""

    IDLE = "idle"
    STREAMING = "streaming"
    BUFFERED = "buffered"
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class AnimationSession:
    """Backend state for a single animation session."""

    request_id: str
    session_id: str | None = None
    total_frames: int | None = None
    fps_hint: float | None = None
    title_template: str | None = None
    frames: list[bytes] = field(default_factory=list)
    playback_state: AnimationPlaybackState = field(default=AnimationPlaybackState.IDLE)
    current_frame_index: int = 0
    error_message: str | None = None
    started_at: float | None = None
    completed_at: float | None = None

    def add_frame(self, png_bytes: bytes) -> None:
        """Append a frame to the buffer."""
        if self.playback_state == AnimationPlaybackState.FAILED:
            logger.warning("Ignoring frame for failed session %s", self.request_id)
            return
        self.frames.append(png_bytes)
        if self.playback_state == AnimationPlaybackState.IDLE:
            self.playback_state = AnimationPlaybackState.STREAMING

    def frame_count(self) -> int:
        """Return number of buffered frames."""
        return len(self.frames)

    def get_frame(self, index: int) -> bytes | None:
        """Get frame at index, or None if out of bounds."""
        if 0 <= index < len(self.frames):
            return self.frames[index]
        return None

    def mark_started(self, total_frames: int | None, fps_hint: float | None, title_template: str | None) -> None:
        """Mark session as started with metadata."""
        self.started_at = time.time()
        self.total_frames = total_frames
        self.fps_hint = fps_hint or 4.0
        self.title_template = title_template
        self.playback_state = AnimationPlaybackState.STREAMING

    def mark_completed(self) -> None:
        """Mark session as completed."""
        if self.playback_state != AnimationPlaybackState.FAILED:
            self.playback_state = AnimationPlaybackState.BUFFERED
        self.completed_at = time.time()
        logger.info(
            "Animation session complete request_id=%s session_id=%s frames=%d",
            self.request_id,
            self.session_id,
            len(self.frames),
        )

    def mark_failed(self, error: str) -> None:
        """Mark session as failed."""
        self.playback_state = AnimationPlaybackState.FAILED
        self.error_message = error
        logger.error("Animation session failed request_id=%s: %s", self.request_id, error)

    def start_playback(self) -> None:
        """Transition from buffered to playing."""
        if self.playback_state == AnimationPlaybackState.BUFFERED:
            self.playback_state = AnimationPlaybackState.PLAYING
            self.current_frame_index = 0
            logger.info("Playback started for request_id=%s", self.request_id)

    def pause_playback(self) -> None:
        """Pause playback."""
        if self.playback_state == AnimationPlaybackState.PLAYING:
            self.playback_state = AnimationPlaybackState.PAUSED
            logger.info("Playback paused for request_id=%s at frame %d", self.request_id, self.current_frame_index)

    def resume_playback(self) -> None:
        """Resume from pause."""
        if self.playback_state == AnimationPlaybackState.PAUSED:
            self.playback_state = AnimationPlaybackState.PLAYING
            logger.info("Playback resumed for request_id=%s", self.request_id)

    def stop_playback(self) -> None:
        """Stop playback and reset to frame 0."""
        if self.playback_state in (AnimationPlaybackState.PLAYING, AnimationPlaybackState.PAUSED):
            self.current_frame_index = 0
            self.playback_state = AnimationPlaybackState.STOPPED
            logger.info("Playback stopped for request_id=%s", self.request_id)

    def next_frame(self) -> bytes | None:
        """Advance to next frame and return its bytes, or None if at end."""
        if self.current_frame_index < len(self.frames):
            frame = self.frames[self.current_frame_index]
            self.current_frame_index += 1
            return frame
        return None

    def seek_to_frame(self, index: int) -> bytes | None:
        """Seek to a specific frame and return its bytes."""
        if 0 <= index < len(self.frames):
            self.current_frame_index = index
            return self.frames[index]
        return None

    def reset_playback(self) -> None:
        """Reset playback to frame 0 without changing state."""
        self.current_frame_index = 0


class AnimationSessionController:
    """Manager for multiple concurrent animation sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, AnimationSession] = {}

    def create_session(self, request_id: str, session_id: str | None = None) -> AnimationSession:
        """Create a new animation session."""
        session = AnimationSession(request_id=request_id, session_id=session_id)
        self._sessions[request_id] = session
        logger.info("Created animation session request_id=%s session_id=%s", request_id, session_id)
        return session

    def get_session(self, request_id: str) -> AnimationSession | None:
        """Retrieve an existing session by request_id."""
        return self._sessions.get(request_id)

    def end_session(self, request_id: str) -> None:
        """Remove a session from tracking."""
        if request_id in self._sessions:
            del self._sessions[request_id]
            logger.info("Ended animation session request_id=%s", request_id)

    def list_sessions(self) -> list[AnimationSession]:
        """Return all active sessions."""
        return list(self._sessions.values())

    def cancel_session(self, request_id: str) -> None:
        """Cancel/abandon a session."""
        session = self._sessions.get(request_id)
        if session:
            session.mark_failed("User cancelled")
            logger.info("Cancelled animation session request_id=%s", request_id)
