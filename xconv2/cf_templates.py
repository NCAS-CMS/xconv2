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
        if arr.size == 0:
            send_to_gui('CONTOUR_RANGE', {'min': 0.0, 'max': 0.0})
        else:
            send_to_gui('CONTOUR_RANGE', {'min': float(arr.min()), 'max': float(arr.max())})
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
        """
    ).lstrip()

    collapse_code = textwrap.dedent(
        """
        # Apply collapses based on GUI selections
        for axis, method in collapse_by_coord.items():
            if method == 'mean':
                pfld = pfld.collapse("mean", axes=axis, weights=False)
            else:
                pfld = pfld.collapse(method, axes=axis)
        """
    ).lstrip()

    return "\n".join([payload_code, selection_code, collapse_code])

def contour(options: dict[str, object] | None) -> str:
    filename = options.get("filename") if options else None
    title = options.get("title") if options else None
    annotation_display = options.get("annotation_display", False) if options else False
    annotation_properties = options.get("annotation_properties", []) if options else []
    cscale = options.get("cscale") if options else None
    fill = options.get("fill", True) if options else True
    lines_enabled = options.get("lines", True) if options else True
    line_labels = options.get("line_labels", True) if options else True
    negative_linestyle = options.get("negative_linestyle", "solid") if options else "solid"
    zero_thick = options.get("zero_thick", False) if options else False
    blockfill = options.get("blockfill", False) if options else False
    blockfill_fast = options.get("blockfill_fast", None) if options else None

    lines: list[str] = []

    if cscale:
        lines.append(f"cfp.cscale(scale={cscale!r})")
    else:
        lines.append("cfp.cscale()")

    if filename is not None:
        lines.append(f"cfp.gopen(file={filename!r})")
        lines.append(f"send_to_gui('STATUS:Saved plot to {filename}')")

    mode = options.get("mode") if options else None
    levels = options.get("levels") if options else None
    auto_min = options.get("min") if options else None
    auto_max = options.get("max") if options else None
    intervals = options.get("intervals") if options else None

    contour_options_code = textwrap.dedent(
        f"""
        contour_levels = None
        contour_min = None
        contour_max = None
        contour_step = None
        _contour_mode = {mode!r}
        _contour_levels = {levels!r}
        _contour_min = {auto_min!r}
        _contour_max = {auto_max!r}
        _contour_intervals = {intervals!r}

        if _contour_mode == 'explicit' and _contour_levels:
            contour_levels = sorted(float(v) for v in _contour_levels)
        elif (
            _contour_mode == 'auto'
            and _contour_min is not None
            and _contour_max is not None
            and _contour_intervals is not None
        ):
            contour_min = float(_contour_min)
            contour_max = float(_contour_max)
            _interval_count = max(int(_contour_intervals), 1)
            contour_step = (contour_max - contour_min) / float(_interval_count)

        contour_kwargs = {{
            'fill': bool({fill!r}),
            'lines': bool({lines_enabled!r}),
            'line_labels': bool({line_labels!r}),
            'negative_linestyle': {negative_linestyle!r},
            'zero_thick': {zero_thick!r},
            'blockfill': bool({blockfill!r}),
        }}
        _title = {title!r}
        if _title:
            contour_kwargs['title'] = str(_title)
        _blockfill_fast = {blockfill_fast!r}
        if _blockfill_fast is not None:
            contour_kwargs['blockfill_fast'] = bool(_blockfill_fast)

        _annotation_display = bool({annotation_display!r})
        _annotation_properties = {annotation_properties!r}
        """
    ).lstrip()
    lines.append(contour_options_code)

    contour_code = textwrap.dedent(
        """
        if contour_levels is not None:
            cfp.levs(manual=contour_levels)
        elif contour_min is not None and contour_max is not None and contour_step is not None:
            cfp.levs(min=contour_min, max=contour_max, step=contour_step)
        else:
            cfp.levs()

        cfp.con(pfld, **contour_kwargs)

        if _annotation_display and _annotation_properties:
            _annotation_items = [f"{key}: {value}" for key, value in _annotation_properties[:4]]
            _line_one = " | ".join(_annotation_items[:2])
            _line_two = " | ".join(_annotation_items[2:4])
            _annotation_text = "\\n".join([x for x in (_line_one, _line_two) if x])
            plt.gcf().text(0.5, 0.02, _annotation_text, ha='center', va='bottom', fontsize=8)
        """
    ).strip()
    lines.append(contour_code)

    if filename is not None:
        lines.append("cfp.gclose()")

    return "\n".join(lines) + "\n"
    

def lineplot(options: dict[str, object] | None) -> str:
   raise NotImplementedError

