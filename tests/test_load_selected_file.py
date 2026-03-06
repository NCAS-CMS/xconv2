from __future__ import annotations

import ast
from dataclasses import dataclass, field

import pytest

from cf_view.cf_templates import coordinate_list
from cf_view.gui import CFVMain


@dataclass
class _DummyStatus:
    messages: list[str] = field(default_factory=list)

    def showMessage(self, message: str) -> None:
        self.messages.append(message)


@dataclass
class _DummyWindow:
    status: _DummyStatus = field(default_factory=_DummyStatus)
    sent_tasks: list[str] = field(default_factory=list)

    def _send_worker_task(self, code: str) -> None:
        self.sent_tasks.append(code)


def test_load_selected_file_builds_worker_task() -> None:
    """The GUI should send a worker task when a file is selected."""
    window = _DummyWindow()
    file_path = "/tmp/mock-data.nc"

    CFVMain._load_selected_file(window, file_path)

    assert window.status.messages[-1] == f"Loading file: {file_path}"
    assert len(window.sent_tasks) == 1

    code = window.sent_tasks[0]
    assert f"cf.read({file_path!r})" in code
    assert "send_to_gui('METADATA', fields)" in code


def test_load_selected_file_task_executes_with_mock_cf_example_fields() -> None:
    """
    The generated worker code should emit field metadata when executed.
    """
    cf = pytest.importorskip("cf")

    window = _DummyWindow()
    file_path = "/tmp/mock-data.nc"
    CFVMain._load_selected_file(window, file_path)
    code = window.sent_tasks[0]

    messages: list[tuple[str, object]] = []

    class _FakeCF:
        @staticmethod
        def read(_path: str):
            return cf.example_fields(0, 1, 2, 3, 4, 5, 6, 7)

    namespace = {
        "cf": _FakeCF,
        "send_to_gui": lambda prefix, payload: messages.append((prefix, payload)),
    }

    exec(code, namespace)

    # expecting
    # [
    # ('METADATA', ['specific_humidity(latitude(5), longitude(8)) 1', 
    # 'air_temperature(atmosphere_hybrid_height_coordinate(1), grid_latitude(10), grid_longitude(9)) K', 
    # 'air_potential_temperature(time(36), latitude(5), longitude(8)) K', 
    # 'precipitation_flux(cf_role=timeseries_id(4), ncdim%timeseries(9)) kg m-2 day-1', 
    # 'air_temperature(cf_role=timeseries_id(3), ncdim%timeseries(26), ncdim%profile_1(4)) K', 
    # 'air_potential_temperature(time(118), latitude(5), longitude(8)) K', 
    # 'precipitation_amount(cf_role=timeseries_id(2), time(4))', 
    # 'eastward_wind(time(3), air_pressure(1), grid_latitude(4), grid_longitude(5)) m s-1'])
    #]

    prefix, payload = messages[-1]
    assert prefix == "METADATA"
    assert isinstance(payload, list)
    assert len(payload) == 8
    assert all(isinstance(item, str) for item in payload)

    parts = payload[0].split("\x1f", 2)
    assert len(parts) == 3
    assert parts[0].startswith("specific_humidity")
    assert "specific_humidity" in parts[1]

    properties = ast.literal_eval(parts[2])
    assert isinstance(properties, dict)


def test_coordinate_list_emits_coordinates_for_example_field() -> None:
    """Coordinate template should emit a non-empty coordinate payload for a sample field."""
    cf = pytest.importorskip("cf")

    messages: list[tuple[str, object]] = []
    namespace = {
        "cf": cf,
        "send_to_gui": lambda prefix, payload: messages.append((prefix, payload)),
    }

    code = "f = cf.example_fields(0, 1, 2, 3, 4, 5, 6, 7)\n" + coordinate_list(0)
    exec(code, namespace)

    prefix, payload = messages[-1]
    assert prefix == "COORD"
    assert isinstance(payload, list)
    assert payload
    assert isinstance(payload[0], tuple)
    assert len(payload[0]) == 2
    assert payload[0][0].startswith("latitude")
    assert isinstance(payload[0][1], list)      
 