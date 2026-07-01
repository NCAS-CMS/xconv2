from __future__ import annotations

import cf
import cfplot

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

    assert "from xconv2.cf_interface import" not in script
    assert "send_to_gui" not in script
    assert "#omit4save" not in script
    assert "def get_data_for_plotting(" in script
    assert "def run_contour_plot(" not in script
    assert "selection_spec" in script
    assert "pfld = get_data_for_plotting" in script
    assert "f = cf.read('/tmp/in.nc')" in script
    assert "fld = f[3]" in script
    assert "plt.show(block=True)" in script


def test_saved_contour_script_executes_without_missing_inlined_helpers(monkeypatch, tmp_path) -> None:
    contour_calls = {"count": 0}

    def _fake_con(*_args, **_kwargs):
        contour_calls["count"] += 1

    sample_file = tmp_path / "in.nc"
    cf.write(cf.example_field(0), str(sample_file))

    monkeypatch.setattr(cfplot, "con", _fake_con)
    monkeypatch.setitem(worker.worker_globals, "_cfview_file_path", str(sample_file))
    monkeypatch.setitem(worker.worker_globals, "_cfview_field_index", 0)

    exec_code = "\n".join(
        [
            "selection_spec = {}",
            "collapse_by_coord = {}",
            "pfld = get_data_for_plotting(fld, selection_spec, collapse_by_coord)",
            "contour_options = {'title': 'ok', 'page_title_display': False, 'annotation_display': False}",
            "run_contour_plot(pfld=pfld, options=contour_options, plot_action='plot', selection_spec=selection_spec, collapse_by_coord=collapse_by_coord)",
        ]
    )

    script = worker._build_saved_plot_script(exec_code)

    exec(script, {})

    assert contour_calls["count"] >= 1


def test_build_saved_plot_script_inlines_lineplot_class_for_line_tasks(monkeypatch) -> None:
    monkeypatch.setitem(worker.worker_globals, "_cfview_file_path", "/tmp/in.nc")
    monkeypatch.setitem(worker.worker_globals, "_cfview_field_index", 0)

    exec_code = "\n".join(
        [
            "selection_spec = {'time': ('1', '2')}",
            "collapse_by_coord = {}",
            "pfld = get_data_for_plotting(fld, selection_spec, collapse_by_coord)",
            "lineplot_options = {'title': 'line'}",
            "run_line_plot(pfld=pfld, options=lineplot_options, plot_action='plot', selection_spec=selection_spec, collapse_by_coord=collapse_by_coord)",
        ]
    )

    script = worker._build_saved_plot_script(exec_code)

    assert "import numpy as np" in script
    assert "import pandas as pd" in script
    assert "class LinePlot:" in script
    assert "def run_line_plot(" in script
