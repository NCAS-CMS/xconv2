from __future__ import annotations

import pytest
import xconv2.xconv_cf_interface as cf_interface

from xconv2.xconv_cf_interface import (
    add_dimension_coordinate_bounds,
    append_unary_xy_field_operation,
    auto_contour_title,
    coordinate_info,
    field_info,
    get_data_for_plotting,
    parse_coordinate_subspace_commands,
    run_contour_plot,
    run_line_plot,
    save_selected_field_data,
)

import numpy as np
import cf

class _MockCellMeasures:
    def __call__(self) -> str:
        return "cell_measures: area: areacella"


class _MockField:
    #FIXME: replace with a cf example field. 
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
    assert isinstance(payload[0], dict)

    row = payload[0]
    assert str(row["identity"]).startswith("air_temperature")
    assert row["detail"] == "mock-field-summary"
    assert row["properties"] == {"units": "K", "standard_name": "air_temperature"}


class _MockCoord:
    def __init__(self, name: str, values: list[object]) -> None:
        self._name = name
        self.array = values

    def identity(self, default: str = "unknown") -> str:
        return self._name or default


def test_coordinate_info_filters_singletons_and_serializes_values() -> None:

    field = cf.example_field(7) 
    payload = coordinate_info(field)

    assert payload == [
        ('time', ['120.5', '121.5', '122.5'], 'days since 1979-1-1 gregorian'),
        ('grid_latitude', ['0.44', '0.00', '-0.44', '-0.88'], 'degrees'),
        ('grid_longitude', ['-1.18', '-0.74', '-0.30', '0.14', '0.58'], 'degrees')
    ]


def test_coordinate_info_nemo_uses_2d_fallback_ranges() -> None:
    field = cf.read('data/nemo_field_eg1.nc')[0]
    payload = coordinate_info(field)

    by_name = {name: (values, units) for name, values, units in payload}

    assert 'latitude' in by_name
    assert 'longitude' in by_name

    lat_values, lat_units = by_name['latitude']
    lon_values, lon_units = by_name['longitude']

    assert lat_units == 'degrees_north'
    assert lon_units == 'degrees_east'

    # Bbox-driven synthesis uses a shared high-resolution count for 2D coords.
    assert len(lat_values) == 1440
    assert len(lon_values) == 1440

    # Ranges are synthesized from directional min/max.
    # Note: values are formatted to .2f precision for degrees coordinates
    assert float(lat_values[0]) == pytest.approx(-89.5)
    assert float(lat_values[-1]) == pytest.approx(89.95)  # 89.94786... rounded to .2f
    assert float(lon_values[0]) == pytest.approx(-180.0)
    assert float(lon_values[-1]) == pytest.approx(180.0)


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


class _FakeXYField:
    def __init__(
        self,
        *,
        has_x: bool = True,
        has_y: bool = True,
        x_coord: object | None = None,
        x_iscyclic: bool = False,
    ) -> None:
        self._has_x = has_x
        self._has_y = has_y
        self._x_coord = x_coord
        self._x_iscyclic = x_iscyclic
        self.last_grad_kwargs: dict[str, object] | None = None
        self.last_laplacian_kwargs: dict[str, object] | None = None

    def identity(self, default: str = "unknown") -> str:
        return "demo" if default else "demo"

    @property
    def shape(self) -> tuple[int, ...]:
        return (2, 2)

    def properties(self) -> dict[str, str]:
        return {"units": "1"}

    def __str__(self) -> str:
        return "demo-field"

    def dimension_coordinate(self, filter_by_axis=(), default=None):
        if filter_by_axis == ("X",):
            if self._x_coord is not None:
                return self._x_coord
            return object() if self._has_x else None
        if filter_by_axis == ("Y",):
            return object() if self._has_y else None
        return default

    def auxiliary_coordinate(self, filter_by_axis=(), axis_mode=None, default=None):
        _ = axis_mode
        if filter_by_axis == ("X",):
            return object() if self._has_x else None
        if filter_by_axis == ("Y",):
            return object() if self._has_y else None
        return default

    def grad_xy(self, **kwargs: object):
        self.last_grad_kwargs = dict(kwargs)
        return _FakeXYField()

    def laplacian_xy(self, **kwargs: object):
        self.last_laplacian_kwargs = dict(kwargs)
        return _FakeXYField()

    def iscyclic(self, axis: str) -> bool:
        return axis == "X" and self._x_iscyclic


class _FakeDimensionCoordinate:
    def __init__(
        self,
        name: str,
        *,
        has_bounds: bool,
        cellsize: object | None = None,
    ) -> None:
        self._name = name
        self._has_bounds = has_bounds
        self.cellsize = cellsize
        self.create_bounds_calls: list[dict[str, object]] = []

    def identity(self, default: str = "unknown") -> str:
        return self._name or default

    def has_bounds(self) -> bool:
        return self._has_bounds

    def create_bounds(
        self,
        bound: object | None = None,
        cellsize: object | None = None,
        flt: float = 0.5,
        max: object | None = None,
        min: object | None = None,
        inplace: bool = False,
    ) -> object | None:
        _ = (bound, flt, max, min)
        self.create_bounds_calls.append({"cellsize": cellsize, "inplace": inplace})
        if self._name == "height" and cellsize is None:
            raise ValueError("cellsize is required in this fake")
        self._has_bounds = True
        return None if inplace else object()


class _FakeXCoordBounds:
    class _Bounds:
        def __init__(self, array: object) -> None:
            self.array = array

    def __init__(self, has_bounds: bool, bounds_array: object | None = None) -> None:
        self._has_bounds = has_bounds
        self._bounds_array = bounds_array

    def has_bounds(self) -> bool:
        return self._has_bounds

    def get_bounds(self, default=None):
        if not self._has_bounds or self._bounds_array is None:
            return default
        return _FakeXCoordBounds._Bounds(self._bounds_array)


class _FakeDimensionCoordinates:
    def __init__(self, coords: list[_FakeDimensionCoordinate]) -> None:
        self._coords = coords

    def values(self):
        return list(self._coords)


class _FakeBoundsField:
    def __init__(self) -> None:
        self.coords = _FakeDimensionCoordinates(
            [
                _FakeDimensionCoordinate("time", has_bounds=True),
                _FakeDimensionCoordinate("latitude", has_bounds=False),
                _FakeDimensionCoordinate("height", has_bounds=False, cellsize=0.0),
            ]
        )

    def identity(self, default: str = "unknown") -> str:
        return "air_temperature" if default else "air_temperature"

    @property
    def shape(self) -> tuple[int, ...]:
        return (2, 2)

    def properties(self) -> dict[str, str]:
        return {"units": "K"}

    def __str__(self) -> str:
        return "fake-bounds-field"

    def dimension_coordinates(self):
        return self.coords


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


def test_append_unary_xy_field_operation_appends_grad_row() -> None:
    field = _FakeXYField()
    fields = [field]

    rows = append_unary_xy_field_operation(fields, 0, "grad")

    assert len(fields) == 2
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert "identity" in rows[0]
    assert field.last_grad_kwargs == {"radius": "earth"}


def test_append_unary_xy_field_operation_applies_laplacian_defaults() -> None:
    field = _FakeXYField()
    fields = [field]

    _ = append_unary_xy_field_operation(fields, 0, "laplacian")

    assert field.last_laplacian_kwargs == {"radius": "earth"}


def test_append_unary_xy_field_operation_handles_multiple_results() -> None:
    class _FakeMultiResultXYField(_FakeXYField):
        def grad_xy(self, **kwargs: object):
            self.last_grad_kwargs = dict(kwargs)
            return [_FakeXYField(), _FakeXYField()]

    field = _FakeMultiResultXYField()
    fields = [field]

    rows = append_unary_xy_field_operation(fields, 0, "grad")

    assert field.last_grad_kwargs == {"radius": "earth"}
    assert len(fields) == 3
    assert len(rows) == 2


def test_append_unary_xy_field_operation_enables_x_wrap_for_global_x_bounds() -> None:
    global_bounds = _FakeXCoordBounds(has_bounds=True, bounds_array=[[0.0, 1.0], [359.0, 360.0]])
    field = _FakeXYField(x_coord=global_bounds, x_iscyclic=True)
    fields = [field]

    _ = append_unary_xy_field_operation(fields, 0, "grad")

    assert field.last_grad_kwargs == {"radius": "earth", "x_wrap": True}


def test_append_unary_xy_field_operation_keeps_x_wrap_off_without_bounds() -> None:
    x_no_bounds = _FakeXCoordBounds(has_bounds=False)
    field = _FakeXYField(x_coord=x_no_bounds, x_iscyclic=True)
    fields = [field]

    _ = append_unary_xy_field_operation(fields, 0, "grad")

    assert field.last_grad_kwargs == {"radius": "earth"}


def test_append_unary_xy_field_operation_requires_xy_axes() -> None:
    fields = [_FakeXYField(has_x=False, has_y=True)]

    with pytest.raises(ValueError, match="both X and Y axes"):
        append_unary_xy_field_operation(fields, 0, "laplacian")


def test_add_dimension_coordinate_bounds_updates_missing_bounds() -> None:
    field = _FakeBoundsField()
    fields = [field]

    rows, updated = add_dimension_coordinate_bounds(fields, 0)

    assert len(rows) == 1
    assert updated == ["latitude", "height"]
    assert field.coords.values()[0].create_bounds_calls == []
    assert field.coords.values()[1].create_bounds_calls == [{"cellsize": None, "inplace": True}]
    assert field.coords.values()[2].create_bounds_calls == [
        {"cellsize": None, "inplace": True},
        {"cellsize": 0.0, "inplace": True},
    ]


def test_add_dimension_coordinate_bounds_requires_valid_index() -> None:
    with pytest.raises(IndexError, match="add_bounds"):
        add_dimension_coordinate_bounds([], 0)


def test_parse_coordinate_subspace_commands_accepts_multiple_formats() -> None:
    commands = """
    time=1:3
    latitude: -20, 20
    longitude -5 10
    level 850
    """

    parsed = parse_coordinate_subspace_commands(commands)

    assert parsed == {
        "time": (1, 3),
        "latitude": (-20, 20),
        "longitude": (-5, 10),
        "level": (850, 850),
    }


def test_parse_coordinate_subspace_commands_reports_line_numbers() -> None:
    with pytest.raises(ValueError, match=r"line 2"):
        parse_coordinate_subspace_commands("time=1:2\ninvalid line tokenized too much\n")


class _FakeCFPlot:
    def __init__(self) -> None:
        self.levs_calls: list[dict[str, object]] = []
        self.con_calls: list[dict[str, object]] = []
        self.lineplot_calls: list[dict[str, object]] = []
        self.cscale_calls: list[dict[str, object]] = []
        self.gopen_calls: list[dict[str, object]] = []
        self.setvars_calls: list[dict[str, object]] = []
        self.gclose_calls = 0
        self.mapset_calls: list[dict[str, object]] = []

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

    def mapset(self, **kwargs: object) -> None:
        self.mapset_calls.append(kwargs)


class _FakeFigure:
    def __init__(self) -> None:
        self.text_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.suptitle_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.savefig_calls: list[str] = []

    def text(self, *args: object, **kwargs: object) -> None:
        self.text_calls.append((args, kwargs))

    def suptitle(self, *args: object, **kwargs: object) -> None:
        self.suptitle_calls.append((args, kwargs))

    def savefig(self, filename: str) -> None:
        self.savefig_calls.append(filename)


class _FakePlt:
    def __init__(self) -> None:
        self.figure = _FakeFigure()
        self.close_calls = 0

    def gcf(self) -> _FakeFigure:
        return self.figure

    def close(self, _fig: object) -> None:
        self.close_calls += 1


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
    assert cfp.gopen_calls == [{"file": "cfplot.png", "user_plot": 1}]
    assert cfp.levs_calls == [{"manual": [-1.0, 0.0, 1.0]}]
    assert cfp.setvars_calls == [{"title_fontsize": 10.5, "viewer": None}]
    assert cfp.con_calls
    assert cfp.gclose_calls == 0
    assert plt_obj.figure.savefig_calls == ["/tmp/mock.png"]
    assert plt_obj.close_calls == 1
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

    assert cfp.setvars_calls == [{"title_fontsize": 12.5, "viewer": None}]
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


def test_save_selected_field_data_uses_cf_write(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, str]] = []
    monkeypatch.setattr(
        cf_interface.cf,
        "write",
        lambda field, filename: calls.append((field, filename)),
    )

    field_obj = object()
    save_selected_field_data(field_obj, "/tmp/out.nc")

    assert calls == [(field_obj, "/tmp/out.nc")]
 
