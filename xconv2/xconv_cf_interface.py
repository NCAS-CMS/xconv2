"""
Reusable worker-side helpers for CF data extraction.

These functions are imported into the worker execution namespace so generated
code snippets in ``cf_templates.py`` can call them directly.
"""

from __future__ import annotations

import cf
import cfplot as cfp
from matplotlib import pyplot as plt

__all__ = [
    "field_info",
    "coordinate_info",
    "get_data_for_plotting",
    "run_contour_plot",
]


def field_info(fields: object) -> list[str]:
    """
    Serialize field metadata for GUI transport.

    Build compact, delimited string rows that include field identity, coordinate
    descriptions, optional cell metadata, and property mappings.

    Args:
        fields: Iterable of CF field-like objects.

    Returns:
        list[str]: Serialized rows ready for worker-to-GUI payload transfer.
    """
    rows: list[str] = []
    for x in fields:
        id_ = f"{x.identity().strip()}{x.shape}"
        props = x.properties()
        info = [str(v) for _, v in x.coordinates().items()]

        cell_methods = x.cell_methods()
        if cell_methods:
            info.append(str(cell_methods))

        cell_measures = x.cell_measures()
        if cell_measures:
            info.append(str(cell_measures))

        nl = "\n"
        rows.append(f"{id_}\x1f{nl.join(info)}\x1f{str(props)}")

    return rows


def coordinate_info(field: object) -> list[tuple[str, list[str]]]:
    """
    Extract plottable 1D dimension-coordinate values.

    Reads dimension coordinates from a field and returns only coordinates with
    more than one value so the GUI can build useful range sliders.

    Args:
        field: CF field-like object exposing dimension coordinate accessors.

    Returns:
        list[tuple[str, list[str]]]: Coordinate identity with serialized values.
    """
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
    """
    Build plot-ready data from selection and collapse directives.

    Parses selection bounds, applies subspace extraction, and then applies any
    requested collapses in sequence.

    Args:
        field: CF field-like object to subset and collapse.
        selection_spec: Mapping of coordinate name to low/high bound pair.
        collapse_by_coord: Mapping of coordinate name to collapse method.

    Returns:
        object: Subspaced and optionally collapsed field-like object.
    """

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
    options: dict[str, object] | None,
) -> None:
    """
    Render a contour plot for a prepared field.

    Applies contour styling, level configuration, optional annotations, and
    optional file output using cf-plot and matplotlib.

    Args:
        pfld: Plot-ready field-like object.
        options: Contour options mapping from GUI state or saved script.

    Returns:
        None
    """
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
        cfp.cscale(scale=cscale)
    else:
        cfp.cscale()

    if filename is not None:
        cfp.gopen(file=filename)

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
        cfp.levs(manual=contour_levels)
    elif contour_min is not None and contour_max is not None and contour_step is not None:
        cfp.levs(min=contour_min, max=contour_max, step=contour_step)
    else:
        cfp.levs()

    cfp.con(pfld, **contour_kwargs)

    if annotation_display and annotation_properties:
        annotation_items = [f"{key}: {value}" for key, value in annotation_properties[:4]]
        line_one = " | ".join(annotation_items[:2])
        line_two = " | ".join(annotation_items[2:4])
        annotation_text = "\n".join([x for x in (line_one, line_two) if x])
        plt.gcf().text(0.5, 0.02, annotation_text, ha="center", va="bottom", fontsize=8)

    if filename is not None:
        cfp.gclose()
