from __future__ import annotations

import numpy as np
import pandas as pd
from matplotlib import pyplot as plt

from xconv2.cell_method_handler import cell_methods_string_from_field
from xconv2.plot_layout_helpers import (
    annotation_text,
    apply_vertical_padding,
    estimate_layout_padding,
)


class LinePlot:
    """Line-plot renderer supporting 1D cf-plot and 2D pandas-backed plotting."""

    def __init__(
        self,
        pfld: object,
        options: dict[str, object] | None = None,
        collapse_by_coord: dict[str, str] | None = None,
    ) -> None:
        self.pfld = pfld
        self.default_options = options or {}
        self.collapse_by_coord = collapse_by_coord or {}
        ndims = self._varying_dims(self.pfld)
        if ndims not in (1, 2):
            raise ValueError(f"Line plots only support 1D or 2D fields, got {ndims}D")

    @staticmethod
    def _varying_dims(field: object) -> int:
        return sum(1 for n in field.shape if n > 1)

    @staticmethod
    def _lineplot_kwargs(options: dict[str, object]) -> dict[str, object]:
        kwargs: dict[str, object] = {}
        for key in (
            "title",
            "color",
            "linewidth",
            "linestyle",
            "marker",
            "markersize",
            "xlabel",
            "ylabel",
        ):
            value = options.get(key)
            if value is not None:
                kwargs[key] = value
        return kwargs

    @staticmethod
    def _figure_settings(options: dict[str, object]) -> tuple[float, float, float]:
        width = float(options.get("figure_width", 10.0) or 10.0)
        height = float(options.get("figure_height", 6.0) or 6.0)
        dpi = float(options.get("figure_dpi", 150.0) or 150.0)
        return width, height, dpi

    @staticmethod
    def _positive_float_option(options: dict[str, object], key: str, default: float) -> float:
        raw = options.get(key, default)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default

    @staticmethod
    def _x_values_for_coord(coord: object) -> object:
        if getattr(coord, "T", False):
            try:
                iso = np.vectorize(str, otypes=["U"])(coord.datetime_array)
                dt_index = pd.to_datetime(iso, errors="coerce")
                if not dt_index.isna().any():
                    return dt_index
                return list(iso)
            except Exception:
                return list(coord.array)
        return list(coord.array)

    def _make_series(self) -> tuple[pd.Series, object, object]:
        coords = self.pfld.dimension_coordinates(todict=True)
        varying = [(k, c) for k, c in coords.items() if getattr(c, "size", 0) > 1]
        if not varying:
            values = np.asarray(self.pfld.array).reshape(-1)
            return pd.Series(values), "index", None

        x_key, x_coord = next(
            ((k, c) for k, c in varying if getattr(c, "T", False)),
            varying[0],
        )
        x_values = self._x_values_for_coord(x_coord)
        y_values = np.asarray(self.pfld.array).reshape(-1)
        return pd.Series(y_values, index=x_values), x_key, x_coord

    def _make_dataframe(self) -> tuple[pd.DataFrame, object, object]:
        coords = self.pfld.dimension_coordinates(todict=True)
        varying = [(k, c) for k, c in coords.items() if getattr(c, "size", 0) > 1]
        if len(varying) < 2:
            raise ValueError("Need at least two varying coordinates for 2D line plotting")

        x_key, x_coord = next(
            ((k, c) for k, c in varying if getattr(c, "T", False)),
            varying[0],
        )
        series_key, series_coord = next((k, c) for k, c in varying if k != x_key)

        x_values = self._x_values_for_coord(x_coord)

        dim_keys = list(coords.keys())
        x_axis_idx = dim_keys.index(x_key)
        series_axis_idx = dim_keys.index(series_key)
        values_2d = np.asarray(self.pfld.array).squeeze()

        if values_2d.ndim != 2:
            raise ValueError(f"Expected a 2D array for line plotting, got {values_2d.ndim}D")

        # Arrange matrix as [x, series] for DataFrame(index=x, columns=series).
        if x_axis_idx == 1 and series_axis_idx == 0:
            values_2d = values_2d.T

        series_labels = [
            f"{series_coord.identity(default=str(series_key))}={value}"
            for value in series_coord.array
        ]

        frame = pd.DataFrame(values_2d, index=x_values, columns=series_labels)
        return frame, x_key, x_coord

    def render(self, options: dict[str, object] | None = None) -> None:
        """Render line plot using default options plus optional overrides."""
        merged_options = dict(self.default_options)
        if options:
            merged_options.update(options)

        filename = merged_options.get("filename")
        lineplot_kwargs = self._lineplot_kwargs(merged_options)
        fig_width, fig_height, fig_dpi = self._figure_settings(merged_options)

        fig = plt.gcf()
        fig.set_size_inches(fig_width, fig_height, forward=True)
        fig.set_dpi(fig_dpi)

        ndims = self._varying_dims(self.pfld)
        ax = plt.gca()

        pandas_kwargs: dict[str, object] = {}
        for key in ("color", "linewidth", "linestyle", "marker", "markersize"):
            if key in lineplot_kwargs:
                pandas_kwargs[key] = lineplot_kwargs[key]

        if ndims == 1:
            series, x_key, x_coord = self._make_series()
            series.plot(ax=ax, **pandas_kwargs)
        else:
            frame, x_key, x_coord = self._make_dataframe()
            frame.plot(ax=ax, **pandas_kwargs)

        legend_display = bool(merged_options.get("legend_display", True))
        legend_location = str(merged_options.get("legend_location", "best"))
        current_legend = ax.get_legend() if hasattr(ax, "get_legend") else None
        if legend_display:
            if hasattr(ax, "legend"):
                ax.legend(loc=legend_location)
        elif current_legend is not None and hasattr(current_legend, "remove"):
            current_legend.remove()

        if "title" in lineplot_kwargs:
            title_text = str(lineplot_kwargs["title"])
        else:
            title_text = self.pfld.identity()
            if isinstance(title_text, str) and "long_name=" in title_text:
                title_text = title_text.split("long_name=", 1)[1]
                title_text = str(title_text)

        # Add collapse ranges to to title
        title2 = cell_methods_string_from_field(
            self.pfld, self.collapse_by_coord
        )
        if title2:
            title_text += f"\n{title2}"

        lineplot_title_fontsize = self._positive_float_option(
            merged_options,
            "lineplot_title_fontsize",
            10.5,
        )
        ax.set_title(title_text, fontsize=lineplot_title_fontsize)

        if "xlabel" in lineplot_kwargs:
            ax.set_xlabel(str(lineplot_kwargs["xlabel"]))
        else:
            if x_coord is not None:
                ax.set_xlabel(x_coord.identity(default=str(x_key)))
        if "ylabel" in lineplot_kwargs:
            ax.set_ylabel(str(lineplot_kwargs["ylabel"]))
        else:
            ax.set_ylabel(str(getattr(self.pfld, "units", "")))

        page_title = merged_options.get("page_title")
        page_title_display = bool(merged_options.get("page_title_display", False))
        annotation_display = bool(merged_options.get("annotation_display", False))
        annotation_properties = merged_options.get("annotation_properties", [])
        annotation_free_text = str(merged_options.get("annotation_free_text", "") or "").strip()
        page_margin_top = float(merged_options.get("page_margin_top", 0.0) or 0.0)
        page_margin_bottom = float(merged_options.get("page_margin_bottom", 0.0) or 0.0)

        page_margin_top = max(0.0, min(page_margin_top, 0.25))
        page_margin_bottom = max(0.0, min(page_margin_bottom, 0.25))

        page_title_fontsize = self._positive_float_option(merged_options, "page_title_fontsize", 10.0)
        annotation_fontsize = self._positive_float_option(merged_options, "annotation_fontsize", 8.0)

        annotation_text_value = annotation_text(
            annotation_display=annotation_display,
            annotation_properties=annotation_properties,
            annotation_free_text=annotation_free_text,
        )
        top_padding, bottom_padding = estimate_layout_padding(
            page_title=str(page_title) if page_title is not None else None,
            page_title_display=page_title_display,
            page_title_fontsize=page_title_fontsize,
            annotation_text=annotation_text_value,
            annotation_fontsize=annotation_fontsize,
            run_prepass=lambda: None,
            close_after_draw=False,
        )
        top_padding += page_margin_top
        bottom_padding += page_margin_bottom

        if top_padding > 0 or bottom_padding > 0:
            apply_vertical_padding(fig, top_padding, bottom_padding)

        if page_title_display and page_title:
            fig.suptitle(str(page_title), y=0.995, fontsize=page_title_fontsize)

        if annotation_text_value:
            fig.text(
                0.5,
                0.02,
                annotation_text_value,
                ha="center",
                va="bottom",
                fontsize=annotation_fontsize,
            )

        if filename is not None:
            plt.savefig(str(filename))
            plt.close(fig)
