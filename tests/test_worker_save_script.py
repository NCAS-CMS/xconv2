from __future__ import annotations

import sys
import types

import xconv2.worker as worker


def test_build_saved_plot_script_omits_gui_only_lines(monkeypatch) -> None:
    monkeypatch.setitem(worker.worker_globals, "_cfview_file_path", "/tmp/in.nc")
    monkeypatch.setitem(worker.worker_globals, "_cfview_field_index", 3)

    exec_code = "\n".join(
        [
            "selection_spec = {'time': ('1', '2')}",
            "send_to_gui('STATUS:noop')  #omit4save",
            "pfld = get_data_for_plotting(fld, selection_spec, {})",
            "if contour_options and 'filename' in contour_options:  #omit4save",
            "    send_to_gui('STATUS:Saved')  #omit4save",
        ]
    )

    script = worker._build_saved_plot_script(exec_code)

    assert "from xconv2.xconv_cf_interface import" not in script
    assert "send_to_gui" not in script
    assert "#omit4save" not in script
    assert "def get_data_for_plotting(" in script
    assert "def run_contour_plot(" not in script
    assert "selection_spec" in script
    assert "pfld = get_data_for_plotting" in script
    assert "f = cf.read('/tmp/in.nc')" in script
    assert "fld = f[3]" in script
    assert "plt.show(block=True)" in script


def test_saved_contour_script_executes_without_missing_inlined_helpers(monkeypatch) -> None:
    class _FakeField:
        def subspace(self, **_kwargs):
            return self

        def collapse(self, _method, axes=None, weights=None):
            _ = (axes, weights)
            return self

    class _FakeFigure:
        def __init__(self) -> None:
            self.axes = []

        def suptitle(self, *_args, **_kwargs):
            return None

        def text(self, *_args, **_kwargs):
            return None

    contour_calls = {"count": 0}
    fake_figure = _FakeFigure()

    fake_cf = types.ModuleType("cf")
    fake_cf.read = lambda _path: [_FakeField()]
    fake_cf.wi = lambda lo, hi: (lo, hi)

    fake_cfp = types.ModuleType("cfplot")
    fake_cfp.cscale = lambda *args, **kwargs: None
    fake_cfp.levs = lambda *args, **kwargs: None

    def _fake_con(*_args, **_kwargs):
        contour_calls["count"] += 1

    fake_cfp.con = _fake_con
    fake_cfp.gopen = lambda *args, **kwargs: None
    fake_cfp.gclose = lambda *args, **kwargs: None

    fake_plt = types.ModuleType("matplotlib.pyplot")
    fake_plt.gcf = lambda: fake_figure
    fake_plt.show = lambda *args, **kwargs: None
    fake_plt.close = lambda *_args, **_kwargs: None

    fake_matplotlib = types.ModuleType("matplotlib")
    fake_matplotlib.pyplot = fake_plt

    monkeypatch.setitem(sys.modules, "cf", fake_cf)
    monkeypatch.setitem(sys.modules, "cfplot", fake_cfp)
    monkeypatch.setitem(sys.modules, "matplotlib", fake_matplotlib)
    monkeypatch.setitem(sys.modules, "matplotlib.pyplot", fake_plt)

    monkeypatch.setitem(worker.worker_globals, "_cfview_file_path", "/tmp/in.nc")
    monkeypatch.setitem(worker.worker_globals, "_cfview_field_index", 0)

    exec_code = "\n".join(
        [
            "selection_spec = {'time': ('1', '1')}",
            "collapse_by_coord = {}",
            "pfld = get_data_for_plotting(fld, selection_spec, collapse_by_coord)",
            "contour_options = {'title': 'ok', 'page_title_display': False, 'annotation_display': False}",
            "run_contour_plot(pfld=pfld, options=contour_options, selection_spec=selection_spec, collapse_by_coord=collapse_by_coord)",
        ]
    )

    script = worker._build_saved_plot_script(exec_code)

    exec(script, {})

    assert contour_calls["count"] >= 1
