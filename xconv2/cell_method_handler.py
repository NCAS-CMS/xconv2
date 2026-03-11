def cell_methods_string_from_field(pfld: object, collapse_by_coord: tuple) -> str:
    """Build a combined cell_methods string for all cell-method constructs in a field."""
    def _unique_domain_axis_identities(f, include_size=True):
        """Temporary function which will be replaced by completely
        replaced by
        f._unique_domain_axis_identities(include_size=True) at
        cf-python v3.20.0

        """
        import re

        key_to_name = {}
        name_to_keys = {}

        for key, value in f.domain_axes(todict=True).items():
            name_size = (
                f.constructs.domain_axis_identity(key),
                value.get_size(""),
            )
            if not include_size:
                name_size = name_size[0]

            name_to_keys.setdefault(name_size, []).append(key)
            key_to_name[key] = name_size

        if include_size:
            for (name, size), keys in name_to_keys.items():
                if len(keys) == 1:
                    key_to_name[keys[0]] = f"{name}({size})"
                else:
                    for key in keys:
                        found = re.findall(r"\d+$", key)[0]
                        key_to_name[key] = f"{name}{{{found}}}({size})"

        return key_to_name

    axis_names = _unique_domain_axis_identities(pfld, include_size=False)
    # at cf v3.20.0: axis_names = f._unique_domain_axis_identities(include_size=False)
    cell_methods = pfld.cell_methods(todict=True)

    # Get the new cell methods created by the collapse, changing their
    # axes to nice names with coordinate ranges.
    #
    # E.g. "domainaxis0" -> "time (1961-12-01 to 1990-12-01)"
    # E.g. "domainaxis1" -> "air_pressure (250 to 750 hPa)"
    selection = []
    n = 0
    for cm in tuple(cell_methods.values())[::-1]:
        # Change the domain axis identifiers to nice names
        # (e.g. "domainaxis0" -> "time")
        cm = cm.change_axes(axis_names)
        new_axes = []
        for axis in cm.get_axes():
            coord = pfld.coordinate(axis, default=None)
            if coord is not None:
                # Add the coordinate range to the nice axis name
                lower = coord.lower_bounds[0]
                upper = coord.upper_bounds[-1]

                if lower.Units.isreftime:
                    # Convert to ISO8601 strings
                    lower = str(lower.datetime_array[0])
                    upper = str(upper.datetime_array[0])
                    lower = lower.rstrip(":00").rstrip()
                    upper = upper.rstrip(":00").rstrip()
                    units = ""
                else:
                    # Convert to nicely formated numbers
                    try:
                        units = lower.Units.units
                    except Exception:
                        units = ""

                    lower = f"{lower.datum():g}"
                    upper = f"{upper.datum():g}"
                    if units is not None:
                        units = f" {units}"

                axis = f"{axis} ({lower} to {upper}{units})"

            new_axes.append(axis)

        cm.set_axes(new_axes)

        selection.append(str(cm))
        n += len(cm.get_axes())
        if n >= len(collapse_by_coord):
            break

    cm_string = " ".join(selection[::-1])

    return cm_string
