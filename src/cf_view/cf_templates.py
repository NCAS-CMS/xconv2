# Get a list of fields in a file using their identity, things that look
# like <CF Field: air_pressure_at_mean_sea_level(time(30), latitude(721), longitude(1440)) Pa>
# and strip the gubbins off the front and back

import textwrap

# Emit list[str] so GUI transport and tests use a stable, serializable contract.
# FIXME: Expand this so it's more tutorial like and useful to readers of code
field_list = textwrap.dedent(
    """
    fields = []
    for x in f:
        id_ = f"{x.identity().strip()}{x.shape}"
        props = x.properties()
        info = [str(v) for _, v in x.coordinates().items()]
        info.append(str(x.cell_methods()))
        cm = getattr(x, "cell_measures", None)
        if callable(cm):
            info.append(str(cm()))
        elif cm is not None:
            info.append(str(cm))
        else:
            cm_legacy = getattr(x, "cellmeasures", None)
            info.append(str(cm_legacy) if cm_legacy is not None else "")
        nl = "\\n"
        fields.append(f"{id_}\x1f{nl.join(info)}\x1f{str(props)}")
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
        coords = []
        for key in fld.dimension_coordinates():
            c = fld.coordinate(key)
            arr = getattr(c, 'array', None)
            if arr is None:
                continue
            if len(arr) <= 1:
                continue
            vals = [str(x) for x in arr]
            coords.append((c.identity(default='unknown'), vals))
        send_to_gui('COORD', coords)
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
    
    if plot_kind == 'lineplot':
        raise NotImplementedError

    # First construct subspace operations from slider selections,
    # then apply any requested collapses, and finally request a plot.

    payload_code = textwrap.dedent(
        f"""
        selection_spec = {selections!r}
        collapse_by_coord = {collapse_by_coord!r}
        """
    ).lstrip()

    selection_code = textwrap.dedent(
        """
        def _parse_bound(value):
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

        subspace_kwargs = {}
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

        pfld = fld.subspace(**subspace_kwargs)
        """    ).lstrip()

    collapse_code  = textwrap.dedent(
        """
        # Apply collapses based on GUI selections
        for axis, method in collapse_by_coord.items():
            if method == 'mean':
                pfld = pfld.collapse("mean", axes=axis, weights=False)
            else:
                pfld = pfld.collapse(method, axes=axis)
        """
        ).lstrip()
    
    if plot_kind == "lineplot":
        plot_code = lineplot(options=plot_options)
    elif plot_kind == "contour":
        plot_code = contour(options=plot_options)
      
    return "\n".join([payload_code, selection_code, collapse_code, plot_code])

def contour(options: dict[str, object] | None) -> str:
    filename = options.get("filename") if options else None
    lines: list[str] = []

    if filename is not None:
        lines.append(f"cfp.gopen(file={filename!r})")
        lines.append(f"send_to_gui('STATUS:Saved plot to {filename}')")

    contour_code = textwrap.dedent(
        """
        cfp.con(pfld)
        """
    ).strip()
    lines.append(contour_code)

    if filename is not None:
        lines.append("cfp.gclose()")

    return "\n".join(lines) + "\n"
    

def lineplot(options: dict[str, object] | None) -> str:
   raise NotImplementedError

