from __future__ import annotations

import pytest
import xconv2.xconv_cf_interface as cf_interface

from xconv2.xconv_cf_interface import (
    auto_contour_title,
    coordinate_info,
    field_info,
    get_data_for_plotting,
    run_contour_plot,
    run_line_plot,
)

import numpy as np


cf = pytest.importorskip("cf")


class _MockCellMeasures:
    def __call__(self) -> str:
        return "cell_measures: area: areacella"


class _MockField:
    shape = (2, 3)

    def __str__(self) -> str:
        return "mock-field-summary"

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
    assert parts[1] == "mock-field-summary"
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

    def dimension_coordinates(self, **kwargs) -> list[str]:
        return self._coords

    def coordinate(self, key: str) -> _MockCoord:
        return self._coords[key]


def test_coordinate_info_filters_singletons_and_serializes_values() -> None:
    payload = coordinate_info(_MockCoordField())

    assert payload == [
        ("time", ["1", "2", "3"], ""),
        ("latitude", ["-90", "0", "90"], ""),
    ]


class _FakePlotField:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] | None = None
        self.collapse_calls: list[tuple[str, str, bool]] = []

    def subspace(self, **kwargs: object) -> "_FakePlotField":
        self.kwargs = kwargs
        return self

    def dimension_coordinate(self, coord_name) -> None:
        return np.array([0.0, 1.0])

    def collapse(self, instruction: str, weights: bool = False) -> "_FakePlotField":
        self.collapse_calls.append((instruction, weights))
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
        ("time: mean name: max", True),
    ]


class _FakeCFPlot:
    def __init__(self) -> None:
        self.levs_calls: list[dict[str, object]] = []
        self.con_calls: list[dict[str, object]] = []
        self.lineplot_calls: list[dict[str, object]] = []
        self.cscale_calls: list[dict[str, object]] = []
        self.gopen_calls: list[dict[str, object]] = []
        self.setvars_calls: list[dict[str, object]] = []
        self.gclose_calls = 0

    def levs(self, **kwargs: object) -> None:
        self.levs_calls.append(kwargs)

    def con(self, _field: object, **kwargs: object) -> None:
        self.con_calls.append(kwargs)

    def lineplot(self, _field: object, **kwargs: object) -> None:
        self.lineplot_calls.append(kwargs)

    def cscale(self, **kwargs: object) -> None:
        self.cscale_calls.append(kwargs)

    def gopen(self, file: str = "cfplot.png", **kwargs: object) -> None:
        payload: dict[str, object] = {"file": file}
        payload.update(kwargs)
        self.gopen_calls.append(payload)

    def setvars(self, **kwargs: object) -> None:
        self.setvars_calls.append(kwargs)

    def gclose(self) -> None:
        self.gclose_calls += 1


class _FakeFigure:
    def __init__(self) -> None:
        self.text_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.suptitle_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def text(self, *args: object, **kwargs: object) -> None:
        self.text_calls.append((args, kwargs))

    def suptitle(self, *args: object, **kwargs: object) -> None:
        self.suptitle_calls.append((args, kwargs))


class _FakePlt:
    def __init__(self) -> None:
        self.figure = _FakeFigure()

    def gcf(self) -> _FakeFigure:
        return self.figure


def test_run_contour_plot_applies_levels_annotations_and_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfp = _FakeCFPlot()
    plt_obj = _FakePlt()

    monkeypatch.setattr(cf_interface, "cfp", cfp)
    monkeypatch.setattr(cf_interface, "plt", plt_obj)

    run_contour_plot(
        pfld=object(),
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
    assert cfp.gopen_calls == [{"file": "/tmp/mock.png"}]
    assert cfp.levs_calls == [{"manual": [-1.0, 0.0, 1.0]}]
    assert cfp.setvars_calls == [{"title_fontsize": 10.5}]
    assert cfp.con_calls
    assert cfp.gclose_calls == 1
    assert plt_obj.figure.text_calls
    assert plt_obj.figure.text_calls[-1][1]["fontsize"] == 8.0


def test_run_contour_plot_uses_configured_title_font_sizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfp = _FakeCFPlot()
    plt_obj = _FakePlt()

    monkeypatch.setattr(cf_interface, "cfp", cfp)
    monkeypatch.setattr(cf_interface, "plt", plt_obj)

    run_contour_plot(
        pfld=object(),
        options={
            "mode": "default",
            "page_title": "Overview",
            "page_title_display": True,
            "annotation_display": True,
            "annotation_properties": [("units", "K")],
            "contour_title_fontsize": 12.5,
            "page_title_fontsize": 14.0,
            "annotation_fontsize": 9.5,
        },
    )

    assert cfp.setvars_calls == [{"title_fontsize": 12.5}]
    assert plt_obj.figure.suptitle_calls == [
        (("Overview",), {"y": 0.995, "fontsize": 14.0})
    ]
    assert plt_obj.figure.text_calls == [
        ((0.5, 0.02, "units: K"), {"ha": "center", "va": "bottom", "fontsize": 9.5})
    ]


def test_run_contour_plot_sets_title_from_singleton_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfp = _FakeCFPlot()
    plt_obj = _FakePlt()

    monkeypatch.setattr(cf_interface, "cfp", cfp)
    monkeypatch.setattr(cf_interface, "plt", plt_obj)

    run_contour_plot(
        pfld=object(),
        options={"mode": "default"},
        selection_spec={"time": ("2000-01-01", "2000-01-01"), "lat": ("-90", "90")},
        collapse_by_coord={},
    )

    assert cfp.gopen_calls == [{"file": "cfplot.png", "user_plot": 1}]
    assert cfp.con_calls
    assert cfp.con_calls[-1]["title"] == "time=2000-01-01"


def test_run_contour_plot_prefers_cell_method_title_for_collapses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfp = _FakeCFPlot()
    plt_obj = _FakePlt()

    monkeypatch.setattr(cf_interface, "cfp", cfp)
    monkeypatch.setattr(cf_interface, "plt", plt_obj)
    monkeypatch.setattr(cf_interface, "cell_methods_string_from_field", lambda _field, *args: "time: mean")

    run_contour_plot(
        pfld=object(),
        options={"mode": "default"},
        selection_spec={"time": ("2000-01-01", "2000-12-31")},
        collapse_by_coord={"time": "mean"},
    )

    assert cfp.con_calls
    assert cfp.con_calls[-1]["title"] == "time: mean"


def test_auto_contour_title_from_singleton_selection() -> None:
    title = auto_contour_title(
        pfld=object(),
        selection_spec={"time": ("2001-01-01", "2001-01-01"), "lat": ("-90", "90")},
        collapse_by_coord={},
    )
    assert title == "time=2001-01-01"


def test_auto_contour_title_prefers_cell_method_for_collapse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cf_interface, "cell_methods_string_from_field", lambda _field, *args: "time: mean")
    title = auto_contour_title(
        pfld=object(),
        selection_spec={"time": ("2001-01-01", "2001-12-31")},
        collapse_by_coord={"time": "mean"},
    )
    assert title == "time: mean"


def test_run_line_plot_uses_canonical_axes_and_wraps_file_output() -> None:
    field_eg = object()
    monkeypatch = pytest.MonkeyPatch()

    captured: dict[str, object] = {}

    class _FakeLinePlot:
        def __init__(
                self,
                pfld: object,
                options: dict[str, object] | None,
                collapse_by_coord: dict[str, str] | None
        ) -> None:
            captured["pfld"] = pfld
            captured["options"] = options

        def render(self) -> None:
            captured["rendered"] = True

    monkeypatch.setattr(cf_interface, "LinePlot", _FakeLinePlot)

    try:
        run_line_plot(
            pfld=field_eg,
            options={"filename": "/tmp/line.png", "title": "line"},
            selection_spec={"time": ("1", "2")},
            collapse_by_coord={},
        )
    finally:
        monkeypatch.undo()

    assert captured["pfld"] is field_eg
    assert captured["options"] == {"filename": "/tmp/line.png", "title": "line"}
    assert captured["rendered"] is True
 
