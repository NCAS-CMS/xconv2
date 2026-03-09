def cfdm_cell_method_to_string(cell_method: object, axis_names: dict) -> str:
    """Build CF-like cell_methods text."""
    cell_method = cell_method.copy()
    cell_method.set_axes(
        tuple(
            [
                axis_names.get(axis, axis)
                for axis in cell_method.get_axes(())
            ]
        )
    )
    return str(cell_method)

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

    # Get the new cell methods created by the collapse
    selection = []
    n = 0
    for cm in tuple(cell_methods.values())[::-1]:
        selection.append(cfdm_cell_method_to_string(cm, axis_names))
        n += len(cm.get_axes())
        if n >= len(collapse_by_coord):
            break

    cm_string = " ".join(selection[::-1])

    return cm_string
