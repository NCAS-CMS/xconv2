"""
Reusable worker-side helpers for CF data extraction.

These functions are imported into the worker execution namespace so generated
code snippets in ``cf_templates.py`` can call them directly.
"""

from __future__ import annotations

from collections.abc import Callable

import cf

__all__ = [
    "field_info",
    "coordinate_info",
    "get_data_for_plotting",
    "run_contour_plot",
]


def field_info(fields: object) -> list[str]:
    """Return serialized field metadata rows for GUI transport."""
    rows: list[str] = []
    for x in fields:
        id_ = f"{x.identity().strip()}{x.shape}"
        props = x.properties()
        info = [str(v) for _, v in x.coordinates().items()]
        info.append(str(x.cell_methods()))

        cm = getattr(x, "cell_measures", None)
        if callable(cm):
            info.append(str(cm()))
        elif cm is not None:
            info.append(str(cm))
        else:
            cm_legacy = getattr(x, "cellmeasures", None)
            info.append(str(cm_legacy) if cm_legacy is not None else "")

        nl = "\n"
        rows.append(f"{id_}\x1f{nl.join(info)}\x1f{str(props)}")

    return rows


def coordinate_info(field: object) -> list[tuple[str, list[str]]]:
    """Return 1D dimension-coordinate values for GUI slider construction."""
    coords: list[tuple[str, list[str]]] = []
    for key in field.dimension_coordinates():
        c = field.coordinate(key)
        arr = getattr(c, "array", None)
        if arr is None:
            continue
        if len(arr) <= 1:
            continue
        vals = [str(x) for x in arr]
        coords.append((c.identity(default="unknown"), vals))
    return coords


def get_data_for_plotting(
    field: object,
    selection_spec: dict[str, tuple[object, object]],
    collapse_by_coord: dict[str, str],
) -> object:
    """Subspace and collapse a field from GUI selections, returning ``pfld``."""

    def _parse_bound(value: object) -> object:
        if isinstance(value, (int, float)):
            return value

        text = str(value).strip()
        try:
            return int(text)
        except ValueError:
            try:
                return float(text)
            except ValueError:
                return text

    subspace_kwargs: dict[str, object] = {}
    for coord_name, bounds in selection_spec.items():
        lo, hi = bounds
        lo = _parse_bound(lo)
        hi = _parse_bound(hi)
        if lo == hi:
            subspace_kwargs[coord_name] = lo
        else:
            if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
                lo, hi = sorted((lo, hi))
            subspace_kwargs[coord_name] = cf.wi(lo, hi)

    pfld = field.subspace(**subspace_kwargs)

    # Apply collapses based on GUI selections
    for axis, method in collapse_by_coord.items():
        if method == "mean":
            pfld = pfld.collapse("mean", axes=axis, weights=False)
        else:
            pfld = pfld.collapse(method, axes=axis)

    return pfld


def run_contour_plot(
    pfld: object,
    cfp_module: object,
    plt_module: object,
    send_to_gui: Callable[..., object],
    options: dict[str, object] | None,
) -> None:
    """Apply contour options and render a contour plot for ``pfld``."""
    options = options or {}

    filename = options.get("filename")
    title = options.get("title")
    annotation_display = bool(options.get("annotation_display", False))
    annotation_properties = options.get("annotation_properties", [])
    cscale = options.get("cscale")

    fill = bool(options.get("fill", True))
    lines_enabled = bool(options.get("lines", False))
    line_labels = bool(options.get("line_labels", True))
    negative_linestyle = options.get("negative_linestyle", "solid")
    zero_thick = options.get("zero_thick", False)
    blockfill = bool(options.get("blockfill", False))
    blockfill_fast = options.get("blockfill_fast", None)

    mode = options.get("mode")
    levels = options.get("levels")
    auto_min = options.get("min")
    auto_max = options.get("max")
    intervals = options.get("intervals")

    if cscale:
        cfp_module.cscale(scale=cscale)
    else:
        cfp_module.cscale()

    if filename is not None:
        cfp_module.gopen(file=filename)
        send_to_gui(f"STATUS:Saved plot to {filename}")

    contour_levels = None
    contour_min = None
    contour_max = None
    contour_step = None

    if mode == "explicit" and levels:
        contour_levels = sorted(float(v) for v in levels)
    elif (
        mode == "auto"
        and auto_min is not None
        and auto_max is not None
        and intervals is not None
    ):
        contour_min = float(auto_min)
        contour_max = float(auto_max)
        interval_count = max(int(intervals), 1)
        contour_step = (contour_max - contour_min) / float(interval_count)

    contour_kwargs: dict[str, object] = {
        "fill": fill,
        "lines": lines_enabled,
        "line_labels": line_labels,
        "negative_linestyle": negative_linestyle,
        "zero_thick": zero_thick,
        "blockfill": blockfill,
    }
    if title:
        contour_kwargs["title"] = str(title)
    if blockfill_fast is not None:
        contour_kwargs["blockfill_fast"] = bool(blockfill_fast)

    if contour_levels is not None:
        cfp_module.levs(manual=contour_levels)
    elif contour_min is not None and contour_max is not None and contour_step is not None:
        cfp_module.levs(min=contour_min, max=contour_max, step=contour_step)
    else:
        cfp_module.levs()

    cfp_module.con(pfld, **contour_kwargs)

    if annotation_display and annotation_properties:
        annotation_items = [f"{key}: {value}" for key, value in annotation_properties[:4]]
        line_one = " | ".join(annotation_items[:2])
        line_two = " | ".join(annotation_items[2:4])
        annotation_text = "\n".join([x for x in (line_one, line_two) if x])
        plt_module.gcf().text(0.5, 0.02, annotation_text, ha="center", va="bottom", fontsize=8)

    if filename is not None:
        cfp_module.gclose()
