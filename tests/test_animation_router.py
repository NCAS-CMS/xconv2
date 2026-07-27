"""Tests for animation message routing in worker_message_router."""

from __future__ import annotations

import base64
import pickle
from dataclasses import dataclass, field
from typing import Any

import pytest

from xconv2.worker_message_router import WorkerMessageRouter


@dataclass
class _DummyAnimationHost:
    """Mock host for testing animation router handlers."""

    received_anim_starts: list[dict[str, Any]] = field(default_factory=list)
    received_anim_frames: list[dict[str, Any]] = field(default_factory=list)
    received_anim_ends: list[dict[str, Any]] = field(default_factory=list)
    received_anim_errors: list[dict[str, Any]] = field(default_factory=list)

    class _DummyWorker:
        def processId(self) -> int:
            return 1234

    worker = _DummyWorker()

    def _handle_animation_start(
        self, request_id: str, session_id: str | None, total_frames: int | None, fps_hint: float | None, title_template: str | None
    ) -> None:
        self.received_anim_starts.append(
            {
                "request_id": request_id,
                "session_id": session_id,
                "total_frames": total_frames,
                "fps_hint": fps_hint,
                "title_template": title_template,
            }
        )

    def _handle_animation_frame(
        self, request_id: str, session_id: str | None, frame_index: int, png_bytes: bytes
    ) -> None:
        self.received_anim_frames.append(
            {
                "request_id": request_id,
                "session_id": session_id,
                "frame_index": frame_index,
                "png_bytes": png_bytes,
            }
        )

    def _handle_animation_end(self, request_id: str, session_id: str | None, frames_emitted: int) -> None:
        self.received_anim_ends.append(
            {
                "request_id": request_id,
                "session_id": session_id,
                "frames_emitted": frames_emitted,
            }
        )

    def _handle_animation_error(
        self, request_id: str, session_id: str | None, frame_index: int | None, error_message: str
    ) -> None:
        self.received_anim_errors.append(
            {
                "request_id": request_id,
                "session_id": session_id,
                "frame_index": frame_index,
                "error_message": error_message,
            }
        )


class TestAnimationMessageRouting:
    """Tests for animation message handling in WorkerMessageRouter."""

    def test_route_anim_start(self) -> None:
        host = _DummyAnimationHost()
        router = WorkerMessageRouter(host)

        payload = {
            "request_id": "req-123",
            "session_id": "sess-456",
            "total_frames": 10,
            "fps_hint": 12.0,
            "title_template": "{title} [{frame}]",
            "started_at": 1234567890.0,
        }
        encoded = base64.b64encode(pickle.dumps(payload)).decode("ascii")

        router.handle_line(f"ANIM_START:{encoded}")

        assert len(host.received_anim_starts) == 1
        assert host.received_anim_starts[0]["request_id"] == "req-123"
        assert host.received_anim_starts[0]["session_id"] == "sess-456"
        assert host.received_anim_starts[0]["total_frames"] == 10
        assert host.received_anim_starts[0]["fps_hint"] == 12.0
        assert host.received_anim_starts[0]["title_template"] == "{title} [{frame}]"

    def test_route_anim_frame(self) -> None:
        host = _DummyAnimationHost()
        router = WorkerMessageRouter(host)

        png_data = b"PNG\x89\x01\x02\x03"
        payload = {
            "request_id": "req-123",
            "session_id": "sess-456",
            "frame_index": 5,
            "total_frames": 10,
            "png_bytes": png_data,
            "frame_value_label": "frame 5",
            "emitted_at": 1234567890.0,
        }
        encoded = base64.b64encode(pickle.dumps(payload)).decode("ascii")

        router.handle_line(f"ANIM_FRAME:{encoded}")

        assert len(host.received_anim_frames) == 1
        assert host.received_anim_frames[0]["request_id"] == "req-123"
        assert host.received_anim_frames[0]["session_id"] == "sess-456"
        assert host.received_anim_frames[0]["frame_index"] == 5
        assert host.received_anim_frames[0]["png_bytes"] == png_data

    def test_route_anim_end(self) -> None:
        host = _DummyAnimationHost()
        router = WorkerMessageRouter(host)

        payload = {
            "request_id": "req-123",
            "session_id": "sess-456",
            "frames_emitted": 10,
            "completed_at": 1234567890.0,
        }
        encoded = base64.b64encode(pickle.dumps(payload)).decode("ascii")

        router.handle_line(f"ANIM_END:{encoded}")

        assert len(host.received_anim_ends) == 1
        assert host.received_anim_ends[0]["request_id"] == "req-123"
        assert host.received_anim_ends[0]["session_id"] == "sess-456"
        assert host.received_anim_ends[0]["frames_emitted"] == 10

    def test_route_anim_error(self) -> None:
        host = _DummyAnimationHost()
        router = WorkerMessageRouter(host)

        payload = {
            "request_id": "req-123",
            "session_id": "sess-456",
            "frame_index": 5,
            "error": "Contour rendering failed",
            "failed_at": 1234567890.0,
        }
        encoded = base64.b64encode(pickle.dumps(payload)).decode("ascii")

        router.handle_line(f"ANIM_ERROR:{encoded}")

        assert len(host.received_anim_errors) == 1
        assert host.received_anim_errors[0]["request_id"] == "req-123"
        assert host.received_anim_errors[0]["session_id"] == "sess-456"
        assert host.received_anim_errors[0]["frame_index"] == 5
        assert host.received_anim_errors[0]["error_message"] == "Contour rendering failed"

    def test_multiple_frames_in_sequence(self) -> None:
        host = _DummyAnimationHost()
        router = WorkerMessageRouter(host)

        for i in range(3):
            payload = {
                "request_id": "req-123",
                "session_id": "sess-456",
                "frame_index": i,
                "total_frames": 3,
                "png_bytes": f"frame{i}".encode(),
                "frame_value_label": f"frame {i}",
                "emitted_at": 1234567890.0 + i,
            }
            encoded = base64.b64encode(pickle.dumps(payload)).decode("ascii")
            router.handle_line(f"ANIM_FRAME:{encoded}")

        assert len(host.received_anim_frames) == 3
        for i in range(3):
            assert host.received_anim_frames[i]["frame_index"] == i

    def test_handler_not_called_if_not_available(self) -> None:
        """Verify router handles missing handlers gracefully."""

        @dataclass
        class _DummyHostNoAnimationHandlers:
            class _DummyWorker:
                def processId(self) -> int:
                    return 1234

            worker = _DummyWorker()

        host = _DummyHostNoAnimationHandlers()
        router = WorkerMessageRouter(host)

        payload = {"request_id": "req-123"}
        encoded = base64.b64encode(pickle.dumps(payload)).decode("ascii")

        # Should not raise even though handlers don't exist
        router.handle_line(f"ANIM_START:{encoded}")
        router.handle_line(f"ANIM_FRAME:{encoded}")
        router.handle_line(f"ANIM_END:{encoded}")
        router.handle_line(f"ANIM_ERROR:{encoded}")
