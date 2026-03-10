"""
Reusable worker-side helpers for CF data extraction.

These functions are imported into the worker execution namespace so generated
code snippets in ``cf_templates.py`` can call them directly.
"""

from __future__ import annotations

from collections.abc import Callable

import cf
import cfplot as cfp
from matplotlib import pyplot as plt
from xconv2.cell_method_handler import cell_methods_string_from_field
from xconv2.lineplot import LinePlot


__all__ = [
    "field_info",
    "coordinate_info",
    "get_data_for_plotting",
    "annotation_text",
    "estimate_layout_padding",
    "apply_vertical_padding",
    "auto_contour_title",
    "run_contour_plot",
    "run_line_plot",
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
        else:
            info.append('No cell methods')

        cell_measures = x.cell_measures()
        if cell_measures:
            info.append(str(cell_measures))
        else:
            info.append('No cell measures')

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


def annotation_text(
    *,
    annotation_display: bool,
    annotation_properties: list[tuple[object, object]] | list[list[object]],
    annotation_free_text: str,
) -> str:
    """Build a compact two-line annotation string from selected properties."""
    if not annotation_display:
        return ""

    max_props = 3 if annotation_free_text else 4
    annotation_items = [f"{key}: {value}" for key, value in annotation_properties[:max_props]]

    entries: list[str] = []
    if annotation_free_text:
        entries.append(annotation_free_text)
    entries.extend(annotation_items)

    if not entries:
        return ""

    lines: list[str] = []
    for idx in range(0, len(entries), 2):
        lines.append(" | ".join(entries[idx : idx + 2]))

    return "\n".join(lines)


def estimate_layout_padding(
    *,
    page_title: str | None,
    page_title_display: bool,
    annotation_text: str,
    run_prepass: Callable[[], None],
) -> tuple[float, float]:
    """
    Estimate top and bottom padding required by page title and annotation text.

    A lightweight prepass is executed by ``run_prepass`` so axis extents can be
    measured before final rendering.
    """
    if (not page_title_display or not page_title) and not annotation_text:
        return (0.0, 0.0)

    fig = plt.gcf()
    canvas = getattr(fig, "canvas", None)
    if canvas is None or not hasattr(canvas, "draw") or not hasattr(canvas, "get_renderer"):
        return (0.0, 0.0)

    run_prepass()

    fig = plt.gcf()
    title_artist = None
    if page_title_display and page_title:
        title_artist = fig.suptitle(str(page_title), y=0.995, fontsize=10)

    annotation_artist = None
    if annotation_text:
        annotation_artist = fig.text(
            0.5,
            0.02,
            annotation_text,
            ha="center",
            va="bottom",
            fontsize=8,
        )

    canvas.draw()
    renderer = canvas.get_renderer()

    axes_top = 0.0
    axes_bottom = 1.0
    for ax in fig.axes:
        tight_bbox = ax.get_tightbbox(renderer)
        if tight_bbox is None:
            continue
        fig_bbox = tight_bbox.transformed(fig.transFigure.inverted())
        axes_top = max(axes_top, fig_bbox.y1)
        axes_bottom = min(axes_bottom, fig_bbox.y0)

    top_padding = 0.0
    if title_artist is not None:
        title_bbox = title_artist.get_window_extent(renderer).transformed(fig.transFigure.inverted())
        title_bottom = title_bbox.y0
        top_overlap = (axes_top + 0.01) - title_bottom
        if top_overlap > 0:
            top_padding = min(top_overlap + 0.01, 0.25)

    bottom_padding = 0.0
    if annotation_artist is not None:
        annotation_bbox = annotation_artist.get_window_extent(renderer).transformed(
            fig.transFigure.inverted()
        )
        annotation_top = annotation_bbox.y1
        bottom_overlap = (annotation_top + 0.01) - axes_bottom
        if bottom_overlap > 0:
            bottom_padding = min(bottom_overlap + 0.01, 0.25)

    if hasattr(plt, "close"):
        plt.close(fig)

    return (top_padding, bottom_padding)


def apply_vertical_padding(fig: object, top_pad: float, bottom_pad: float) -> None:
    """Resize and reposition all axes to reserve top and bottom headroom."""
    if top_pad <= 0 and bottom_pad <= 0:
        return

    axes = list(getattr(fig, "axes", ()))
    if not axes:
        return

    total_pad = top_pad + bottom_pad
    if total_pad <= 0:
        return

    bottom_fraction = bottom_pad / total_pad
    for ax in axes:
        pos = ax.get_position()
        # Apply per-axis clamping so short axes (e.g. colorbars) do not
        # collapse the entire layout adjustment.
        reduction = min(total_pad, max(pos.height - 0.01, 0.0))
        if reduction <= 0:
            continue

        bottom_reduction = reduction * bottom_fraction
        new_y0 = pos.y0 + bottom_reduction
        new_height = pos.height - reduction
        ax.set_position([pos.x0, new_y0, pos.width, new_height])


def run_contour_plot(
    pfld: object,
    options: dict[str, object] | None,
    selection_spec: dict[str, tuple[object, object]] | None = None,
    collapse_by_coord: dict[str, str] | None = None,
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
    selection_spec = selection_spec or {}
    collapse_by_coord = collapse_by_coord or {}

    filename = options.get("filename")
    title = options.get("title")
    page_title = options.get("page_title")
    page_title_display = bool(options.get("page_title_display", False))
    annotation_display = bool(options.get("annotation_display", False))
    annotation_properties = options.get("annotation_properties", [])
    annotation_free_text = str(options.get("annotation_free_text", "") or "").strip()
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
    page_margin_top = float(options.get("page_margin_top", 0.0) or 0.0)
    page_margin_bottom = float(options.get("page_margin_bottom", 0.0) or 0.0)

    page_margin_top = max(0.0, min(page_margin_top, 0.25))
    page_margin_bottom = max(0.0, min(page_margin_bottom, 0.25))

    if cscale:
        cfp.cscale(scale=cscale)
    else:
        cfp.cscale()

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

    if not title:
        title = auto_contour_title(
            pfld=pfld,
            selection_spec=selection_spec,
            collapse_by_coord=collapse_by_coord,
        )

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

    def _apply_levels() -> None:
        if contour_levels is not None:
            cfp.levs(manual=contour_levels)
        elif contour_min is not None and contour_max is not None and contour_step is not None:
            cfp.levs(min=contour_min, max=contour_max, step=contour_step)
        else:
            cfp.levs()

    def _run_contour_prepass() -> None:
        prepass_kwargs = dict(contour_kwargs)
        prepass_kwargs["fill"] = False
        prepass_kwargs["lines"] = False
        prepass_kwargs["line_labels"] = False
        prepass_kwargs["blockfill"] = False
        prepass_kwargs.pop("blockfill_fast", None)

        _apply_levels()
        cfp.con(pfld, **prepass_kwargs)

    annotation_text_value = annotation_text(
        annotation_display=annotation_display,
        annotation_properties=annotation_properties,
        annotation_free_text=annotation_free_text,
    )
    top_padding, bottom_padding = estimate_layout_padding(
        page_title=page_title,
        page_title_display=page_title_display,
        annotation_text=annotation_text_value,
        run_prepass=_run_contour_prepass,
    )
    top_padding += page_margin_top
    bottom_padding += page_margin_bottom

    # Force cf-plot into embedded mode for in-memory rendering. Without this,
    # cfp.con() may implicitly call gclose() and trigger an external viewer.
    if filename is None:
        cfp.gopen(user_plot=1)
    else:
        cfp.gopen(file=filename)

    _apply_levels()
    cfp.con(pfld, **contour_kwargs)
    
    mycanvas = plt.gcf()
    if top_padding > 0 or bottom_padding > 0:
        # Reserve headroom for page title and bottom annotations even when
        # axes are not subplot-managed.
        apply_vertical_padding(mycanvas, top_padding, bottom_padding)

    if page_title_display and page_title:
        mycanvas.suptitle(str(page_title), y=0.995, fontsize=10)

    if annotation_text_value:
        mycanvas.text(0.5, 0.02, annotation_text_value, ha="center", va="bottom", fontsize=8)

    if filename is not None:
        cfp.gclose()





    







def run_line_plot(
    pfld: object,
    options: dict[str, object] | None,
    selection_spec: dict[str, tuple[object, object]] | None = None,
    collapse_by_coord: dict[str, str] | None = None,
) -> None:
    """Render line plots via the dedicated LinePlot helper class."""
    _ = (selection_spec, collapse_by_coord)
    plotter = LinePlot(pfld=pfld, options=options)
    plotter.render()


def auto_contour_title(
    pfld: object,
    selection_spec: dict[str, tuple[object, object]] | None,
    collapse_by_coord: dict[str, str] | None,
) -> str:
    """Derive default contour title from collapse metadata or singleton selections."""
    selection_spec = selection_spec or {}
    collapse_by_coord = collapse_by_coord or {}

    if collapse_by_coord:
        collapse_title = cell_methods_string_from_field(pfld).strip()
        if collapse_title:
            return collapse_title

    selections: list[str] = []
    for coord_name, bounds in selection_spec.items():
        if not isinstance(bounds, (tuple, list)) or len(bounds) < 2:
            continue
        lo, hi = bounds[0], bounds[1]
        if lo == hi:
            selections.append(f"{coord_name}={lo}")

    return ", ".join(selections)
