from __future__ import annotations

import json

import pytest
import xconv2.xconv_cf_interface as cf_interface
from xconv2.cf_templates import regrid_fields_operation

from xconv2.xconv_cf_interface import (
    add_dimension_coordinate_bounds,
    append_unary_xy_field_operation,
    auto_contour_title,
    coordinate_info,
    field_info,
    get_data_for_plotting,
    parse_coordinate_subspace_commands,
    regrid_from_config,
    remove_fields_by_index,
    run_contour_plot,
    run_line_plot,
    save_selected_field_data,
    save_selected_fields,
)

import numpy as np
import cf


def test_field_info_returns_serialized_rows() -> None:
    field = cf.example_field(0)
    chunks = tuple([2] * field.ndim)
    field.nc_set_dataset_chunksizes(chunks)
    payload = field_info([field])

    assert isinstance(payload, list)
    assert len(payload) == 1
    assert isinstance(payload[0], dict)

    row = payload[0]
    assert str(row["identity"]).startswith(field.identity().strip())
    assert row["detail"] == str(field)
    assert row["properties"] == field.properties()
    assert row["chunk_shape"] == str(chunks)


def test_field_info_ignores_non_serializable_chunk_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    field = cf.example_field(1)
    monkeypatch.setattr(field, "nc_dataset_chunksizes", lambda: object())
    payload = field_info([field])

    assert payload[0]["chunk_shape"] == ""


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
    def __init__(self, coord_arrays: dict[str, object] | None = None) -> None:
        self.kwargs: dict[str, object] | None = None
        self.collapse_calls: list[tuple[str, str, bool]] = []
        self.coord_arrays = coord_arrays or {}

    def subspace(self, **kwargs: object) -> "_FakePlotField":
        self.kwargs = kwargs
        return self

    class _Coord:
        def __init__(self, array: object) -> None:
            self.array = array
            self.size = len(array)

    def dimension_coordinate(self, coord_name, default=None):
        if coord_name in self.coord_arrays:
            return _FakePlotField._Coord(self.coord_arrays[coord_name])
        return default

    def auxiliary_coordinate(self, coord_name, default=None):
        return default

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


def test_get_data_for_plotting_snaps_singleton_to_nearest_coordinate_value() -> None:
    fld = _FakePlotField(coord_arrays={"longitude": np.array([0.0, 0.07, 0.14])})

    pfld = get_data_for_plotting(
        fld,
        {"longitude": ("0.069999", "0.069999")},
        {},
    )

    assert pfld is fld
    assert fld.kwargs is not None
    assert fld.kwargs["longitude"] == pytest.approx(0.07)


def test_append_unary_xy_field_operation_appends_grad_row(monkeypatch: pytest.MonkeyPatch) -> None:
    field = cf.example_field(0)
    fields = [field]
    seen: dict[str, object] = {}

    def _fake_grad_xy(self, **kwargs):
        _ = self
        seen["kwargs"] = dict(kwargs)
        return cf.example_field(1)

    monkeypatch.setattr(cf.Field, "grad_xy", _fake_grad_xy)
    monkeypatch.setattr(cf.Field, "iscyclic", lambda self, axis: False)

    rows = append_unary_xy_field_operation(fields, 0, "grad")

    assert len(fields) == 2
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert "identity" in rows[0]
    assert seen["kwargs"] == {"radius": "earth"}


def test_append_unary_xy_field_operation_applies_laplacian_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    field = cf.example_field(0)
    fields = [field]
    seen: dict[str, object] = {}

    def _fake_laplacian_xy(self, **kwargs):
        _ = self
        seen["kwargs"] = dict(kwargs)
        return cf.example_field(1)

    monkeypatch.setattr(cf.Field, "laplacian_xy", _fake_laplacian_xy)
    monkeypatch.setattr(cf.Field, "iscyclic", lambda self, axis: False)

    _ = append_unary_xy_field_operation(fields, 0, "laplacian")

    assert seen["kwargs"] == {"radius": "earth"}


def test_append_unary_xy_field_operation_handles_multiple_results(monkeypatch: pytest.MonkeyPatch) -> None:
    field = cf.example_field(0)
    fields = [field]
    seen: dict[str, object] = {}

    def _fake_grad_xy(self, **kwargs):
        _ = self
        seen["kwargs"] = dict(kwargs)
        return [cf.example_field(1), cf.example_field(2)]

    monkeypatch.setattr(cf.Field, "grad_xy", _fake_grad_xy)
    monkeypatch.setattr(cf.Field, "iscyclic", lambda self, axis: False)

    rows = append_unary_xy_field_operation(fields, 0, "grad")

    assert seen["kwargs"] == {"radius": "earth"}
    assert len(fields) == 3
    assert len(rows) == 2


def test_append_unary_xy_field_operation_enables_x_wrap_for_cyclic_x(monkeypatch: pytest.MonkeyPatch) -> None:
    field = cf.example_field(0)
    fields = [field]
    seen: dict[str, object] = {}

    def _fake_grad_xy(self, **kwargs):
        _ = self
        seen["kwargs"] = dict(kwargs)
        return cf.example_field(1)

    monkeypatch.setattr(cf.Field, "grad_xy", _fake_grad_xy)
    monkeypatch.setattr(cf.Field, "iscyclic", lambda self, axis: axis == "X")

    _ = append_unary_xy_field_operation(fields, 0, "grad")

    assert seen["kwargs"] == {"radius": "earth", "x_wrap": True}


def test_append_unary_xy_field_operation_keeps_x_wrap_off_for_non_cyclic_x(monkeypatch: pytest.MonkeyPatch) -> None:
    field = cf.example_field(0)
    fields = [field]
    seen: dict[str, object] = {}

    def _fake_grad_xy(self, **kwargs):
        _ = self
        seen["kwargs"] = dict(kwargs)
        return cf.example_field(1)

    monkeypatch.setattr(cf.Field, "grad_xy", _fake_grad_xy)
    monkeypatch.setattr(cf.Field, "iscyclic", lambda self, axis: False)

    _ = append_unary_xy_field_operation(fields, 0, "grad")

    assert seen["kwargs"] == {"radius": "earth"}


def test_regrid_from_config_selected_field_conservative(monkeypatch: pytest.MonkeyPatch) -> None:
    fields = [cf.example_field(0), cf.example_field(1), cf.example_field(2)]
    seen: dict[str, object] = {"calls": []}

    def _fake_regrids(self, dst, **kwargs):
        _ = (self, dst)
        calls = seen["calls"]
        assert isinstance(calls, list)
        calls.append(dict(kwargs))
        return cf.example_field(3)

    monkeypatch.setattr(cf.Field, "regrids", _fake_regrids)

    payload = {
        "target": "selected field",
        "field_indices": [0, 1, 2],
        "target_field_index": 1,
        "target_field_name": str(fields[1].identity()),
        "method": "conservative",
    }
    rows = regrid_from_config(fields, json.dumps(payload))

    assert isinstance(rows, list)
    assert len(rows) == 2
    assert len(fields) == 5
    calls = seen["calls"]
    assert isinstance(calls, list)
    assert calls == [{"method": "conservative"}, {"method": "conservative"}]


def test_regrid_from_config_selected_field_linear(monkeypatch: pytest.MonkeyPatch) -> None:
    fields = [cf.example_field(0), cf.example_field(1)]
    seen: dict[str, object] = {"kwargs": None}

    def _fake_regrids(self, dst, **kwargs):
        _ = (self, dst)
        seen["kwargs"] = dict(kwargs)
        return cf.example_field(2)

    monkeypatch.setattr(cf.Field, "regrids", _fake_regrids)

    payload = {
        "target": "selected field",
        "field_indices": [0, 1],
        "target_field_index": 1,
        "method": "linear",
    }
    rows = regrid_from_config(fields, json.dumps(payload))

    assert len(rows) == 1
    assert len(fields) == 3
    assert seen["kwargs"] == {"method": "linear"}


def test_regrid_from_config_healpix_target(monkeypatch: pytest.MonkeyPatch) -> None:
    fields = [cf.example_field(0), cf.example_field(1)]
    seen: dict[str, object] = {"calls": [], "level": None}

    def _fake_create_healpix(level):
        seen["level"] = int(level)
        return object()

    def _fake_regrids(self, dst, **kwargs):
        _ = self
        calls = seen["calls"]
        assert isinstance(calls, list)
        calls.append({"dst": dst, "kwargs": dict(kwargs)})
        return cf.example_field(2)

    monkeypatch.setattr(cf.Domain, "create_healpix", _fake_create_healpix)
    monkeypatch.setattr(cf.Field, "regrids", _fake_regrids)

    payload = {
        "target": "healpix",
        "field_indices": [0, 1],
        "method": "conservative_2nd",
        "target_spec": {"level": 6},
    }

    rows = regrid_from_config(fields, json.dumps(payload))

    assert seen["level"] == 6
    assert len(rows) == 2
    assert len(fields) == 4
    calls = seen["calls"]
    assert isinstance(calls, list)
    assert len(calls) == 2
    assert all(entry["kwargs"] == {"method": "conservative_2nd"} for entry in calls)


def test_regrid_from_config_regular_latlon_target(monkeypatch: pytest.MonkeyPatch) -> None:
    fields = [cf.example_field(0), cf.example_field(1)]
    seen: dict[str, object] = {"targets": []}

    def _fake_regrids(self, dst, **kwargs):
        _ = self
        targets = seen["targets"]
        assert isinstance(targets, list)
        targets.append({"dst": dst, "kwargs": dict(kwargs)})
        return cf.example_field(2)

    monkeypatch.setattr(cf.Field, "regrids", _fake_regrids)

    payload = {
        "target": "lat/lon",
        "field_indices": [0, 1],
        "method": "nearest_stod",
        "target_spec": {
            "nx": 10,
            "lon1": 0.0,
            "deltax": 1.0,
            "ny": 10,
            "lat1": -45.0,
            "deltay": 1.0,
        },
    }

    rows = regrid_from_config(fields, json.dumps(payload))

    assert len(rows) == 2
    targets = seen["targets"]
    assert isinstance(targets, list)
    assert len(targets) == 2
    assert all(entry["kwargs"] == {"method": "nearest_stod"} for entry in targets)
    # Regular lon/lat target is passed as a coordinate sequence [lon, lat].
    assert isinstance(targets[0]["dst"], list)
    assert len(targets[0]["dst"]) == 2


def test_regrid_from_config_asset_target_spec_list_form(monkeypatch: pytest.MonkeyPatch) -> None:
    fields = [cf.example_field(0), cf.example_field(1)]
    seen: dict[str, object] = {"target": None}

    def _fake_regrids(self, dst, **kwargs):
        _ = (self, kwargs)
        seen["target"] = dst
        return cf.example_field(2)

    monkeypatch.setattr(cf.Field, "regrids", _fake_regrids)

    payload = {
        "target": "N216T",
        "field_indices": [0, 1],
        "method": "linear",
        "target_key": "N216T",
        "target_spec": [
            {"longitude": {"nx": 432, "lon1": 0.41666667, "deltax": 0.83333333}},
            {"latitude": {"ny": 324, "lat1": -89.72222222, "deltay": 0.55555556}},
        ],
    }

    rows = regrid_from_config(fields, json.dumps(payload))

    assert len(rows) == 2
    target = seen["target"]
    assert isinstance(target, list)
    assert len(target) == 2


def test_regrid_from_config_rejects_unknown_target_without_spec() -> None:
    fields = [cf.example_field(0), cf.example_field(1)]
    payload = {
        "target": "unknown-target",
        "field_indices": [0, 1],
        "method": "linear",
    }

    with pytest.raises(ValueError, match="target_spec"):
        regrid_from_config(fields, json.dumps(payload))


def test_regrid_fields_operation_template_executes_with_dialog_payload() -> None:
    fields = [cf.example_field(0), cf.example_field(1)]
    payload = {
        "target": "selected field",
        "field_indices": [0, 1],
        "target_field_index": 1,
        "target_field_name": str(fields[1].identity()),
        "method": "conservative_1st",
    }

    code = regrid_fields_operation(json.dumps(payload))

    captured: dict[str, object] = {"json": None}
    messages: list[tuple[str, object]] = []

    def _fake_regrid_from_config(worker_fields, config_json):
        assert worker_fields is fields
        captured["json"] = config_json
        return [{"identity": "regridded_field", "detail": "mock", "properties": {}, "chunk_shape": ""}]

    namespace = {
        "f": fields,
        "regrid_from_config": _fake_regrid_from_config,
        "send_to_gui": lambda prefix, payload=None: messages.append((prefix, payload)),
    }

    exec(code, namespace)

    assert captured["json"] is not None
    decoded = json.loads(str(captured["json"]))
    assert decoded == payload
    assert ("METADATA_APPEND", [{"identity": "regridded_field", "detail": "mock", "properties": {}, "chunk_shape": ""}]) in messages
    assert any(msg[0].startswith("STATUS:Added 1 regridded field(s)") for msg in messages)


def test_append_unary_xy_field_operation_requires_xy_axes(monkeypatch: pytest.MonkeyPatch) -> None:
    field = cf.example_field(0)

    def _no_x_dimension_coordinate(self, filter_by_axis=(), default=None):
        _ = self
        if filter_by_axis == ("X",):
            return None
        if filter_by_axis == ("Y",):
            return object()
        return default

    def _no_x_auxiliary_coordinate(self, filter_by_axis=(), axis_mode=None, default=None):
        _ = (self, axis_mode)
        if filter_by_axis == ("X",):
            return None
        if filter_by_axis == ("Y",):
            return object()
        return default

    monkeypatch.setattr(cf.Field, "dimension_coordinate", _no_x_dimension_coordinate)
    monkeypatch.setattr(cf.Field, "auxiliary_coordinate", _no_x_auxiliary_coordinate)

    fields = [field]

    with pytest.raises(ValueError, match="both X and Y axes"):
        append_unary_xy_field_operation(fields, 0, "laplacian")


def test_add_dimension_coordinate_bounds_updates_missing_bounds() -> None:
    field = cf.example_field(1)
    coords = list(field.dimension_coordinates().values())
    assert len(coords) >= 2

    for coord in coords:
        if coord.has_bounds():
            coord.del_bounds()

    missing_before = {
        coord.identity()
        for coord in coords
        if not coord.has_bounds()
    }

    fields = [field]

    rows, updated = add_dimension_coordinate_bounds(fields, 0)

    assert len(rows) == 1
    assert updated
    assert set(updated).issubset(missing_before)

    by_identity = {coord.identity(): coord for coord in coords}
    for name in updated:
        assert by_identity[name].has_bounds()


def test_add_dimension_coordinate_bounds_requires_valid_index() -> None:
    with pytest.raises(IndexError, match="add_bounds"):
        add_dimension_coordinate_bounds([], 0)


def test_remove_fields_by_index_removes_descending() -> None:
    fields = ["a", "b", "c", "d"]

    removed = remove_fields_by_index(fields, [1, 3])

    assert removed == 2
    assert fields == ["a", "c"]


def test_remove_fields_by_index_ignores_out_of_range() -> None:
    fields = ["a", "b"]

    removed = remove_fields_by_index(fields, [99, -1, 1])

    assert removed == 1
    assert fields == ["a"]


def test_save_selected_fields_writes_netcdf(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, str, str, dict[str, object]]] = []

    def _fake_write(fields, destination, fmt="NETCDF4", **kwargs):
        calls.append((fields, destination, fmt, kwargs))

    monkeypatch.setattr(cf_interface.cf, "write", _fake_write)
    monkeypatch.setattr(
        cf_interface,
        "estimate_hdf5_metadata_bytes_for_fields",
        lambda *args, **kwargs: 1234,
    )

    payload = [
        cf.example_field(0),
        cf.example_field(1),
        cf.example_field(2),
    ]
    count = save_selected_fields(payload, [0, 2], "/tmp/out.nc", "nc")

    assert count == 2
    assert len(calls) == 1
    written_fields, destination, fmt, kwargs = calls[0]
    assert written_fields == [payload[0], payload[2]]
    assert destination == "/tmp/out.nc"
    assert fmt == "NETCDF4"
    assert kwargs == {"h5py_options": {"meta_block_size": 1234}}


def test_save_selected_fields_writes_zarr(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, str, str]] = []

    def _fake_write(fields, destination, fmt="NETCDF4", **kwargs):
        _ = kwargs
        calls.append((fields, destination, fmt))

    monkeypatch.setattr(cf_interface.cf, "write", _fake_write)

    payload = [cf.example_field(0), cf.example_field(1)]
    count = save_selected_fields(payload, [1], "/tmp/out.zarr", "zarr")

    assert count == 1
    assert calls == [([payload[1]], "/tmp/out.zarr", "ZARR")]


def test_save_selected_fields_passes_chunk_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, str, str, dict[str, object]]] = []

    def _fake_write(fields, destination, fmt="NETCDF4", **kwargs):
        calls.append((fields, destination, fmt, kwargs))

    monkeypatch.setattr(cf_interface.cf, "write", _fake_write)
    monkeypatch.setattr(
        cf_interface,
        "estimate_hdf5_metadata_bytes_for_fields",
        lambda *args, **kwargs: 2222,
    )

    payload = [
        cf.example_field(0),
        cf.example_field(1),
        cf.example_field(2),
    ]

    def _normalized_chunks(field: cf.Field, requested: tuple[int, ...]) -> tuple[int, ...]:
        return tuple(min(int(size), int(dim)) for size, dim in zip(requested, field.shape))

    request0 = tuple([4] * payload[0].ndim)
    request2 = tuple([6] * payload[2].ndim)
    payload[0].nc_set_dataset_chunksizes(tuple([1] * payload[0].ndim))
    payload[2].nc_set_dataset_chunksizes(tuple([2] * payload[2].ndim))
    _ = save_selected_fields(
        payload,
        [0, 2],
        "/tmp/out.nc",
        "nc",
        {0: str(request0), 2: str(request2)},
    )

    assert len(calls) == 1
    written_fields, destination, fmt, kwargs = calls[0]
    assert written_fields == [payload[0], payload[2]]
    assert destination == "/tmp/out.nc"
    assert fmt == "NETCDF4"
    assert kwargs == {"h5py_options": {"meta_block_size": 2222}}
    assert tuple(payload[0].nc_dataset_chunksizes()) == _normalized_chunks(payload[0], request0)
    assert tuple(payload[2].nc_dataset_chunksizes()) == _normalized_chunks(payload[2], request2)


def test_save_selected_fields_skips_matching_chunk_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[int] = []

    def _fake_write(fields, destination, fmt="NETCDF4", **kwargs):
        _ = (fields, destination, fmt, kwargs)
        writes.append(1)

    monkeypatch.setattr(cf_interface.cf, "write", _fake_write)
    monkeypatch.setattr(
        cf_interface,
        "estimate_hdf5_metadata_bytes_for_fields",
        lambda *args, **kwargs: 1111,
    )

    field = cf.example_field(0)
    matching = tuple([4] * field.ndim)
    field.nc_set_dataset_chunksizes(matching)
    _ = save_selected_fields([field], [0], "/tmp/out.nc", "nc", {0: str(matching)})

    assert writes == [1]
    assert tuple(field.nc_dataset_chunksizes()) == matching


def test_save_selected_fields_rejects_unknown_format() -> None:
    with pytest.raises(ValueError, match="Unsupported output format"):
        save_selected_fields(["a"], [0], "/tmp/out.foo", "foo")


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
 
