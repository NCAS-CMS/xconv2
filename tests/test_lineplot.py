from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import xconv2.lineplot as lineplot_module
from xconv2.lineplot import LinePlot


class _FakeCoord:
    def __init__(
        self,
        name: str,
        values: list[object],
        *,
        is_time: bool = False,
        datetime_values: list[object] | None = None,
    ) -> None:
        self._name = name
        self.array = values
        self.size = len(values)
        self.T = is_time
        self.datetime_array = datetime_values if datetime_values is not None else values

    def identity(self, default: str = "unknown") -> str:
        return self._name or default


class _FakeField:
    def __init__(
        self,
        shape: tuple[int, ...],
        coords: dict[str, _FakeCoord],
        array: np.ndarray,
        ident: str = "air_temperature",
        units: str = "",
    ) -> None:
        self.shape = shape
        self._coords = coords
        self.array = array
        self._identity = ident
        self.units = units

    def dimension_coordinates(self, todict: bool = False):
        if todict:
            return self._coords
        return list(self._coords.values())

    def cell_methods(self, **kwargs) -> None:
        return {}

    def domain_axes(self, **kwargs) -> None:
        return {}

    def identity(self, default: str = "value") -> str:
        return self._identity or default


class _FakeFigure:
    def __init__(self) -> None:
        self.size_inches: tuple[float, float] | None = None
        self.dpi: float | None = None

    def set_size_inches(self, width: float, height: float, forward: bool = True) -> None:
        _ = forward
        self.size_inches = (width, height)

    def set_dpi(self, dpi: float) -> None:
        self.dpi = dpi


class _FakeAxes:
    def __init__(self) -> None:
        self.title = ""
        self.xlabel = ""
        self.ylabel = ""

    def set_title(self, value: str) -> None:
        self.title = value

    def set_xlabel(self, value: str) -> None:
        self.xlabel = value

    def set_ylabel(self, value: str) -> None:
        self.ylabel = value


class _FakePlt:
    def __init__(self, axes: _FakeAxes) -> None:
        self._axes = axes
        self._figure = _FakeFigure()
        self.savefig_calls: list[str] = []

    def gca(self) -> _FakeAxes:
        return self._axes

    def gcf(self) -> _FakeFigure:
        return self._figure

    def savefig(self, filename: str) -> None:
        self.savefig_calls.append(filename)


def test_lineplot_rejects_more_than_2d() -> None:
    field = _FakeField(
        shape=(2, 3, 4),
        coords={},
        array=np.zeros((2, 3, 4)),
    )

    with pytest.raises(ValueError, match="1D or 2D"):
        LinePlot(field, options={})


def test_lineplot_render_1d_uses_pandas_series_and_savefig(monkeypatch: pytest.MonkeyPatch) -> None:
    axes = _FakeAxes()
    plt_obj = _FakePlt(axes)

    monkeypatch.setattr(lineplot_module, "plt", plt_obj)

    captured: dict[str, object] = {}

    def _fake_series_plot(self: pd.Series, ax=None, **kwargs):  # type: ignore[no-untyped-def]
        captured["series_index"] = list(self.index)
        captured["series_values"] = list(self.values)
        captured["kwargs"] = kwargs
        return ax

    monkeypatch.setattr(pd.Series, "plot", _fake_series_plot)

    field = _FakeField(
        shape=(5,),
        coords={"x": _FakeCoord("x", [0, 1, 2, 3, 4])},
        array=np.array([1.0, 2.0, 3.0, 4.0, 5.0]),
    )

    LinePlot(field, options={"title": "one-d", "filename": "/tmp/one.png", "color": "blue"}).render()

    assert captured["series_index"] == [0, 1, 2, 3, 4]
    assert captured["series_values"] == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert captured["kwargs"] == {"color": "blue"}
    assert axes.title == "one-d"
    assert axes.ylabel == ""
    assert plt_obj._figure.size_inches == (10.0, 6.0)
    assert plt_obj._figure.dpi == 150.0
    assert plt_obj.savefig_calls == ["/tmp/one.png"]


def test_lineplot_render_2d_builds_dataframe_with_iso_time(monkeypatch: pytest.MonkeyPatch) -> None:
    axes = _FakeAxes()
    plt_obj = _FakePlt(axes)

    monkeypatch.setattr(lineplot_module, "plt", plt_obj)

    captured: dict[str, object] = {}

    def _fake_plot(self: pd.DataFrame, ax=None, **kwargs):  # type: ignore[no-untyped-def]
        captured["index"] = list(self.index)
        captured["columns"] = list(self.columns)
        captured["kwargs"] = kwargs
        return ax

    monkeypatch.setattr(pd.DataFrame, "plot", _fake_plot)

    time_coord = _FakeCoord(
        "time",
        [10, 20, 30],
        is_time=True,
        datetime_values=[
            np.datetime64("2020-01-01"),
            np.datetime64("2020-01-02"),
            np.datetime64("2020-01-03"),
        ],
    )
    lat_coord = _FakeCoord("latitude", [-10.0, 10.0])
    field = _FakeField(
        shape=(3, 2),
        coords={"time": time_coord, "latitude": lat_coord},
        array=np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]),
        units="m s-1",
    )

    LinePlot(field, options={"color": "red", "linewidth": 2}).render()

    assert plt_obj.savefig_calls == []

    assert captured["index"] == [
        pd.Timestamp("2020-01-01"),
        pd.Timestamp("2020-01-02"),
        pd.Timestamp("2020-01-03"),
    ]
    assert captured["columns"] == ["latitude=-10.0", "latitude=10.0"]
    assert captured["kwargs"] == {"color": "red", "linewidth": 2}

    assert axes.xlabel == "time"
    assert axes.ylabel == "m s-1"
    assert axes.title == "air_temperature"


def test_lineplot_honors_custom_figure_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    axes = _FakeAxes()
    plt_obj = _FakePlt(axes)

    monkeypatch.setattr(lineplot_module, "plt", plt_obj)
    monkeypatch.setattr(pd.Series, "plot", lambda self, ax=None, **kwargs: ax)

    field = _FakeField(
        shape=(3,),
        coords={"x": _FakeCoord("x", [0, 1, 2])},
        array=np.array([1.0, 2.0, 3.0]),
    )

    LinePlot(
        field,
        options={"figure_width": 12, "figure_height": 7, "figure_dpi": 200},
    ).render()

    assert plt_obj._figure.size_inches == (12.0, 7.0)
    assert plt_obj._figure.dpi == 200.0
