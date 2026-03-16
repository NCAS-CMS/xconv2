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
