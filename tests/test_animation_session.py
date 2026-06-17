"""Tests for animation session controller."""

from __future__ import annotations

import pytest

from xconv2.animation_session import (
    AnimationPlaybackState,
    AnimationSession,
    AnimationSessionController,
)


class TestAnimationSession:
    """Tests for AnimationSession state machine."""

    def test_create_session(self) -> None:
        session = AnimationSession(request_id="req-123", session_id="sess-123")

        assert session.request_id == "req-123"
        assert session.session_id == "sess-123"
        assert session.playback_state == AnimationPlaybackState.IDLE
        assert session.frame_count() == 0

    def test_add_frame_to_idle_session(self) -> None:
        session = AnimationSession(request_id="req-123")
        frame_bytes = b"PNG\x89\x01\x02"

        session.add_frame(frame_bytes)

        assert session.frame_count() == 1
        assert session.playback_state == AnimationPlaybackState.STREAMING
        assert session.get_frame(0) == frame_bytes

    def test_add_multiple_frames(self) -> None:
        session = AnimationSession(request_id="req-123")
        frames = [b"frame1", b"frame2", b"frame3"]

        for frame in frames:
            session.add_frame(frame)

        assert session.frame_count() == 3
        assert session.get_frame(0) == b"frame1"
        assert session.get_frame(1) == b"frame2"
        assert session.get_frame(2) == b"frame3"

    def test_get_frame_out_of_bounds(self) -> None:
        session = AnimationSession(request_id="req-123")
        session.add_frame(b"frame1")

        assert session.get_frame(0) == b"frame1"
        assert session.get_frame(1) is None
        assert session.get_frame(-1) is None

    def test_mark_started(self) -> None:
        session = AnimationSession(request_id="req-123")

        session.mark_started(total_frames=10, fps_hint=12.0, title_template="{title} [{frame}]")

        assert session.total_frames == 10
        assert session.fps_hint == 12.0
        assert session.title_template == "{title} [{frame}]"
        assert session.playback_state == AnimationPlaybackState.STREAMING

    def test_mark_completed(self) -> None:
        session = AnimationSession(request_id="req-123")
        session.add_frame(b"frame1")

        session.mark_completed()

        assert session.playback_state == AnimationPlaybackState.BUFFERED

    def test_mark_failed(self) -> None:
        session = AnimationSession(request_id="req-123")

        session.mark_failed("Contour rendering error")

        assert session.playback_state == AnimationPlaybackState.FAILED
        assert session.error_message == "Contour rendering error"

    def test_add_frame_to_failed_session_ignored(self) -> None:
        session = AnimationSession(request_id="req-123")
        session.mark_failed("Error")

        session.add_frame(b"frame1")

        assert session.frame_count() == 0

    def test_playback_start_stop_cycle(self) -> None:
        session = AnimationSession(request_id="req-123")
        session.add_frame(b"frame1")
        session.add_frame(b"frame2")
        session.mark_completed()

        session.start_playback()
        assert session.playback_state == AnimationPlaybackState.PLAYING
        assert session.current_frame_index == 0

        session.pause_playback()
        assert session.playback_state == AnimationPlaybackState.PAUSED

        session.resume_playback()
        assert session.playback_state == AnimationPlaybackState.PLAYING

        session.stop_playback()
        assert session.playback_state == AnimationPlaybackState.STOPPED
        assert session.current_frame_index == 0

    def test_next_frame_advances_index(self) -> None:
        session = AnimationSession(request_id="req-123")
        frames = [b"f1", b"f2", b"f3"]
        for f in frames:
            session.add_frame(f)

        assert session.next_frame() == b"f1"
        assert session.current_frame_index == 1

        assert session.next_frame() == b"f2"
        assert session.current_frame_index == 2

        assert session.next_frame() == b"f3"
        assert session.current_frame_index == 3

        assert session.next_frame() is None
        assert session.current_frame_index == 3

    def test_seek_to_frame(self) -> None:
        session = AnimationSession(request_id="req-123")
        frames = [b"f0", b"f1", b"f2"]
        for f in frames:
            session.add_frame(f)

        assert session.seek_to_frame(1) == b"f1"
        assert session.current_frame_index == 1

        assert session.seek_to_frame(2) == b"f2"
        assert session.current_frame_index == 2

        assert session.seek_to_frame(10) is None
        assert session.current_frame_index == 2

    def test_reset_playback(self) -> None:
        session = AnimationSession(request_id="req-123")
        session.add_frame(b"f1")
        session.add_frame(b"f2")

        session.current_frame_index = 5
        session.reset_playback()

        assert session.current_frame_index == 0


class TestAnimationSessionController:
    """Tests for AnimationSessionController state management."""

    def test_create_session(self) -> None:
        controller = AnimationSessionController()

        session = controller.create_session("req-123", "sess-456")

        assert session.request_id == "req-123"
        assert session.session_id == "sess-456"

    def test_get_session(self) -> None:
        controller = AnimationSessionController()
        created = controller.create_session("req-123")

        retrieved = controller.get_session("req-123")

        assert retrieved is created
        assert retrieved.request_id == "req-123"

    def test_get_nonexistent_session(self) -> None:
        controller = AnimationSessionController()

        session = controller.get_session("nonexistent")

        assert session is None

    def test_list_sessions(self) -> None:
        controller = AnimationSessionController()
        s1 = controller.create_session("req-1")
        s2 = controller.create_session("req-2")

        sessions = controller.list_sessions()

        assert len(sessions) == 2
        assert s1 in sessions
        assert s2 in sessions

    def test_end_session(self) -> None:
        controller = AnimationSessionController()
        session = controller.create_session("req-123")

        controller.end_session("req-123")

        assert controller.get_session("req-123") is None

    def test_cancel_session(self) -> None:
        controller = AnimationSessionController()
        session = controller.create_session("req-123")

        controller.cancel_session("req-123")

        assert session.playback_state == AnimationPlaybackState.FAILED
        assert "cancelled" in session.error_message.lower()

    def test_multiple_concurrent_sessions(self) -> None:
        controller = AnimationSessionController()
        s1 = controller.create_session("req-1")
        s2 = controller.create_session("req-2")

        s1.add_frame(b"s1f1")
        s1.add_frame(b"s1f2")

        s2.add_frame(b"s2f1")

        s1.mark_completed()
        s2.mark_failed("Error")

        assert s1.frame_count() == 2
        assert s2.playback_state == AnimationPlaybackState.FAILED
        assert len(controller.list_sessions()) == 2
