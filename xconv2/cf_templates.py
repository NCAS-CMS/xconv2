# -------------------------------------------------------------------------------------------
# These module includes the templates and functions which emit text
# that is executed in the worker process. Some of the code here is 
# signalling back to the GUI, and this is marked with a special
# omit4save comment to indicate that it should not be included if the code
# is being saved for later execution.
# -------------------------------------------------------------------------------------------

import textwrap

# Emit list[str] so GUI transport and tests use a stable, serializable contract.
field_list = textwrap.dedent(
    """
    fields = field_info(f)
    """
).lstrip()

# Shared collapse options for GUI selection and future worker command expansion.
collapse_methods = (
    'mean',
    'minimum',
    'maximum',
    'root_mean_square',
    'standard_deviation',
    'integral',
    'maximum_absolute_value',
    'minimum_absolute_value',
    'mean_absolute_value',
    'mean_of_upper_decile',
    'mid_range',
    'median',
    'range',
    'sample_size',
    'sum',
    'sum_of_squares',
    'sum_of_weights',
    'sum_of_weights2',
    'variance',
)

map_projections = {
    'cyl': 'cylindrical',
    'npstere': 'north polar stereographic',  # lon0
    'spstere': 'south polar stereographic',  # lon0
    'ortho': 'orthographic',  #lat0, lon0
    'merc': 'mercator', #lon0
    'moll': 'mollweide', #lon0
    'robin': 'robinson', #lon0
    'lcc': 'lambert conformal conic', #lon0 
    'rotated': 'rotated pole',
    'UKCP': 'UK Climate Projections',
    'OSGB': 'UK Ordnance Survey',
    'EuroPP': 'European Polar',
}
map_resolution_options = (
    '110m',
    '50m',
    '10m',
)
use_lon_0 = ('npstere', 'spstere', 'ortho', 'merc', 'moll', 'robin', 'lcc')

def coordinate_list(index: int) -> str:
    """Generate worker code that emits 1D dimension-coordinate values for a field."""
    return textwrap.dedent(
        f"""
        _cfview_field_index = {index}
        fld = f[{index}]
        fld = fld.squeeze() # make it easier for the GUI to handle coordinates with length 1
        coords = coordinate_info(fld)
        send_to_gui('COORD', coords) #omit4save
        """
    ).lstrip()


def unary_xy_field_operation(index: int, operation: str) -> str:
    """Generate worker code for unary XY field operation that appends derived field metadata."""
    return textwrap.dedent(
        f"""
        _cfview_field_index = {index}
        _cfview_operation = {operation!r}
        metadata_rows = append_unary_xy_field_operation(f, _cfview_field_index, _cfview_operation)
        send_to_gui('METADATA_APPEND', metadata_rows) #omit4save
        _cfview_added_count = len(metadata_rows)
        _cfview_first_id = metadata_rows[0].get('identity', 'unknown') if metadata_rows else 'unknown'
        send_to_gui(
            f"STATUS:Added {{_cfview_added_count}} field(s) via {{_cfview_operation}}; first: {{_cfview_first_id}}"
        ) #omit4save
        """
    ).lstrip()


def filter_field_operation(index: int, config: dict[str, object]) -> str:
    """Generate worker code for configurable field filtering and metadata append."""
    return textwrap.dedent(
        f"""
        _cfview_field_index = {index}
        _cfview_filter_config = {config!r}
        metadata_rows = append_filter_field_operation(f, _cfview_field_index, _cfview_filter_config)
        send_to_gui('METADATA_APPEND', metadata_rows) #omit4save
        _cfview_added_count = len(metadata_rows)
        _cfview_first_id = metadata_rows[0].get('identity', 'unknown') if metadata_rows else 'unknown'
        _cfview_method = str(_cfview_filter_config.get('method', 'unknown'))
        send_to_gui(
            f"STATUS:Added {{_cfview_added_count}} field(s) via filter ({{_cfview_method}}); first: {{_cfview_first_id}}"
        ) #omit4save
        """
    ).lstrip()


def filter_axes_for_field(index: int) -> str:
    """Generate worker code that reports valid filter axes for one field."""
    return textwrap.dedent(
        f"""
        _cfview_field_index = {index}
        _cfview_filter_axes = available_filter_axes(f[_cfview_field_index])
        send_to_gui('FILTER_AXES', _cfview_filter_axes) #omit4save
        """
    ).lstrip()


def binary_field_operation(
    index_a: int,
    index_b: int,
    operation: str,
    source_files: list[str] | None = None,
) -> str:
    """Generate worker code for binary field operations that append derived metadata."""
    return textwrap.dedent(
        f"""
        _cfview_field_index_a = {index_a}
        _cfview_field_index_b = {index_b}
        _cfview_operation = {operation!r}
        _cfview_source_files = {source_files or []!r}
        metadata_rows = append_binary_field_operation(
            f,
            _cfview_field_index_a,
            _cfview_field_index_b,
            _cfview_operation,
            source_files=_cfview_source_files,
        )
        send_to_gui('METADATA_APPEND', metadata_rows) #omit4save
        _cfview_added_count = len(metadata_rows)
        _cfview_first_id = metadata_rows[0].get('identity', 'unknown') if metadata_rows else 'unknown'
        send_to_gui(
            f"STATUS:Added {{_cfview_added_count}} field(s) via {{_cfview_operation}}; first: {{_cfview_first_id}}"
        ) #omit4save
        """
    ).lstrip()


def regrid_fields_operation(regrid_config_json: str) -> str:
    """Generate worker code that regrids selected field(s) from JSON config."""
    return textwrap.dedent(
        f"""
        _cfview_regrid_config_json = {regrid_config_json!r}
        regridder = XconvRegridder(_cfview_regrid_config_json)
        metadata_rows = regridder.do_regrid(f)
        send_to_gui('METADATA_APPEND', metadata_rows) #omit4save
        _cfview_added_count = len(metadata_rows)
        _cfview_target = 'unknown'
        try:
            import json as _json
            _cfview_target = str(_json.loads(_cfview_regrid_config_json).get('target', 'unknown'))
        except Exception:
            pass
        send_to_gui(
            f"STATUS:Added {{_cfview_added_count}} regridded field(s) (target={{_cfview_target}})."
        ) #omit4save
        """
    ).lstrip()


def add_dimension_coordinate_bounds(index: int) -> str:
    """Generate worker code that adds missing bounds to dimension coordinates."""
    return textwrap.dedent(
        f"""
        _cfview_field_index = {index}
        metadata_rows, _cfview_updated_coordinate_names = add_dimension_coordinate_bounds(f, _cfview_field_index)
        send_to_gui('METADATA', metadata_rows) #omit4save
        if _cfview_updated_coordinate_names:
            send_to_gui(
                f"STATUS:Added bounds to {{len(_cfview_updated_coordinate_names)}} dimension coordinate(s): {{', '.join(_cfview_updated_coordinate_names)}}"
            ) #omit4save
        else:
            send_to_gui("STATUS:No missing dimension-coordinate bounds were found.") #omit4save
        """
    ).lstrip()


def apply_selection_field_operation(
    index: int,
    selections: dict[str, tuple[object, object]],
    collapse_by_coord: dict[str, str],
) -> str:
    """Generate worker code that applies current selection and appends a new field."""
    return textwrap.dedent(
        f"""
        _cfview_field_index = {index}
        _cfview_selection_spec = {selections!r}
        _cfview_collapse_by_coord = {collapse_by_coord!r}
        metadata_rows = append_selection_field_operation(
            f,
            _cfview_field_index,
            _cfview_selection_spec,
            _cfview_collapse_by_coord,
        )
        send_to_gui('METADATA_APPEND', metadata_rows) #omit4save
        _cfview_added_count = len(metadata_rows)
        _cfview_first_id = metadata_rows[0].get('identity', 'unknown') if metadata_rows else 'unknown'
        send_to_gui(
            f"STATUS:Added {{_cfview_added_count}} field(s) via apply_selection; first: {{_cfview_first_id}}"
        ) #omit4save
        """
    ).lstrip()


def remove_selected_fields(indices: list[int]) -> str:
    """Generate worker code that removes selected fields from the worker list."""
    return textwrap.dedent(
        f"""
        _cfview_remove_indices = {indices!r}
        _cfview_removed_count = remove_fields_by_index(f, _cfview_remove_indices)
        send_to_gui(f"STATUS:Removed {{_cfview_removed_count}} field(s).") #omit4save
        """
    ).lstrip()


def save_selected_fields_task(
    indices: list[int],
    destination: str,
    output_format: str,
    output_chunk_by_index: dict[int, str] | None = None,
) -> str:
    """Generate worker code that saves selected fields to disk."""
    return textwrap.dedent(
        f"""
        _cfview_save_indices = {indices!r}
        _cfview_destination = {destination!r}
        _cfview_output_format = {output_format!r}
        _cfview_output_chunk_by_index = {output_chunk_by_index or {}!r}
        _cfview_saved_count = save_selected_fields(
            f,
            _cfview_save_indices,
            _cfview_destination,
            _cfview_output_format,
            _cfview_output_chunk_by_index,
        )
        send_to_gui(
            f"STATUS:Saved {{_cfview_saved_count}} selected field(s) to {{_cfview_destination}} ({{_cfview_output_format}})"
        ) #omit4save
        """
    ).lstrip()


def plot_from_selection(
    selections: dict[str, tuple[object, object]],
    collapse_by_coord: dict[str, str],
    plot_kind: str,
    plot_options: dict[str, object] | None = None,
    plot_action: str = "plot",
    save_data_path: str | None = None,
) -> str:
    """Generate worker code for plotting based on GUI selections.

    This currently wires the API contract and emits status information.
    Plot rendering and collapse application will be expanded later.
    """
    if plot_kind not in {"lineplot", "contour", "vector"}:
        raise ValueError(f"Unsupported plot kind: {plot_kind}")
    if plot_action not in {"plot", "overplot"}:
        raise ValueError(f"Unsupported plot action: {plot_action}")

    prep_code = _pfld_from_selection_code(selections, collapse_by_coord)

    if plot_kind == "lineplot":
        plot_code = lineplot(options=plot_options, plot_action=plot_action)
    elif plot_kind == "contour":
        plot_code = contour(options=plot_options, plot_action=plot_action)
    elif plot_kind == "vector":
        plot_code = vector(options=plot_options, plot_action=plot_action)
    else:
        raise ValueError(f"Unsupported plot kind: {plot_kind}")

    data_save_code = _save_data_code(save_data_path) if save_data_path else ""
    parts = [prep_code, plot_code]
    if data_save_code:
        parts.append(data_save_code)
    return "\n".join(parts)


def build_vector_overplot_command(
    contour_field_index: int,
    vector_field_index: int,
    contour_selections: dict[str, tuple[object, object]],
    contour_collapse_by_coord: dict[str, str],
    contour_options: dict[str, object] | None,
    vector_selections: dict[str, tuple[object, object]],
    vector_collapse_by_coord: dict[str, str],
    vector_options: dict[str, object] | None,
) -> str:
    """Build worker code that redraws the base contour before a vector overplot."""
    contour_code = plot_from_selection(
        contour_selections,
        contour_collapse_by_coord,
        "contour",
        contour_options,
        plot_action="plot",
    )
    vector_code = textwrap.dedent(
        f"""
        vector_options = {vector_options!r}
        vector_plot_action = 'overplot'
        selection_spec = {vector_selections!r}
        collapse_by_coord = {vector_collapse_by_coord!r}
        _cfview_v_field_index = vector_options.get('v_field_index')
        if _cfview_v_field_index is None:
            raise ValueError('vector plot options must include v_field_index; open Vector Options first')
        fld_v = f[_cfview_v_field_index]
        _cfview_reference_pfld = globals().get('_cfview_contour_reference_pfld')
        if _cfview_reference_pfld is not None:
            pfld = subset_field_to_reference_xy_domain(fld, _cfview_reference_pfld)
            pfld_v = subset_field_to_reference_xy_domain(fld_v, _cfview_reference_pfld)
        else:
            pfld = get_data_for_plotting(fld, selection_spec, collapse_by_coord)
            pfld_v = get_data_for_plotting(fld_v, selection_spec, collapse_by_coord)
        mapset_options = {{
            'map_projection': vector_options.get('map_projection') if vector_options else None,
            'bbox': vector_options.get('bbox') if vector_options else None,
            'boundinglat': vector_options.get('boundinglat') if vector_options else None,
            'map_resolution': vector_options.get('map_resolution') if vector_options else None,
            'lat_0': vector_options.get('lat_0') if vector_options else None,
            'lon_0': vector_options.get('lon_0') if vector_options else None,
        }}
        run_vector_plot(
            pfld_u=pfld,
            pfld_v=pfld_v,
            options=vector_options,
            plot_action=vector_plot_action,
            mapset=mapset_options,
            selection_spec=selection_spec,
            collapse_by_coord=collapse_by_coord,
        )
        if vector_options and 'filename' in vector_options:  #omit4save
            send_to_gui(f"STATUS:Saved plot to {{vector_options['filename']}}")  #omit4save
        """
    ).lstrip()
    return "\n".join(
        [
            f"_cfview_contour_field_index = {contour_field_index}",
            "fld = f[_cfview_contour_field_index]",
            contour_code,
            "_cfview_contour_reference_pfld = pfld",
            f"_cfview_vector_field_index = {vector_field_index}",
            "fld = f[_cfview_vector_field_index]",
            vector_code,
        ]
    )


def save_data_from_selection(
    selections: dict[str, tuple[object, object]],
    collapse_by_coord: dict[str, str],
    save_data_path: str,
) -> str:
    """Generate worker code that saves selected data without rendering a plot."""
    prep_code = _pfld_from_selection_code(selections, collapse_by_coord)
    return "\n".join([prep_code, _save_data_code(save_data_path)])


def contour_range_from_selection(
    selections: dict[str, tuple[object, object]],
    collapse_by_coord: dict[str, str],
) -> str:
    """Generate worker code that computes contour range for current selection."""
    prep_code = _pfld_from_selection_code(selections, collapse_by_coord)
    range_code = textwrap.dedent(
        """
        range_min, range_max = contour_data_range(pfld)
        suggested_title = auto_contour_title(
            pfld=pfld,
            selection_spec=selection_spec,
            collapse_by_coord=collapse_by_coord,
        )
        send_to_gui('CONTOUR_RANGE', {'min': range_min, 'max': range_max, 'suggested_title': suggested_title}) #omit4save
        """
    ).lstrip()
    return "\n".join([prep_code, range_code])


def _pfld_from_selection_code(
    selections: dict[str, tuple[object, object]],
    collapse_by_coord: dict[str, str],
) -> str:
    """Build code snippet that derives pfld from selection and collapse state."""
    payload_code = textwrap.dedent(
        f"""
        selection_spec = {selections!r}
        collapse_by_coord = {collapse_by_coord!r}
        """
    ).lstrip()

    selection_code = textwrap.dedent(
        """
        pfld = get_data_for_plotting(fld, selection_spec, collapse_by_coord)
        """
    ).lstrip()

    return "\n".join([payload_code, selection_code])


def contour(options: dict[str, object] | None, plot_action: str) -> str:
    """Generate worker code that delegates contour rendering to API helpers."""
    payload_code = textwrap.dedent(
        f"""
        contour_options = {options!r}
        contour_plot_action = {plot_action!r}
        mapset_options = {{
            'map_projection': contour_options.get('map_projection') if contour_options else None,
            'bbox': contour_options.get('bbox') if contour_options else None,
            'boundinglat': contour_options.get('boundinglat') if contour_options else None,
            'map_resolution': contour_options.get('map_resolution') if contour_options else None,
            'lat_0': contour_options.get('lat_0') if contour_options else None,
            'lon_0': contour_options.get('lon_0') if contour_options else None,
        }}
        run_contour_plot(
            pfld=pfld,
            options=contour_options,
            plot_action=contour_plot_action,
            mapset=mapset_options,
            selection_spec=selection_spec,
            collapse_by_coord=collapse_by_coord,
        )
        if contour_options and 'filename' in contour_options:  #omit4save
            send_to_gui(f"STATUS:Saved plot to {{contour_options['filename']}}")  #omit4save
        """
    ).lstrip()
    return payload_code


def lineplot(options: dict[str, object] | None, plot_action: str) -> str:
    """Generate worker code that delegates line-plot rendering to API helpers."""
    payload_code = textwrap.dedent(
        f"""
        lineplot_options = {options!r}
        lineplot_action = {plot_action!r}
        run_line_plot(
            pfld=pfld,
            options=lineplot_options,
            plot_action=lineplot_action,
            selection_spec=selection_spec,
            collapse_by_coord=collapse_by_coord,
        )
        if lineplot_options and 'filename' in lineplot_options:  #omit4save
            send_to_gui(f"STATUS:Saved plot to {{lineplot_options['filename']}}")  #omit4save
        """
    ).lstrip()
    return payload_code


def vector(options: dict[str, object] | None, plot_action: str) -> str:
    """Generate worker code that delegates vector rendering to API helpers."""
    if not options or options.get("v_field_index") is None:
        raise ValueError("vector plot options must include v_field_index; open Vector Options first")
    payload_code = textwrap.dedent(
        f"""
        vector_options = {options!r}
        vector_plot_action = {plot_action!r}
        _cfview_v_field_index = vector_options.get('v_field_index')
        fld_v = f[_cfview_v_field_index]
        pfld_v = get_data_for_plotting(fld_v, selection_spec, collapse_by_coord)
        mapset_options = {{
            'map_projection': vector_options.get('map_projection') if vector_options else None,
            'bbox': vector_options.get('bbox') if vector_options else None,
            'boundinglat': vector_options.get('boundinglat') if vector_options else None,
            'map_resolution': vector_options.get('map_resolution') if vector_options else None,
            'lat_0': vector_options.get('lat_0') if vector_options else None,
            'lon_0': vector_options.get('lon_0') if vector_options else None,
        }}
        run_vector_plot(
            pfld_u=pfld,
            pfld_v=pfld_v,
            options=vector_options,
            plot_action=vector_plot_action,
            mapset=mapset_options,
            selection_spec=selection_spec,
            collapse_by_coord=collapse_by_coord,
        )
        if vector_options and 'filename' in vector_options:  #omit4save
            send_to_gui(f"STATUS:Saved plot to {{vector_options['filename']}}")  #omit4save
        """
    ).lstrip()
    return payload_code


def _save_data_code(save_data_path: str) -> str:
    """Generate worker code that persists selected data via cf.write."""
    payload_code = textwrap.dedent(
        f"""
        save_data_path = {save_data_path!r}
        save_selected_field_data(pfld, save_data_path)
        send_to_gui(f"STATUS:Saved data to {{save_data_path}}")  #omit4save
        """
    ).lstrip()
    return payload_code

