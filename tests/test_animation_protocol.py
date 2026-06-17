"""Tests for animation protocol encoding and decoding."""

from __future__ import annotations

import base64
import pickle
import time

import pytest

from xconv2.animation_protocol import (
    AnimationEnd,
    AnimationError,
    AnimationFrame,
    AnimationMetadata,
    AnimationStart,
    decode_payload,
    encode_payload,
)


class TestAnimationMetadata:
    """Tests for AnimationMetadata serialization."""

    def test_metadata_to_dict(self) -> None:
        meta = AnimationMetadata(
            session_id="sess-123",
            total_frames=10,
            fps_hint=12.0,
            title_template="{title} [{frame}]",
            plot_kind="contour",
            levels_locked=True,
        )

        data = meta.to_dict()

        assert data["session_id"] == "sess-123"
        assert data["total_frames"] == 10
        assert data["fps_hint"] == 12.0
        assert data["title_template"] == "{title} [{frame}]"
        assert data["plot_kind"] == "contour"
        assert data["levels_locked"] is True

    def test_metadata_from_dict(self) -> None:
        data = {
            "session_id": "sess-123",
            "total_frames": 10,
            "fps_hint": 12.0,
            "title_template": "{title} [{frame}]",
            "plot_kind": "contour",
            "levels_locked": True,
        }

        meta = AnimationMetadata.from_dict(data)

        assert meta.session_id == "sess-123"
        assert meta.total_frames == 10
        assert meta.fps_hint == 12.0
        assert meta.title_template == "{title} [{frame}]"
        assert meta.plot_kind == "contour"
        assert meta.levels_locked is True

    def test_metadata_from_dict_partial(self) -> None:
        data = {"session_id": "sess-123"}

        meta = AnimationMetadata.from_dict(data)

        assert meta.session_id == "sess-123"
        assert meta.total_frames is None
        assert meta.fps_hint is None
        assert meta.plot_kind == "contour"
        assert meta.levels_locked is False


class TestAnimationFrame:
    """Tests for AnimationFrame serialization."""

    def test_frame_to_dict(self) -> None:
        now = time.time()
        png_data = b"PNG\x89\x01\x02\x03"

        frame = AnimationFrame(
            request_id="req-123",
            session_id="sess-123",
            frame_index=5,
            total_frames=10,
            png_bytes=png_data,
            frame_value_label="frame 5",
            emitted_at=now,
        )

        data = frame.to_dict()

        assert data["request_id"] == "req-123"
        assert data["session_id"] == "sess-123"
        assert data["frame_index"] == 5
        assert data["total_frames"] == 10
        assert data["png_bytes"] == png_data
        assert data["frame_value_label"] == "frame 5"
        assert data["emitted_at"] == now

    def test_frame_from_dict(self) -> None:
        now = time.time()
        png_data = b"PNG\x89\x01\x02\x03"

        data = {
            "request_id": "req-123",
            "session_id": "sess-123",
            "frame_index": 5,
            "total_frames": 10,
            "png_bytes": png_data,
            "frame_value_label": "frame 5",
            "emitted_at": now,
        }

        frame = AnimationFrame.from_dict(data)

        assert frame.request_id == "req-123"
        assert frame.session_id == "sess-123"
        assert frame.frame_index == 5
        assert frame.total_frames == 10
        assert frame.png_bytes == png_data
        assert frame.frame_value_label == "frame 5"
        assert frame.emitted_at == now


class TestAnimationStart:
    """Tests for AnimationStart serialization."""

    def test_start_to_dict(self) -> None:
        now = time.time()

        start = AnimationStart(
            request_id="req-123",
            session_id="sess-123",
            total_frames=10,
            fps_hint=12.0,
            title_template="{title} [{frame}]",
            started_at=now,
        )

        data = start.to_dict()

        assert data["request_id"] == "req-123"
        assert data["session_id"] == "sess-123"
        assert data["total_frames"] == 10
        assert data["fps_hint"] == 12.0
        assert data["title_template"] == "{title} [{frame}]"
        assert data["started_at"] == now

    def test_start_from_dict(self) -> None:
        now = time.time()

        data = {
            "request_id": "req-123",
            "session_id": "sess-123",
            "total_frames": 10,
            "fps_hint": 12.0,
            "title_template": "{title} [{frame}]",
            "started_at": now,
        }

        start = AnimationStart.from_dict(data)

        assert start.request_id == "req-123"
        assert start.session_id == "sess-123"
        assert start.total_frames == 10
        assert start.fps_hint == 12.0
        assert start.title_template == "{title} [{frame}]"
        assert start.started_at == now


class TestAnimationEnd:
    """Tests for AnimationEnd serialization."""

    def test_end_to_dict(self) -> None:
        now = time.time()

        end = AnimationEnd(
            request_id="req-123",
            session_id="sess-123",
            frames_emitted=10,
            completed_at=now,
        )

        data = end.to_dict()

        assert data["request_id"] == "req-123"
        assert data["session_id"] == "sess-123"
        assert data["frames_emitted"] == 10
        assert data["completed_at"] == now


class TestAnimationError:
    """Tests for AnimationError serialization."""

    def test_error_to_dict(self) -> None:
        now = time.time()

        error = AnimationError(
            request_id="req-123",
            session_id="sess-123",
            frame_index=5,
            error="Contour failed",
            failed_at=now,
        )

        data = error.to_dict()

        assert data["request_id"] == "req-123"
        assert data["session_id"] == "sess-123"
        assert data["frame_index"] == 5
        assert data["error"] == "Contour failed"
        assert data["failed_at"] == now


class TestPayloadEncoding:
    """Tests for payload encoding and decoding."""

    def test_encode_decode_roundtrip(self) -> None:
        payload = {
            "request_id": "req-123",
            "session_id": "sess-123",
            "frame_index": 5,
            "data": "test",
        }

        encoded = encode_payload(payload)

        assert isinstance(encoded, str)
        assert encoded == base64.b64encode(pickle.dumps(payload)).decode("ascii")

        decoded = decode_payload(encoded)

        assert decoded == payload

    def test_encode_with_binary_data(self) -> None:
        payload = {
            "png_bytes": b"PNG\x89\x01\x02\x03",
            "request_id": "req-123",
        }

        encoded = encode_payload(payload)
        decoded = decode_payload(encoded)

        assert decoded["png_bytes"] == b"PNG\x89\x01\x02\x03"
        assert decoded["request_id"] == "req-123"
