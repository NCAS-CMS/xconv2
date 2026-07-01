"""Filtering helpers and field-operation wrappers."""

from __future__ import annotations

from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)

BASIC_MOVING_OPERATIONS = ("mean", "sum", "integral")
WINDOW_MODES = ("reflect", "constant", "nearest", "mirror", "wrap")
BASIC_WINDOWS = (
    "barthann",
    "bartlett",
    "blackman",
    "boxcar",
    "flattop",
    "hamming",
    "hann",
    "parzen",
    "taylor",
    "triang",
    "tukey",
)

WINDOW_DOCS_URL = "https://docs.scipy.org/doc/scipy/reference/signal.windows.html#module-scipy.signal.windows"
MOVING_WINDOW_DOCS_URL = (
    "https://ncas-cms.github.io/cf-python/method/cf.Field.moving_window.html#cf.Field.moving_window"
)
CONVOLUTION_DOCS_URL = (
    "https://ncas-cms.github.io/cf-python/method/cf.Field.convolution_filter.html#cf.Field.convolution_filter"
)


def _check_option(option: object, *, vocab: tuple[str, ...], docs_url: str) -> None:
    """Validate a vocabulary-controlled option string."""
    if not isinstance(option, str):
        raise ValueError(
            "Option must be a string specifying the option type. "
            f"Got {type(option)}. Supported values are {vocab}."
        )

    if option not in vocab:
        raise ValueError(
            f"Unsupported option '{option}'. Must be one of {vocab}. "
            f"See {docs_url} for details."
        )


def apply_window_to_field(field, window: str, size: int, axis: str):
    """Apply a scipy window via cf-python convolution_filter along one axis.

    Note this also updates the bounds of a relevant dimension coordinate construct
    to account for the width of the filter. This should not be confused with a
    moving window operation, which does not update the bounds, and does not update
    filter weights as they pass through axes.
    """
    _check_option(window, vocab=BASIC_WINDOWS, docs_url=WINDOW_DOCS_URL)
    from scipy.signal import get_window

    our_window = get_window(window, size)
    return field.convolution_filter(our_window, axis=axis)


def apply_moving_window_to_field(
    field,
    method: str,
    size: int,
    axis: str,
    weights: bool | None = None,
    mode: str | None = None,
):
    """Apply a moving-window reduction using cf-python moving_window.

    Note this does not update the bounds of a relevant dimension coordinate
    construct to account for the width of the filter. It should update cell
    methods to reflect the moving window operation.
    """
    _check_option(method, vocab=BASIC_MOVING_OPERATIONS, docs_url=MOVING_WINDOW_DOCS_URL)
    if mode:
        _check_option(mode, vocab=WINDOW_MODES, docs_url=MOVING_WINDOW_DOCS_URL)

    return field.moving_window(method, window_size=size, axis=axis, weights=weights, mode=mode)

def available_filter_axes(field) -> list[str]:
    """Return filterable CF axis labels with coordinate length > 1.

    Axes are reported in canonical order ``T, Z, Y, X`` and only included when
    a matching dimension/auxiliary coordinate exists with more than one value.
    """
    available: list[str] = []
    for axis in ("T", "Z", "Y", "X"):
        coord = field.dimension_coordinate(filter_by_axis=(axis,), default=None)
        if coord is None:
            coord = field.auxiliary_coordinate(filter_by_axis=(axis,), axis_mode="exact", default=None)
        if coord is None:
            continue

        values = getattr(coord, "array", None)
        if values is None:
            continue

        try:
            size = int(len(values))
        except Exception:
            continue

        if size > 1:
            available.append(axis)

    return available


def append_filter_field_operation(
    fields: list,
    field_index: int,
    config: dict[str, object],
) -> list[dict[str, object]]:
    """Create and append one filtered field from config; return metadata rows."""
    if field_index < 0 or field_index >= len(fields):
        raise IndexError(f"Field index out of range for filter operation: {field_index}")

    if not isinstance(config, dict):
        raise ValueError("Filter operation requires a configuration mapping.")

    method = str(config.get("method", "")).strip().lower()
    axis = str(config.get("axis", "")).strip().upper()

    size_raw = config.get("size")
    try:
        size = int(size_raw)
    except (TypeError, ValueError):
        raise ValueError(f"Filter size must be an integer. Got {size_raw!r}.") from None
    if size <= 0:
        raise ValueError("Filter size must be a positive integer.")

    source_field = fields[field_index]

    valid_axes = available_filter_axes(source_field)
    if not valid_axes:
        raise ValueError("No filterable axes are available (all candidate axes have size 1).")
    if axis not in valid_axes:
        raise ValueError(f"Filter axis must be one of {valid_axes}; received {axis!r}.")

    if method == "convolution":
        window = str(config.get("window", "")).strip().lower()
        filtered_field = apply_window_to_field(source_field, window=window, size=size, axis=axis)
    elif method == "moving_window":
        moving_method = str(config.get("moving_method", "")).strip().lower()
        mode_raw = config.get("mode")
        mode = str(mode_raw).strip().lower() if isinstance(mode_raw, str) and mode_raw.strip() else None
        weights_raw = config.get("weights")
        weights = bool(weights_raw) if isinstance(weights_raw, bool) else None
        filtered_field = apply_moving_window_to_field(
            source_field,
            method=moving_method,
            size=size,
            axis=axis,
            weights=weights,
            mode=mode,
        )
    else:
        raise ValueError(f"Unsupported filter method: {method!r}")

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    history = filtered_field.get_property("history", "")
    if history:
        history += "\n"
    history += (
        f"{filtered_field.identity()} derived from {source_field.identity()} by filter "
        f"method={method}, axis={axis}, size={size} ({now})."
    )
    filtered_field.set_property("history", history)

    fields.append(filtered_field)
    from .metadata_operations import field_info

    rows = field_info([filtered_field])
    logger.debug("Appended filtered field method=%s axis=%s size=%s", method, axis, size)
    return rows



