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
collapse_methods = ["mean", "range", "max", "min"]


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
) -> str:
    """Generate worker code for plotting based on GUI selections.

    This currently wires the API contract and emits status information.
    Plot rendering and collapse application will be expanded later.
    """
    if plot_kind not in {"lineplot", "contour"}:
        raise ValueError(f"Unsupported plot kind: {plot_kind}")

    if plot_kind == "lineplot":
        raise NotImplementedError

    prep_code = _pfld_from_selection_code(selections, collapse_by_coord)

    if plot_kind == "lineplot":
        plot_code = lineplot(options=plot_options)
    elif plot_kind == "contour":
        plot_code = contour(options=plot_options)

    return "\n".join([prep_code, plot_code])


def contour_range_from_selection(
    selections: dict[str, tuple[object, object]],
    collapse_by_coord: dict[str, str],
) -> str:
    """Generate worker code that computes contour range for current selection."""
    prep_code = _pfld_from_selection_code(selections, collapse_by_coord)
    range_code = textwrap.dedent(
        """
        arr = np.ma.array(pfld.array).compressed()
        suggested_title = auto_contour_title(
            pfld=pfld,
            selection_spec=selection_spec,
            collapse_by_coord=collapse_by_coord,
        )
        if arr.size == 0:  #omit4save
            send_to_gui('CONTOUR_RANGE', {'min': 0.0, 'max': 0.0, 'suggested_title': suggested_title}) #omit4save
        else:
            send_to_gui('CONTOUR_RANGE', {'min': float(arr.min()), 'max': float(arr.max()), 'suggested_title': suggested_title}) #omit4save
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
        run_contour_plot(
            pfld=pfld,
            options=contour_options,
            selection_spec=selection_spec,
            collapse_by_coord=collapse_by_coord,
        )
        if contour_options and 'filename' in contour_options:  #omit4save
            send_to_gui(f"STATUS:Saved plot to {{contour_options['filename']}}")  #omit4save
        """
    ).lstrip()
    return payload_code


def lineplot(options: dict[str, object] | None) -> str:
    raise NotImplementedError

