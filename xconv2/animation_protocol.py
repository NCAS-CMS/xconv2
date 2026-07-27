"""Animation protocol types and message helpers for xconv2 streaming animation."""

from __future__ import annotations

import base64
import json
import pickle
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class AnimationMetadata:
    """Metadata for an animation session."""

    session_id: str | None
    total_frames: int | None
    fps_hint: float | None
    title_template: str | None
    plot_kind: str
    levels_locked: bool

    def to_dict(self) -> dict[str, Any]:
        """Convert to payload dict."""
        return {
            "session_id": self.session_id,
            "total_frames": self.total_frames,
            "fps_hint": self.fps_hint,
            "title_template": self.title_template,
            "plot_kind": self.plot_kind,
            "levels_locked": self.levels_locked,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnimationMetadata:
        """Construct from payload dict."""
        return cls(
            session_id=data.get("session_id"),
            total_frames=data.get("total_frames"),
            fps_hint=data.get("fps_hint"),
            title_template=data.get("title_template"),
            plot_kind=str(data.get("plot_kind", "contour")),
            levels_locked=bool(data.get("levels_locked", False)),
        )


@dataclass
class AnimationFrame:
    """A single frame in an animation stream."""

    request_id: str
    session_id: str | None
    frame_index: int
    total_frames: int | None
    png_bytes: bytes
    frame_value_label: str | None
    emitted_at: float

    def to_dict(self) -> dict[str, Any]:
        """Convert to payload dict (bytes included as-is)."""
        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "frame_index": self.frame_index,
            "total_frames": self.total_frames,
            "png_bytes": self.png_bytes,
            "frame_value_label": self.frame_value_label,
            "emitted_at": self.emitted_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnimationFrame:
        """Construct from payload dict."""
        return cls(
            request_id=str(data.get("request_id", "")),
            session_id=data.get("session_id"),
            frame_index=int(data.get("frame_index", 0)),
            total_frames=data.get("total_frames"),
            png_bytes=bytes(data.get("png_bytes", b"")),
            frame_value_label=data.get("frame_value_label"),
            emitted_at=float(data.get("emitted_at", time.time())),
        )


@dataclass
class AnimationStart:
    """Animation session start notification."""

    request_id: str
    session_id: str | None
    total_frames: int | None
    fps_hint: float | None
    title_template: str | None
    started_at: float

    def to_dict(self) -> dict[str, Any]:
        """Convert to payload dict."""
        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "total_frames": self.total_frames,
            "fps_hint": self.fps_hint,
            "title_template": self.title_template,
            "started_at": self.started_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnimationStart:
        """Construct from payload dict."""
        return cls(
            request_id=str(data.get("request_id", "")),
            session_id=data.get("session_id"),
            total_frames=data.get("total_frames"),
            fps_hint=data.get("fps_hint"),
            title_template=data.get("title_template"),
            started_at=float(data.get("started_at", time.time())),
        )


@dataclass
class AnimationEnd:
    """Animation session completion notification."""

    request_id: str
    session_id: str | None
    frames_emitted: int
    completed_at: float

    def to_dict(self) -> dict[str, Any]:
        """Convert to payload dict."""
        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "frames_emitted": self.frames_emitted,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnimationEnd:
        """Construct from payload dict."""
        return cls(
            request_id=str(data.get("request_id", "")),
            session_id=data.get("session_id"),
            frames_emitted=int(data.get("frames_emitted", 0)),
            completed_at=float(data.get("completed_at", time.time())),
        )


@dataclass
class AnimationError:
    """Animation session error notification."""

    request_id: str
    session_id: str | None
    frame_index: int | None
    error: str
    failed_at: float

    def to_dict(self) -> dict[str, Any]:
        """Convert to payload dict."""
        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "frame_index": self.frame_index,
            "error": self.error,
            "failed_at": self.failed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnimationError:
        """Construct from payload dict."""
        return cls(
            request_id=str(data.get("request_id", "")),
            session_id=data.get("session_id"),
            frame_index=data.get("frame_index"),
            error=str(data.get("error", "")),
            failed_at=float(data.get("failed_at", time.time())),
        )


def encode_payload(payload: dict[str, Any]) -> str:
    """Encode a payload dict as base64(pickle) for stdout transmission."""
    return base64.b64encode(pickle.dumps(payload)).decode("ascii")


def decode_payload(encoded: str) -> dict[str, Any]:
    """Decode a base64(pickle) payload from stdout."""
    return pickle.loads(base64.b64decode(encoded.encode("ascii")))
