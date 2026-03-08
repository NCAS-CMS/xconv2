def cfdm_cell_method_to_string(cell_method: object) -> str:
    """Adapted from cfdm.CellMethod.__str__ to build CF-like cell_methods text."""
    string: list[str] = [f"{axis}:" for axis in cell_method.get_axes(())]
    string.append(cell_method.get_method(""))

    for portion in ("within", "where", "over"):
        qualifier = cell_method.get_qualifier(portion, None)
        if qualifier is not None:
            string.extend((portion, qualifier))

    interval = cell_method.get_qualifier("interval", ())
    comment = cell_method.get_qualifier("comment", None)

    if interval:
        wrapped = ["("]
        wrapped.append(" ".join(f"interval: {data}" for data in interval))
        if comment is not None:
            wrapped.append(f" comment: {comment}")
        wrapped.append(")")
        string.append("".join(wrapped))
    elif comment is not None:
        string.append(f"({comment})")

    return " ".join(string)


def cell_methods_string_from_field(field: object) -> str:
    """Build a combined cell_methods string for all cell-method constructs in a field."""
    cell_methods_obj = field.cell_methods()
    if cell_methods_obj is None:
        return ""

    if isinstance(cell_methods_obj, dict):
        items = cell_methods_obj.values()
    elif hasattr(cell_methods_obj, "values") and callable(cell_methods_obj.values):
        items = cell_methods_obj.values()
    elif isinstance(cell_methods_obj, (list, tuple)):
        items = cell_methods_obj
    else:
        items = [cell_methods_obj]

    rendered = [cfdm_cell_method_to_string(cm) for cm in items if cm is not None]
    return " ".join(x for x in rendered if x)

