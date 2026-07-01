"""Plot helper operations extracted from CFVMain."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


def build_plot_context(
    host: object,
    *,
    parse_coordinate_subspace_commands_fn: Callable[[str], dict[str, tuple[object, object]]],
) -> tuple[dict[str, tuple[object, object]], dict[str, str], str] | None:
    """Collect current selections/collapse state and infer plot type."""
    if getattr(host, "selection_mode", "single") == "multi":
        selected_field_indices = list(getattr(host, "_selected_field_indices", []))
        if len(selected_field_indices) > 1:
            host._show_status_message(
                "Multi-field plotting is not enabled yet. Keep fields selected for upcoming Field Ops.",
                is_error=True,
            )
            return None

        command_text = host._coordinate_subspace_command_text()
        if not command_text:
            host._show_status_message(
                "Enter coordinate bounds commands before requesting a plot.",
                is_error=True,
            )
            return None

        try:
            selections = parse_coordinate_subspace_commands_fn(command_text)
        except ValueError as exc:
            host._show_status_message(str(exc), is_error=True)
            return None

        if not selections:
            host._show_status_message(
                "No coordinate bounds commands were parsed.",
                is_error=True,
            )
            return None

        return selections, {}, "contour"

    if not host.controls:
        return None

    selections: dict[str, tuple[object, object]] = {}
    collapse_by_coord: dict[str, str] = {}
    dims: list[int] = []

    for name, control in host.controls.items():
        values = control["values"]
        start_idx, end_idx = control["range_slider"].value()
        lo_idx = int(min(start_idx, end_idx))
        hi_idx = int(max(start_idx, end_idx))
        is_singleton = (hi_idx - lo_idx) <= 1

        if is_singleton:
            if lo_idx == 0:
                singleton_idx = lo_idx
            elif hi_idx == (len(values) - 1):
                singleton_idx = hi_idx
            else:
                singleton_idx = lo_idx
            lo = values[singleton_idx]
            hi = values[singleton_idx]
        else:
            lo = values[lo_idx]
            hi = values[hi_idx]
        selections[name] = (lo, hi)

        collapse_method = host.selected_collapse_methods.get(name)
        if collapse_method:
            collapse_by_coord[name] = collapse_method
            dims.append(1)
        else:
            dims.append(1 if is_singleton else 2)

    varying_dims = sum(1 for dim in dims if dim != 1)
    available_kinds = getattr(host, "available_plot_kinds", [])
    selected_kind = getattr(host, "selected_plot_kind", None)
    selected_action = getattr(host, "selected_plot_action", "plot")

    if varying_dims == 0:
        plot_kind = "collapsed"
    elif varying_dims == 3 and selected_action == "animation":
        # Animation mode treats 3D selections as contour slices over one varying axis.
        plot_kind = "contour"
    elif varying_dims > 2:
        plot_kind = "unsupported"
    elif isinstance(selected_kind, str) and selected_kind in available_kinds:
        plot_kind = selected_kind
    elif varying_dims == 1:
        plot_kind = "lineplot"
    elif varying_dims == 2:
        plot_kind = "contour"
    else:
        plot_kind = "unsupported"

    return selections, collapse_by_coord, plot_kind


def request_plot_task(
    host: object,
    *,
    save_code_path: str | None,
    save_plot_path: str | None,
    save_data_path: str | None,
    emit_image_override: bool | None,
    save_data_from_selection_fn: Callable[[dict[str, tuple[object, object]], dict[str, str], str], str],
    plot_from_selection_fn: Callable[..., str],
    build_vector_overplot_command_fn: Callable[..., str],
) -> None:
    """Build and send a plot/data task with optional save targets."""
    context = host._build_plot_context()
    if context is None:
        logger.info("PLOT_DIAG gui_plot_skip reason=no_controls")
        return
    selections, collapse_by_coord, plot_kind = context

    plot_action = getattr(host, "selected_plot_action", "plot")
    if plot_action not in {"plot", "overplot", "animation"}:
        plot_action = "plot"

    template_plot_action = plot_action

    if plot_kind in {"collapsed", "unsupported"}:
        if plot_action == "animation":
            host._show_status_message(
                "Animation requires exactly 3 varying dimensions in the coordinate selection.",
                is_error=True,
            )
        logger.info(
            "PLOT_DIAG gui_plot_skip reason=dimensionality kind=%s coords=%d collapses=%d",
            plot_kind,
            len(selections),
            len(collapse_by_coord),
        )
        return

    field_index = host._selected_field_index_for_operation("Plot")
    if field_index is None:
        return

    selected_item = host.field_list_widget.item(field_index)
    selected_field_label = (
        host._field_identity_from_item(selected_item)
        if selected_item is not None
        else f"field[{field_index}]"
    )

    save_target = None
    if save_code_path:
        save_target = str(Path(save_code_path).expanduser())

    save_plot_target = str(Path(save_plot_path).expanduser()) if save_plot_path else None
    save_data_target = str(Path(save_data_path).expanduser()) if save_data_path else None

    if save_data_target and not save_plot_target:
        if save_code_path:
            host._show_status_message(
                "Save Code + Save Data requires a plot target. Use Save All.",
                is_error=True,
            )
            return
        cmd = save_data_from_selection_fn(selections, collapse_by_coord, save_data_target)
    else:
        plot_options = dict(host.plot_options_by_kind.get(plot_kind, {}))
        if plot_kind == "contour":
            plot_options.setdefault("contour_title_fontsize", host._contour_title_fontsize())
            plot_options.setdefault("page_title_fontsize", host._page_title_fontsize())
            plot_options.setdefault("annotation_fontsize", host._annotation_fontsize())

        contour_context: dict[str, object] | None = None
        if plot_kind == "contour":
            contour_context = {
                "field_index": int(field_index),
                "selections": dict(selections),
                "collapse_by_coord": dict(collapse_by_coord),
                "plot_options": dict(plot_options),
            }

        if plot_kind == "vector" and plot_options.get("v_field_index") is None:
            host._show_status_message(
                "Open Vector Options to assign U and V fields before plotting.",
                is_error=False,
            )
            host._show_vector_options_dialog(field_index)
            return

        if save_plot_target:
            plot_options["filename"] = save_plot_target
        elif not plot_options:
            plot_options = None

        try:
            cmd = plot_from_selection_fn(
                selections,
                collapse_by_coord,
                plot_kind,
                plot_options,
                plot_action=template_plot_action,
                save_data_path=save_data_target,
            )
        except (ValueError, NotImplementedError) as exc:
            host._show_status_message(f"Plot request unavailable: {exc}", is_error=True)
            logger.warning("Plot template unavailable for kind=%s: %s", plot_kind, exc)
            return

        if contour_context is not None:
            stored_options = contour_context.get("plot_options")
            if isinstance(stored_options, dict):
                stored_options.pop("filename", None)
            host._last_contour_plot_context = contour_context

        if plot_kind == "vector" and plot_action == "overplot":
            last_contour = getattr(host, "_last_contour_plot_context", None)
            if isinstance(last_contour, dict):
                contour_field_index = last_contour.get("field_index")
                contour_selections = last_contour.get("selections")
                contour_collapse_by_coord = last_contour.get("collapse_by_coord")
                contour_options = last_contour.get("plot_options")
                if (
                    isinstance(contour_field_index, int)
                    and isinstance(contour_selections, dict)
                    and isinstance(contour_collapse_by_coord, dict)
                ):
                    cmd = build_vector_overplot_command_fn(
                        contour_field_index=contour_field_index,
                        vector_field_index=int(field_index),
                        contour_selections=contour_selections,
                        contour_collapse_by_coord=contour_collapse_by_coord,
                        contour_options=contour_options if isinstance(contour_options, dict) else None,
                        vector_selections=selections,
                        vector_collapse_by_coord=collapse_by_coord,
                        vector_options=plot_options,
                    )

    cmd = (
        f"_cfview_field_index = {field_index}\n"
        f"fld = f[{field_index}]\n"
        + cmd
    )

    animation_enabled = plot_action == "animation"

    if emit_image_override is not None:
        emit_image = emit_image_override
    elif animation_enabled:
        emit_image = False
    else:
        emit_image = save_plot_target is None and save_data_target is None

    logger.debug(
        "Requesting plot update kind=%s coords=%d collapses=%d save_code=%s save_plot=%s",
        plot_kind,
        len(selections),
        len(collapse_by_coord),
        bool(save_target),
        bool(save_plot_target),
    )
    logger.info(
        "PLOT_DIAG gui_plot_request pid=%s worker_pid=%s field_index=%s kind=%s emit_image=%s",
        os.getpid(),
        host.worker.processId(),
        field_index,
        plot_kind,
        emit_image,
    )

    if animation_enabled:
        loading_message = "Preparing animation..."
    elif save_target and save_plot_target and save_data_target:
        loading_message = "Saving plot, data, and code..."
    elif save_plot_target and save_data_target:
        loading_message = "Rendering and saving plot/data..."
    elif save_plot_target:
        loading_message = "Rendering and saving plot..."
    elif save_data_target:
        loading_message = "Saving selected data..."
    elif save_code_path:
        loading_message = "Rendering plot and saving code..."
    else:
        loading_message = "Rendering plot..."

    loading_message = f"{loading_message} Field {field_index}: {selected_field_label}"

    host._plot_request_in_flight = True
    host._plot_request_expects_image = emit_image
    host._suppress_stale_error_status = False
    host._set_plot_loading(True, loading_message)
    if animation_enabled:
        host._send_worker_task(
            cmd,
            save_code_path=save_target,
            emit_image=emit_image,
            animation_enabled=True,
        )
    else:
        host._send_worker_task(
            cmd,
            save_code_path=save_target,
            emit_image=emit_image,
        )
