"""CF helper submodules for metadata operations, regridding, and maths."""

from xconv2.cf_interface.plot_layout_helpers import (
    annotation_text,
    apply_vertical_padding,
    estimate_layout_padding,
)

from .maths import append_binary_field_operation, append_unary_xy_field_operation
from .metadata_operations import (
    add_dimension_coordinate_bounds,
    append_selection_field_operation,
    coordinate_info,
    field_info,
    get_data_for_plotting,
    parse_coordinate_subspace_commands,
    remove_fields_by_index,
    save_selected_field_data,
    save_selected_fields,
    subset_field_to_reference_xy_domain,
)
from .plotting import (
    auto_contour_title,
    contour_data_range,
    run_contour_plot,
    run_line_plot,
    run_vector_plot,
)
from .regridding import XconvRegridder, regrid_from_config

__all__ = [
    "field_info",
    "coordinate_info",
    "parse_coordinate_subspace_commands",
    "remove_fields_by_index",
    "save_selected_fields",
    "add_dimension_coordinate_bounds",
    "append_selection_field_operation",
    "append_unary_xy_field_operation",
    "append_binary_field_operation",
    "XconvRegridder",
    "regrid_from_config",
    "get_data_for_plotting",
    "save_selected_field_data",
    "subset_field_to_reference_xy_domain",
    "contour_data_range",
    "auto_contour_title",
    "run_contour_plot",
    "run_line_plot",
    "run_vector_plot",
    "annotation_text",
    "estimate_layout_padding",
    "apply_vertical_padding",
]
