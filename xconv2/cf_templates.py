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
        fld.squeeze(inplace=True) # make it easier for the GUI to handle coordinates with length 1
        coords = coordinate_info(fld)
        send_to_gui('COORD', coords) #omit4save
        """
    ).lstrip()


def plot_from_selection(
    selections: dict[str, tuple[object, object]],
    collapse_by_coord: dict[str, str],
    plot_kind: str,
    plot_options: dict[str, object] | None = None,
    save_data_path: str | None = None,
) -> str:
    """Generate worker code for plotting based on GUI selections.

    This currently wires the API contract and emits status information.
    Plot rendering and collapse application will be expanded later.
    """
    if plot_kind not in {"lineplot", "contour"}:
        raise ValueError(f"Unsupported plot kind: {plot_kind}")

    prep_code = _pfld_from_selection_code(selections, collapse_by_coord)

    if plot_kind == "lineplot":
        plot_code = lineplot(options=plot_options)
    elif plot_kind == "contour":
        plot_code = contour(options=plot_options)

    data_save_code = _save_data_code(save_data_path) if save_data_path else ""
    parts = [prep_code, plot_code]
    if data_save_code:
        parts.append(data_save_code)
    return "\n".join(parts)


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


def contour(options: dict[str, object] | None) -> str:
    """Generate worker code that delegates contour rendering to API helpers."""
    payload_code = textwrap.dedent(
        f"""
        contour_options = {options!r}
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
            mapset=mapset_options,
            selection_spec=selection_spec,
            collapse_by_coord=collapse_by_coord,
        )
        if contour_options and 'filename' in contour_options:  #omit4save
            send_to_gui(f"STATUS:Saved plot to {{contour_options['filename']}}")  #omit4save
        """
    ).lstrip()
    return payload_code


def lineplot(options: dict[str, object] | None) -> str:
    """Generate worker code that delegates line-plot rendering to API helpers."""
    payload_code = textwrap.dedent(
        f"""
        lineplot_options = {options!r}
        run_line_plot(
            pfld=pfld,
            options=lineplot_options,
            selection_spec=selection_spec,
            collapse_by_coord=collapse_by_coord,
        )
        if lineplot_options and 'filename' in lineplot_options:  #omit4save
            send_to_gui(f"STATUS:Saved plot to {{lineplot_options['filename']}}")  #omit4save
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

