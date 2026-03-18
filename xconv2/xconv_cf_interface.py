"""
Reusable worker-side helpers for CF data extraction.

These functions are imported into the worker execution namespace so generated
code snippets in ``cf_templates.py`` can call them directly.
"""

from __future__ import annotations

import cf
import cfplot as cfp
from matplotlib import pyplot as plt
from xconv2.cell_method_handler import cell_methods_string_from_field
from xconv2.lineplot import LinePlot
from xconv2.plot_layout_helpers import (
    annotation_text,
    apply_vertical_padding,
    estimate_layout_padding,
)


__all__ = [
    "field_info",
    "coordinate_info",
    "get_data_for_plotting",
    "save_selected_field_data",
    "annotation_text",
    "estimate_layout_padding",
    "apply_vertical_padding",
    "auto_contour_title",
    "run_contour_plot",
    "run_line_plot",
]



def field_info(fields: object) -> list[dict[str, object]]:
    """
    Serialize field metadata for GUI transport.

    Build compact, delimited string rows that include field identity, coordinate
    descriptions, optional cell metadata, and property mappings.

    Args:
        fields: Iterable of CF field-like objects.

    Returns:
        list[dict[str, object]]: Structured rows ready for worker-to-GUI payload transfer.
    """
    rows: list[dict[str, object]] = []
    for x in fields:
        id_ = f"{x.identity().strip()}{x.shape}"
        props = x.properties()
        info = str(x)
        rows.append(
            {
                "identity": id_,
                "detail": info,
                "properties": dict(props) if isinstance(props, dict) else props,
            }
        )

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
    for key, c in field.dimension_coordinates(todict=True).items():
        arr = getattr(c, "array", None)
        if arr is None:
            continue
        if len(arr) <= 1:
            continue
        vals = [str(x) for x in arr]
        coords.append((c.identity(default="unknown"), vals, str(getattr(c, "Units",""))))

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

    # Remove subspaced-down-to-size-1 axes from the collapse selection
    # (if they're there it doesn't upset the collapse call, but it
    # does make creating a sensible plot title tricker).
    for coord_name in selection_spec:
        if (coord_name in collapse_by_coord
            and pfld.dimension_coordinate(coord_name).size <= 1):
            del collapse_by_coord[coord_name]

    # Apply collapses based on GUI selections.
    #
    # Build up a collapse string, e.g. "time: mean", or "time: height:
    # mean", or "time: mean height: maximum", etc.
    #
    # Note: "time: height: mean" is not always the same as two
    #       separate consectutive collapses of "time: mean" and then
    #       "height: mean". It is presumed that when a user asks for a
    #       collapse over two axes that they mean this to the
    #       simulataneous collapse (i.e. "time: height: mean"), rather
    #       than the two seperate collapses.
    #
    # Note to selves: It would be nice to replace "time", "height",
    #                 etc. with domain axis keys "domainaxis0",
    #                 "domainaxis2", etc.
    if collapse_by_coord:
        instruction = []

        axes_methods = tuple(collapse_by_coord.items())
        previous_method = axes_methods[0][1]
        for axis, method in axes_methods:
            if method != previous_method:
                instruction.append(previous_method)

            instruction.append(f"{axis}:")
            previous_method = method

        instruction.append(axes_methods[-1][1])

        instruction = " ".join(instruction)

        try:
            # Try a weighted collapse
            pfld = pfld.collapse(instruction, weights=True)
        except ValueError:
            # Could find appropriate weights, so collapse un-weighted.
            pfld = pfld.collapse(instruction, weights=False)

    return pfld


def save_selected_field_data(field: object, filename: str) -> None:
    """Persist selected field data to disk using cf.write."""
    cf.write(field, filename)
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

    def _positive_float_option(key: str, default: float) -> float:
        raw = options.get(key, default)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return default
        if value <= 0:
            return default
        return value

    contour_title_fontsize = _positive_float_option("contour_title_fontsize", 10.5)
    page_title_fontsize = _positive_float_option("page_title_fontsize", 10.0)
    annotation_fontsize = _positive_float_option("annotation_fontsize", 8.0)

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

        # Keep prepass side-effect free for level configuration; levels are
        # applied once in the final render pass.
        cfp.con(pfld, **prepass_kwargs)

    annotation_text_value = annotation_text(
        annotation_display=annotation_display,
        annotation_properties=annotation_properties,
        annotation_free_text=annotation_free_text,
    )
    top_padding, bottom_padding = estimate_layout_padding(
        page_title=page_title,
        page_title_display=page_title_display,
        page_title_fontsize=page_title_fontsize,
        annotation_text=annotation_text_value,
        annotation_fontsize=annotation_fontsize,
        run_prepass=_run_contour_prepass,
    )
    top_padding += page_margin_top
    bottom_padding += page_margin_bottom

    # Force cf-plot into embedded mode for worker rendering. Using cf-plot's
    # file mode can trigger an external viewer command on some platforms.
    cfp.gopen(user_plot=1)

    _apply_levels()

    if hasattr(cfp, "setvars"):
        # Always pass viewer=None to prevent cfplot from spawning an external
        # image viewer (e.g. ImageMagick display) after gclose().
        cfp.setvars(title_fontsize=contour_title_fontsize, viewer=None)

    cfp.con(pfld, **contour_kwargs)
    
    mycanvas = plt.gcf()
    if top_padding > 0 or bottom_padding > 0:
        # Reserve headroom for page title and bottom annotations even when
        # axes are not subplot-managed.
        apply_vertical_padding(mycanvas, top_padding, bottom_padding)

    if page_title_display and page_title:
        mycanvas.suptitle(str(page_title), y=0.995, fontsize=page_title_fontsize)

    if annotation_text_value:
        mycanvas.text(
            0.5,
            0.02,
            annotation_text_value,
            ha="center",
            va="bottom",
            fontsize=annotation_fontsize,
        )

    if filename is not None:
        mycanvas.savefig(str(filename))
        plt.close(mycanvas)

def run_line_plot(
    pfld: object,
    options: dict[str, object] | None,
    selection_spec: dict[str, tuple[object, object]] | None = None,
    collapse_by_coord: dict[str, str] | None = None,
) -> None:
    """Render line plots via the dedicated LinePlot helper class."""
    _ = (selection_spec, collapse_by_coord)
    plotter = LinePlot(pfld=pfld, options=options, 
                       collapse_by_coord=collapse_by_coord)
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
        collapse_title = cell_methods_string_from_field(
            pfld, collapse_by_coord
        ).strip()
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
