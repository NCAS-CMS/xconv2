"""Shared matplotlib layout helpers for page titles and annotations."""

from __future__ import annotations

from collections.abc import Callable

from matplotlib import pyplot as plt


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
    page_title_fontsize: float,
    annotation_text: str,
    annotation_fontsize: float,
    run_prepass: Callable[[], None],
    close_after_draw: bool = True,
) -> tuple[float, float]:
    """Estimate top and bottom padding required by title/annotation overlays."""
    if (not page_title_display or not page_title) and not annotation_text:
        return (0.0, 0.0)

    fig = plt.gcf()
    canvas = getattr(fig, "canvas", None)
    if canvas is None or not hasattr(canvas, "draw") or not hasattr(canvas, "get_renderer"):
        return (0.0, 0.0)

    run_prepass()

    fig = plt.gcf()
    canvas = getattr(fig, "canvas", None)
    if canvas is None or not hasattr(canvas, "draw") or not hasattr(canvas, "get_renderer"):
        return (0.0, 0.0)

    title_artist = None
    if page_title_display and page_title:
        title_artist = fig.suptitle(str(page_title), y=0.995, fontsize=page_title_fontsize)

    annotation_artist = None
    if annotation_text:
        annotation_artist = fig.text(
            0.5,
            0.02,
            annotation_text,
            ha="center",
            va="bottom",
            fontsize=annotation_fontsize,
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

    if close_after_draw and hasattr(plt, "close"):
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
        reduction = min(total_pad, max(pos.height - 0.01, 0.0))
        if reduction <= 0:
            continue

        bottom_reduction = reduction * bottom_fraction
        new_y0 = pos.y0 + bottom_reduction
        new_height = pos.height - reduction
        ax.set_position([pos.x0, new_y0, pos.width, new_height])