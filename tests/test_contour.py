from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from xconv2.cf_templates import contour_range_from_selection, plot_from_selection, save_data_from_selection
import xconv2.xconv_cf_interface as cf_interface
from xconv2.xconv_cf_interface import auto_contour_title, get_data_for_plotting, run_contour_plot


@dataclass
class _FakeField:
    subspace_calls: list[dict[str, object]] = field(default_factory=list)
    collapse_calls: list[tuple[str, str, bool]] = field(default_factory=list)

    @property
    def array(self) -> np.ndarray:
        return np.array([[0.0, 1.0], [2.0, 3.0]], dtype=float)

    def subspace(self, **kwargs: object) -> "_FakeField":
        self.subspace_calls.append(kwargs)
        return self

    def collapse(self, instruction: str, weights: bool = False) -> "_FakeField":
        self.collapse_calls.append((instruction, weights))
        return self

    def cell_methods(self, **kwargs) -> None:
        return {}

    def dimension_coordinate(self, coord_name) -> None:
        return np.array([0.0, 1.0])

    def dimension_coordinates(self, **kwargs) -> None:
        return {}

    def domain_axes(self, **kwargs) -> None:
        return {}

    def _unique_domain_axis_identities(self, **kwargs) -> None:
        return None


class _FakeCF:
    @staticmethod
    def wi(lo: object, hi: object) -> tuple[object, object]:
        return (lo, hi)


@dataclass
class _FakeCFPlot:
    levs_calls: list[dict[str, object]] = field(default_factory=list)
    con_calls: list[dict[str, object]] = field(default_factory=list)
    cscale_calls: list[dict[str, object]] = field(default_factory=list)
    gopen_calls: list[dict[str, object]] = field(default_factory=list)
    gclose_calls: int = 0

    def levs(self, **kwargs: object) -> None:
        self.levs_calls.append(kwargs)

    def con(self, field: object, **kwargs: object) -> None:
        self.con_calls.append(kwargs)

    def cscale(self, **kwargs: object) -> None:
        self.cscale_calls.append(kwargs)

    def gopen(self, file: str = "cfplot.png", **kwargs: object) -> None:
        payload: dict[str, object] = {"file": file}
        payload.update(kwargs)
        self.gopen_calls.append(payload)

    def gclose(self) -> None:
        self.gclose_calls += 1


@dataclass
class _FakeFigure:
    text_calls: list[tuple[tuple[object, ...], dict[str, object]]] = field(default_factory=list)
    savefig_calls: list[str] = field(default_factory=list)

    def text(self, *args: object, **kwargs: object) -> None:
        self.text_calls.append((args, kwargs))

    def savefig(self, filename: str) -> None:
        self.savefig_calls.append(filename)


@dataclass
class _FakePlt:
    figure: _FakeFigure = field(default_factory=_FakeFigure)
    close_calls: int = 0

    def gcf(self) -> _FakeFigure:
        return self.figure

    def close(self, _fig: object) -> None:
        self.close_calls += 1



def _run_generated(
    code: str,
    fld: _FakeField,
    cfp: _FakeCFPlot,
    plt_obj: _FakePlt | None = None,
) -> list[tuple[str, object]]:
    messages: list[tuple[str, object]] = []
    if plt_obj is None:
        plt_obj = _FakePlt()

    # Keep helper tests deterministic by overriding module-level plotting deps.
    prev_cfp = cf_interface.cfp
    prev_plt = cf_interface.plt
    cf_interface.cfp = cfp
    cf_interface.plt = plt_obj

    namespace = {
        "fld": fld,
        "cf": _FakeCF,
        "np": np,
        "get_data_for_plotting": get_data_for_plotting,
        "save_selected_field_data": lambda _field, _path: None,
        "run_contour_plot": run_contour_plot,
        "auto_contour_title": auto_contour_title,
        "send_to_gui": lambda prefix, payload=None: messages.append((prefix, payload)),
    }
    try:
        exec(code, namespace)
    finally:
        cf_interface.cfp = prev_cfp
        cf_interface.plt = prev_plt

    return messages



def test_plot_from_selection_contour_auto_options_executes_and_calls_con() -> None:
    code = plot_from_selection(
        selections={"time": ("1", "2"), "latitude": ("-90", "90")},
        collapse_by_coord={"time": "mean"},
        plot_kind="contour",
        plot_options={
            "mode": "auto",
            "min": -2.0,
            "max": 2.0,
            "intervals": 4,
            "fill": True,
            "lines": True,
            "line_labels": True,
            "negative_linestyle": "solid",
            "zero_thick": False,
            "blockfill": False,
            "blockfill_fast": None,
            "cscale": "magma",
        },
    )

    fld = _FakeField()
    cfp = _FakeCFPlot()
    _run_generated(code, fld, cfp)

    assert fld.subspace_calls
    assert fld.collapse_calls == [("time: mean", True)]
    assert cfp.levs_calls
    assert cfp.cscale_calls == [{"scale": "magma"}]
    assert cfp.gopen_calls == [{"file": "cfplot.png", "user_plot": 1}]
    assert cfp.con_calls



def test_plot_from_selection_contour_explicit_levels_and_save_file() -> None:
    output_file = "/tmp/mock-contour.png"
    code = plot_from_selection(
        selections={"latitude": ("-90", "90"), "longitude": ("0", "359")},
        collapse_by_coord={},
        plot_kind="contour",
        plot_options={
            "mode": "explicit",
            "levels": [-1.0, 0.0, 1.0],
            "fill": True,
            "lines": True,
            "line_labels": False,
            "negative_linestyle": "dashed",
            "zero_thick": 3,
            "blockfill": True,
            "blockfill_fast": True,
            "filename": output_file,
        },
    )

    fld = _FakeField()
    cfp = _FakeCFPlot()
    fake_plt = _FakePlt()
    messages = _run_generated(code, fld, cfp, plt_obj=fake_plt)

    assert cfp.gopen_calls == [{"file": "cfplot.png", "user_plot": 1}]
    assert cfp.gclose_calls == 0
    assert cfp.cscale_calls == [{}]
    assert cfp.con_calls
    assert fake_plt.figure.savefig_calls == [output_file]
    assert fake_plt.close_calls == 1
    assert ("STATUS:Saved plot to /tmp/mock-contour.png", None) in messages


def test_plot_from_selection_contour_annotations_are_rendered_when_enabled() -> None:
    code = plot_from_selection(
        selections={"latitude": ("-90", "90"), "longitude": ("0", "359")},
        collapse_by_coord={},
        plot_kind="contour",
        plot_options={
            "mode": "default",
            "annotation_display": True,
            "annotation_properties": [
                ("long_name", "air_temperature"),
                ("units", "K"),
            ],
        },
    )

    fld = _FakeField()
    cfp = _FakeCFPlot()
    fake_plt = _FakePlt()
    _run_generated(code, fld, cfp, plt_obj=fake_plt)

    assert fake_plt.figure.text_calls
    args, _kwargs = fake_plt.figure.text_calls[-1]
    assert "long_name: air_temperature" in str(args[2])
    assert "units: K" in str(args[2])



def test_contour_range_from_selection_emits_min_max_payload() -> None:
    code = contour_range_from_selection(
        selections={"latitude": ("-90", "90"), "longitude": ("0", "359")},
        collapse_by_coord={},
    )

    fld = _FakeField()
    cfp = _FakeCFPlot()
    messages = _run_generated(code, fld, cfp)

    assert messages
    prefix, payload = messages[-1]
    assert prefix == "CONTOUR_RANGE"
    assert payload["min"] == 0.0
    assert payload["max"] == 3.0
    assert isinstance(payload.get("suggested_title"), str)


def test_plot_from_selection_lineplot_generates_worker_call() -> None:
    code = plot_from_selection(
        selections={"time": ("1", "2")},
        collapse_by_coord={},
        plot_kind="lineplot",
        plot_options={"mode": "default"},
    )

    assert "run_line_plot(" in code
    assert "lineplot_options" in code


def test_plot_from_selection_includes_data_save_when_requested() -> None:
    code = plot_from_selection(
        selections={"time": ("1", "2")},
        collapse_by_coord={},
        plot_kind="lineplot",
        plot_options={"mode": "default"},
        save_data_path="/tmp/selection.nc",
    )

    assert "save_selected_field_data(pfld, save_data_path)" in code
    assert "save_data_path = '/tmp/selection.nc'" in code


def test_save_data_from_selection_builds_data_only_worker_code() -> None:
    code = save_data_from_selection(
        selections={"time": ("1", "2")},
        collapse_by_coord={"time": "mean"},
        save_data_path="/tmp/data-only.nc",
    )

    assert "pfld = get_data_for_plotting" in code
    assert "save_selected_field_data(pfld, save_data_path)" in code
    assert "run_line_plot(" not in code
    assert "run_contour_plot(" not in code
