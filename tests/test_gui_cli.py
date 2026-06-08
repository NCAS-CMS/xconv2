from __future__ import annotations

from xconv2.gui import _launch_paths_from_argv, _open_paths_from_cli


class _DummyWindow:
    def __init__(self) -> None:
        self.base_window_title = "xconv2"
        self.calls: list[tuple[str, object]] = []

    def _set_file_open_mode(self, mode: str) -> None:
        self.calls.append(("set_mode", mode))

    def _set_window_title_for_file(self, path: str) -> None:
        self.calls.append(("set_title_for_file", path))

    def _record_recent_file(self, path: str) -> None:
        self.calls.append(("record_recent", path))

    def setWindowTitle(self, title: str) -> None:
        self.calls.append(("set_window_title", title))

    def on_file_selected(self, path: str) -> None:
        self.calls.append(("open_one", path))

    def on_files_selected(self, paths: list[str]) -> None:
        self.calls.append(("open_many", paths))


def test_launch_paths_from_argv_expands_user_home() -> None:
    paths = _launch_paths_from_argv(["~/demo.nc", "./a.nc"])

    assert paths[0].startswith("/")
    assert paths[0].endswith("demo.nc")
    assert paths[1] == "a.nc"


def test_open_paths_from_cli_single_file() -> None:
    window = _DummyWindow()

    _open_paths_from_cli(window, ["/tmp/one.nc"])

    assert window.calls == [
        ("set_title_for_file", "/tmp/one.nc"),
        ("record_recent", "/tmp/one.nc"),
        ("open_one", "/tmp/one.nc"),
    ]


def test_open_paths_from_cli_multiple_files_enables_multi_mode() -> None:
    window = _DummyWindow()

    _open_paths_from_cli(window, ["/tmp/one.nc", "/tmp/two.nc"])

    assert window.calls == [
        ("set_mode", "multi"),
        ("record_recent", "/tmp/one.nc"),
        ("record_recent", "/tmp/two.nc"),
        ("set_window_title", "xconv2: 2 files"),
        ("open_many", ["/tmp/one.nc", "/tmp/two.nc"]),
    ]
