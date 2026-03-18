from types import SimpleNamespace

from xconv2.ui.selection_controller import SelectionController


def test_format_slider_label_value_time_without_calendar() -> None:
    controller = SelectionController(SimpleNamespace())

    result = controller.format_slider_label_value(
        0,
        "days since 2000-01-01",
        delta=86400,
    )

    assert result == "2000-01-01"


class _DummyButton:
    def __init__(self) -> None:
        self.enabled = False

    def setEnabled(self, enabled: bool) -> None:
        self.enabled = enabled


class _DummyLabel:
    def __init__(self) -> None:
        self.text = ""

    def setText(self, text: str) -> None:
        self.text = text


class _DummyComboController:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str | None]] = []

    def set_plot_type_options(self, options: list[str], selected: str | None) -> None:
        self.calls.append((options, selected))


class _DummySlider:
    def __init__(self, values: tuple[int, int]) -> None:
        self._values = values

    def value(self) -> tuple[int, int]:
        return self._values


def test_refresh_plot_summary_enables_options_for_lineplot() -> None:
    host = SimpleNamespace()
    host.controls = {
        "time": {"range_slider": _DummySlider((0, 2))},
        "lat": {"range_slider": _DummySlider((0, 1))},
    }
    host.selected_collapse_methods = {}
    host.plot_summary_label = _DummyLabel()
    host.plot_button = _DummyButton()
    host.options_button = _DummyButton()
    host.save_code_button = _DummyButton()
    host.save_plot_button = _DummyButton()
    host.plot_view_controller = _DummyComboController()
    host.last_varying_dims = None
    host.available_plot_kinds = []
    host.selected_plot_kind = None

    controller = SelectionController(host)
    controller.refresh_plot_summary()

    assert host.selected_plot_kind == "lineplot"
    assert host.options_button.enabled is True
