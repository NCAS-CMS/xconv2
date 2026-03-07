from __future__ import annotations

import pytest

from xconv2.xconv_cf_interface import (
    coordinate_info,
    field_info,
    get_data_for_plotting,
    run_contour_plot,
)

cf = pytest.importorskip("cf")


class _MockCellMeasures:
    def __call__(self) -> str:
        return "cell_measures: area: areacella"


class _MockField:
    shape = (2, 3)

    def identity(self) -> str:
        return "air_temperature"

    def properties(self) -> dict[str, str]:
        return {"units": "K", "standard_name": "air_temperature"}

    def coordinates(self) -> dict[str, str]:
        return {
            "dimensioncoordinate0": "Dimension coordinate: latitude(2) degrees_north",
            "dimensioncoordinate1": "Dimension coordinate: longitude(3) degrees_east",
        }

    def cell_methods(self) -> str:
        return ""

    def cell_measures(self) -> str:
        return _MockCellMeasures()()


def test_field_info_returns_serialized_rows() -> None:
    payload = field_info([_MockField()])

    assert isinstance(payload, list)
    assert len(payload) == 1
    assert isinstance(payload[0], str)

    parts = payload[0].split("\x1f", 2)
    assert len(parts) == 3
    assert parts[0].startswith("air_temperature")
    assert "latitude" in parts[1]
    assert "units" in parts[2]


class _MockCoord:
    def __init__(self, name: str, values: list[object]) -> None:
        self._name = name
        self.array = values

    def identity(self, default: str = "unknown") -> str:
        return self._name or default


class _MockCoordField:
    def __init__(self) -> None:
        self._coords = {
            "time": _MockCoord("time", [1, 2, 3]),
            "height": _MockCoord("height", [10]),
            "lat": _MockCoord("latitude", [-90, 0, 90]),
        }

    def dimension_coordinates(self) -> list[str]:
        return list(self._coords.keys())

    def coordinate(self, key: str) -> _MockCoord:
        return self._coords[key]


def test_coordinate_info_filters_singletons_and_serializes_values() -> None:
    payload = coordinate_info(_MockCoordField())

    assert payload == [
        ("time", ["1", "2", "3"]),
        ("latitude", ["-90", "0", "90"]),
    ]


class _FakePlotField:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] | None = None
        self.collapse_calls: list[tuple[str, str, bool]] = []

    def subspace(self, **kwargs: object) -> "_FakePlotField":
        self.kwargs = kwargs
        return self

    def collapse(self, method: str, axes: str, weights: bool = False) -> "_FakePlotField":
        self.collapse_calls.append((method, axes, weights))
        return self


def test_get_data_for_plotting_builds_subspace_kwargs() -> None:
    fld = _FakePlotField()

    pfld = get_data_for_plotting(
        fld,
        {
            "time": ("3", "1"),
            "level": ("850", "850"),
            "name": ("foo", "foo"),
        },
        {"time": "mean", "name": "max"},
    )

    assert pfld is fld
    assert fld.kwargs is not None
    assert fld.kwargs["level"] == 850
    assert fld.kwargs["name"] == "foo"
    assert str(fld.kwargs["time"]) == str(cf.wi(1, 3))
    assert fld.collapse_calls == [
        ("mean", "time", False),
        ("max", "name", False),
    ]


class _FakeCFPlot:
    def __init__(self) -> None:
        self.levs_calls: list[dict[str, object]] = []
        self.con_calls: list[dict[str, object]] = []
        self.cscale_calls: list[dict[str, object]] = []
        self.gopen_calls: list[str] = []
        self.gclose_calls = 0

    def levs(self, **kwargs: object) -> None:
        self.levs_calls.append(kwargs)

    def con(self, _field: object, **kwargs: object) -> None:
        self.con_calls.append(kwargs)

    def cscale(self, **kwargs: object) -> None:
        self.cscale_calls.append(kwargs)

    def gopen(self, file: str) -> None:
        self.gopen_calls.append(file)

    def gclose(self) -> None:
        self.gclose_calls += 1


class _FakeFigure:
    def __init__(self) -> None:
        self.text_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def text(self, *args: object, **kwargs: object) -> None:
        self.text_calls.append((args, kwargs))


class _FakePlt:
    def __init__(self) -> None:
        self.figure = _FakeFigure()

    def gcf(self) -> _FakeFigure:
        return self.figure


def test_run_contour_plot_applies_levels_annotations_and_save() -> None:
    cfp = _FakeCFPlot()
    plt_obj = _FakePlt()
    messages: list[tuple[str, object]] = []

    run_contour_plot(
        pfld=object(),
        cfp_module=cfp,
        plt_module=plt_obj,
        send_to_gui=lambda prefix, payload=None: messages.append((prefix, payload)),
        options={
            "mode": "explicit",
            "levels": [-1.0, 0.0, 1.0],
            "cscale": "magma",
            "filename": "/tmp/mock.png",
            "annotation_display": True,
            "annotation_properties": [("units", "K")],
        },
    )

    assert cfp.cscale_calls == [{"scale": "magma"}]
    assert cfp.gopen_calls == ["/tmp/mock.png"]
    assert cfp.levs_calls == [{"manual": [-1.0, 0.0, 1.0]}]
    assert cfp.con_calls
    assert cfp.gclose_calls == 1
    assert messages == [("STATUS:Saved plot to /tmp/mock.png", None)]
    assert plt_obj.figure.text_calls
