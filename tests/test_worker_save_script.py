from __future__ import annotations

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
