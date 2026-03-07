from __future__ import annotations

import base64
import pickle
from dataclasses import dataclass, field

from xconv2.main_window import CFVMain


@dataclass
class _DummyMain:
    built_slider_payloads: list[dict[str, list[object]]] = field(default_factory=list)

    def build_dynamic_sliders(self, metadata: dict[str, list[object]]) -> None:
        self.built_slider_payloads.append(metadata)


@dataclass
class _FakeLine:
    text: str

    def data(self) -> bytes:
        return self.text.encode()


class _FakeWorker:
    def __init__(self, lines: list[str]) -> None:
        self._lines = [_FakeLine(line) for line in lines]

    def canReadLine(self) -> bool:
        return bool(self._lines)

    def readLine(self) -> _FakeLine:
        return self._lines.pop(0)


def test_normalize_coordinate_metadata_filters_and_coerces() -> None:
    payload = [
        ("time", ["1850-01-16", "1850-02-16"]),
        ("lat", ("-90", "0", "90")),
        ("empty", []),
        ("none", None),
        "bad-entry",
        ("too-short",),
    ]

    normalized = CFVMain._normalize_coordinate_metadata(None, payload)

    assert normalized == {
        "time": ["1850-01-16", "1850-02-16"],
        "lat": ["-90", "0", "90"],
    }


def test_handle_worker_output_coord_routes_to_slider_builder() -> None:
    coord_payload = [("time", ["1850-01-16", "1850-02-16"]), ("lat", ["-90", "0", "90"])]
    encoded = base64.b64encode(pickle.dumps(coord_payload)).decode()
    line = f"COORD:{encoded}\n"

    dummy = _DummyMain()
    dummy._normalize_coordinate_metadata = lambda payload: CFVMain._normalize_coordinate_metadata(None, payload)
    dummy.worker = _FakeWorker([line])

    CFVMain.handle_worker_output(dummy)

    assert len(dummy.built_slider_payloads) == 1
    assert dummy.built_slider_payloads[0] == {
        "time": ["1850-01-16", "1850-02-16"],
        "lat": ["-90", "0", "90"],
    }
