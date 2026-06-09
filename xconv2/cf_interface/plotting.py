"""Plotting helpers for worker-side CF rendering."""

from __future__ import annotations

import logging

import cfplot as cfp
import numpy as np
from matplotlib import pyplot as plt

from xconv2.cell_method_handler import cell_methods_string_from_field
from xconv2.cf_interface.lineplot import LinePlot
from xconv2.cf_interface.plot_layout_helpers import (
    annotation_text,
    apply_vertical_padding,
    estimate_layout_padding,
)

logger = logging.getLogger(__name__)


def contour_data_range(pfld: object) -> tuple[float, float]:
    """
    Return contour min/max while tolerating backend indexing quirks.

    Primary path uses the field array directly so masked values are excluded.
    If that fails (for example with some h5netcdf/h5py indexing behaviors),
    return a safe default and let plotting continue.
    """
    try:
        arr = np.ma.array(pfld.array).compressed()
    except Exception as exc:
        logger.warning(
            "Falling back to default contour range due to backend read error: %s",
            exc,
        )
        return 0.0, 0.0

    if arr.size == 0:
        return 0.0, 0.0

    return float(arr.min()), float(arr.max())


def run_contour_plot(
    pfld: object,
    options: dict[str, object] | None,
    plot_action: str = "plot",
    mapset: dict[str, object] | None = None,
    selection_spec: dict[str, tuple[object, object]] | None = None,
    collapse_by_coord: dict[str, str] | None = None,
) -> None:
    """Render a contour plot for a prepared field."""
    options = options or {}
    mapset = mapset or {}
    selection_spec = selection_spec or {}
    collapse_by_coord = collapse_by_coord or {}
    normalized_action = "overplot" if plot_action == "overplot" else "plot"
    get_fignums = getattr(plt, "get_fignums", None)
    if callable(get_fignums):
        try:
            has_existing_figure = bool(get_fignums())
        except Exception:
            has_existing_figure = False
    else:
        has_existing_figure = False

    if normalized_action != "overplot" and has_existing_figure:
        plt.close("all")
        has_existing_figure = False

    if mapset.get("map_projection") and (normalized_action != "overplot" or not has_existing_figure):
        projection = mapset.get("map_projection")
        resolution = mapset.get("map_resolution", "110m")
        if projection in ["spstere", "npstere"]:
            cfp.mapset(
                proj=projection,
                resolution=resolution,
                boundinglat=mapset.get("boundinglat", -45 if projection == "spstere" else 45),
                lon_0=mapset.get("lon_0", 0.0),
            )
        else:
            bbox = mapset.get("bbox")
            if bbox and isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                lonmin, latmin, lonmax, latmax = tuple(bbox)
            else:
                lonmin, latmin, lonmax, latmax = None, None, None, None
            lon_0 = mapset.get("lon_0", 0.0)
            lat_0 = mapset.get("lat_0", 0.0)

            cfp.mapset(
                proj=projection,
                resolution=resolution,
                lonmin=lonmin,
                lonmax=lonmax,
                latmin=latmin,
                latmax=latmax,
                lon_0=lon_0,
                lat_0=lat_0,
            )

    annotation_display = bool(options.get("annotation_display", False))
    filename = options.get("filename")
    title = options.get("title")
    page_title = options.get("page_title")
    page_title_display = bool(options.get("page_title_display", False))
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
        prepass_kwargs.pop("title", None)

        try:
            cfp.con(pfld, **prepass_kwargs)
        except Exception as exc:
            logger.warning("Skipping contour prepass after cf-plot error: %s", exc)

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

    if normalized_action != "overplot" or not has_existing_figure:
        cfp.gopen(user_plot=1)

    _apply_levels()

    if hasattr(cfp, "setvars"):
        cfp.setvars(title_fontsize=contour_title_fontsize, viewer=None)

    map_title_fallback_used = False
    fallback_contour_title = str(contour_kwargs.get("title", "") or "")

    try:
        cfp.con(pfld, **contour_kwargs)
    except UnboundLocalError as exc:
        if "xpt" in str(exc) and "title" in contour_kwargs:
            map_title_fallback_used = True
            logger.warning(
                "CFP_TITLE_FALLBACK retrying contour render without title after cf-plot _map_title error: %s",
                exc,
            )
            fallback_kwargs = dict(contour_kwargs)
            fallback_kwargs.pop("title", None)
            cfp.con(pfld, **fallback_kwargs)

            if fallback_contour_title and not (page_title_display and page_title):
                page_title = fallback_contour_title
                page_title_display = True
        else:
            raise

    if map_title_fallback_used:
        logger.info("CFP_TITLE_FALLBACK_APPLIED title rendered as page title")
        send_to_gui_fn = globals().get("send_to_gui")
        if callable(send_to_gui_fn):
            send_to_gui_fn("STATUS:Map title fallback applied (cf-plot _map_title bug workaround)")

    mycanvas = plt.gcf()
    if top_padding > 0 or bottom_padding > 0:
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
    plot_action: str = "plot",
    selection_spec: dict[str, tuple[object, object]] | None = None,
    collapse_by_coord: dict[str, str] | None = None,
) -> None:
    """Render line plots via the dedicated LinePlot helper class."""
    _ = (selection_spec, collapse_by_coord)
    plotter = LinePlot(
        pfld=pfld,
        options=options,
        collapse_by_coord=collapse_by_coord,
        plot_action=plot_action,
    )
    plotter.render()


def run_vector_plot(
    pfld_u: object,
    pfld_v: object,
    options: dict[str, object] | None,
    plot_action: str = "plot",
    mapset: dict[str, object] | None = None,
    selection_spec: dict[str, tuple[object, object]] | None = None,
    collapse_by_coord: dict[str, str] | None = None,
) -> None:
    """Render a vector plot for two prepared U/V component fields using cfp.vect."""
    options = options or {}
    mapset = mapset or {}
    normalized_action = "overplot" if plot_action == "overplot" else "plot"

    get_fignums = getattr(plt, "get_fignums", None)
    has_existing_figure = False
    if callable(get_fignums):
        try:
            has_existing_figure = bool(get_fignums())
        except Exception:
            pass

    if normalized_action != "overplot" and has_existing_figure:
        plt.close("all")
        has_existing_figure = False

    if mapset.get("map_projection") and (normalized_action != "overplot" or not has_existing_figure):
        projection = mapset.get("map_projection")
        resolution = mapset.get("map_resolution", "110m")
        if projection in ["spstere", "npstere"]:
            cfp.mapset(
                proj=projection,
                resolution=resolution,
                boundinglat=mapset.get("boundinglat", -45 if projection == "spstere" else 45),
                lon_0=mapset.get("lon_0", 0.0),
            )
        else:
            bbox = mapset.get("bbox")
            if bbox and isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                lonmin, latmin, lonmax, latmax = tuple(bbox)
            else:
                lonmin, latmin, lonmax, latmax = None, None, None, None
            lon_0 = mapset.get("lon_0", 0.0)
            lat_0 = mapset.get("lat_0", 0.0)
            cfp.mapset(
                proj=projection,
                resolution=resolution,
                lonmin=lonmin,
                lonmax=lonmax,
                latmin=latmin,
                latmax=latmax,
                lon_0=lon_0,
                lat_0=lat_0,
            )

    filename = options.get("filename")
    title = str(options.get("title", "") or "")

    stride = int(options.get("stride", 1) or 1)
    if stride < 1:
        stride = 1

    vecs_kwargs: dict[str, object] = {"stride": stride}

    scale_raw = options.get("scale")
    if scale_raw is not None:
        try:
            scale_val = float(scale_raw)
            if scale_val > 0:
                vecs_kwargs["scale"] = scale_val
        except (TypeError, ValueError):
            pass

    key_length_raw = options.get("key_length")
    if key_length_raw is not None:
        try:
            key_len_val = float(key_length_raw)
            if key_len_val > 0:
                vecs_kwargs["key_length"] = key_len_val
        except (TypeError, ValueError):
            pass

    key_label = str(options.get("key_label", "") or "")
    if key_label:
        vecs_kwargs["key_label"] = key_label

    if title:
        vecs_kwargs["title"] = title

    if normalized_action != "overplot" or not has_existing_figure:
        cfp.gopen(user_plot=1)

    cfp.vect(pfld_u, pfld_v, **vecs_kwargs)

    mycanvas = plt.gcf()
    if filename is not None:
        mycanvas.savefig(str(filename))
        plt.close(mycanvas)


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
