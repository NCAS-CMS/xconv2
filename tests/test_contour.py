from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from xconv2.cf_templates import contour_range_from_selection, plot_from_selection


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

    def collapse(self, method: str, axes: str, weights: bool = False) -> "_FakeField":
        self.collapse_calls.append((method, axes, weights))
        return self


class _FakeCF:
    @staticmethod
    def wi(lo: object, hi: object) -> tuple[object, object]:
        return (lo, hi)


@dataclass
class _FakeCFPlot:
    levs_calls: list[dict[str, object]] = field(default_factory=list)
    con_calls: list[dict[str, object]] = field(default_factory=list)
    cscale_calls: list[dict[str, object]] = field(default_factory=list)
    gopen_calls: list[str] = field(default_factory=list)
    gclose_calls: int = 0

    def levs(self, **kwargs: object) -> None:
        self.levs_calls.append(kwargs)

    def con(self, field: object, **kwargs: object) -> None:
        self.con_calls.append(kwargs)

    def cscale(self, **kwargs: object) -> None:
        self.cscale_calls.append(kwargs)

    def gopen(self, file: str) -> None:
        self.gopen_calls.append(file)

    def gclose(self) -> None:
        self.gclose_calls += 1


@dataclass
class _FakeFigure:
    text_calls: list[tuple[tuple[object, ...], dict[str, object]]] = field(default_factory=list)

    def text(self, *args: object, **kwargs: object) -> None:
        self.text_calls.append((args, kwargs))


@dataclass
class _FakePlt:
    figure: _FakeFigure = field(default_factory=_FakeFigure)

    def gcf(self) -> _FakeFigure:
        return self.figure



def _run_generated(
    code: str,
    fld: _FakeField,
    cfp: _FakeCFPlot,
    plt_obj: _FakePlt | None = None,
) -> list[tuple[str, object]]:
    messages: list[tuple[str, object]] = []
    if plt_obj is None:
        plt_obj = _FakePlt()
    namespace = {
        "fld": fld,
        "cf": _FakeCF,
        "cfp": cfp,
        "np": np,
        "plt": plt_obj,
        "send_to_gui": lambda prefix, payload=None: messages.append((prefix, payload)),
    }
    exec(code, namespace)
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
    assert fld.collapse_calls == [("mean", "time", False)]
    assert cfp.levs_calls
    assert cfp.cscale_calls == [{"scale": "magma"}]
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
    messages = _run_generated(code, fld, cfp)

    assert cfp.gopen_calls == [output_file]
    assert cfp.gclose_calls == 1
    assert cfp.cscale_calls == [{}]
    assert cfp.con_calls
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
    assert payload == {"min": 0.0, "max": 3.0}
