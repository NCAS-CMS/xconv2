"""Maths and derived-field operations for worker-side CF helpers."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path

import cf

from .metadata_operations import coordinate_info, field_info

logger = logging.getLogger(__name__)

# Default kwargs for unary XY operations. These are applied automatically by
# worker-side field ops until UI controls for operation parameters are added.
_UNARY_XY_OPERATION_DEFAULT_KWARGS: dict[str, dict[str, object]] = {
    "grad": {"radius": "earth"},
    "laplacian": {"radius": "earth"},
}


def append_unary_xy_field_operation(
    fields: list,
    field_index: int,
    operation: str,
) -> list[dict[str, object]]:
    """Create derived XY field(s), append them, and return metadata rows."""

    if field_index < 0 or field_index >= len(fields):
        raise IndexError(f"Field index out of range for operation {operation!r}: {field_index}")

    fld = fields[field_index]
    has_x = (
        fld.dimension_coordinate(filter_by_axis=("X",), default=None) is not None
        or fld.auxiliary_coordinate(filter_by_axis=("X",), axis_mode="exact", default=None) is not None
    )
    has_y = (
        fld.dimension_coordinate(filter_by_axis=("Y",), default=None) is not None
        or fld.auxiliary_coordinate(filter_by_axis=("Y",), axis_mode="exact", default=None) is not None
    )

    if not (has_x and has_y):
        raise ValueError(f"{operation} requires a field with both X and Y axes.")

    normalized = operation.strip().lower()
    kwargs = dict(_UNARY_XY_OPERATION_DEFAULT_KWARGS.get(normalized, {}))
    if normalized in {"grad", "laplacian"} and fld.iscyclic("X"):
        logger.debug("Enabling x_wrap for %s operation on cyclic X coordinate", operation)
        kwargs["x_wrap"] = True
    if normalized == "grad":
        new_result = fld.grad_xy(**kwargs)
    elif normalized == "laplacian":
        new_result = fld.laplacian_xy(**kwargs)
    else:
        raise ValueError(f"Unsupported unary XY field operation: {operation!r}")

    if isinstance(new_result, cf.FieldList):
        new_fields = list(new_result)
    elif isinstance(new_result, (list, tuple)):
        new_fields = list(new_result)
    else:
        new_fields = [new_result]

    for new_field in new_fields:
        today = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        history = new_field.get_property("history", "")
        if history:
            history += "\n"
        history += f"{new_field.identity()} derived from {fld.identity()} by cf-python {cf.__version__} ({today})."
        new_field.set_property("history", history)
        logger.debug("Appending new field: %r (cyclic status %s)", new_field, new_field.iscyclic("X"))
        fields.append(new_field)

    return field_info(new_fields)


def append_binary_field_operation(
    fields: list,
    index_a: int,
    index_b: int,
    operation: str,
    source_files: list[str] | None = None,
) -> list[dict[str, object]]:
    """Create derived field(s) from two source fields, append them, and return metadata rows."""

    if index_a < 0 or index_a >= len(fields):
        raise IndexError(f"Field index out of range for binary operation {operation!r}: {index_a}")
    if index_b < 0 or index_b >= len(fields):
        raise IndexError(f"Field index out of range for binary operation {operation!r}: {index_b}")
    if index_a == index_b:
        raise ValueError("Binary field operation requires two distinct fields.")

    fld_a = fields[index_a]
    fld_b = fields[index_b]
    common_properties = _common_source_properties_for_difference(fld_a, fld_b)

    coordinate_mismatch = _difference_coordinate_mismatch_details(fld_a, fld_b)
    if coordinate_mismatch is not None:
        raise ValueError(coordinate_mismatch)

    if fld_a.identity() != fld_b.identity():
        raise ValueError("Two fields need the same identity")

    units_a = str(getattr(fld_a, "Units", ""))
    units_b = str(getattr(fld_b, "Units", ""))
    if units_a != units_b:
        raise ValueError("Two fields need the same units")

    normalized = operation.strip().lower()
    alignment_note: str | None = None
    if normalized == "difference_ab":
        subtrahend, alignment_note = _aligned_subtrahend_for_difference(fld_a, fld_b)
        new_field = fld_a - subtrahend
    elif normalized == "difference_ba":
        subtrahend, alignment_note = _aligned_subtrahend_for_difference(fld_b, fld_a)
        new_field = fld_b - subtrahend
    else:
        raise ValueError(f"Unsupported binary field operation: {operation!r}")

    # For difference outputs, keep only source-shared metadata plus provenance.
    # Delete properties one-by-one instead of clear+restore to avoid touching
    # CF-managed internals (notably units) that may reject re-assignment.
    common_property_names = set(common_properties)
    for prop_name in list(new_field.properties().keys()):
        if prop_name in common_property_names:
            continue
        try:
            new_field.del_property(prop_name, default=None)
        except Exception:
            # Some CF internals may reject direct property deletion; keep them.
            continue

    for prop_name, prop_value in common_properties.items():
        if prop_name.lower() == "units":
            continue
        try:
            new_field.set_property(prop_name, prop_value)
        except Exception:
            # Keep best-effort behavior for CF-managed properties.
            continue

    source_labels = _difference_source_labels(source_files)
    history_lines = ["Difference constructed from:"]
    history_lines.extend(f"File: {label}" for label in source_labels)
    if alignment_note:
        history_lines.append(alignment_note)
    history_text = "\n".join(history_lines)
    new_field.set_property("history", history_text)

    fields.append(new_field)
    rows = field_info([new_field])
    return rows


def _common_source_properties_for_difference(field_a: cf.Field, field_b: cf.Field) -> dict[str, object]:
    """Return property mapping with keys/values shared by both source fields."""
    properties_a = field_a.properties()
    properties_b = field_b.properties()

    common: dict[str, object] = {}
    for key, value_a in properties_a.items():
        if key not in properties_b:
            continue
        value_b = properties_b[key]
        if _property_values_equal_for_difference(value_a, value_b):
            common[key] = value_a

    return common


def _property_values_equal_for_difference(value_a: object, value_b: object) -> bool:
    """Return True when two property values should be treated as equal."""
    try:
        return bool(value_a == value_b)
    except Exception:
        # Fall back to string representations for values lacking robust equality.
        return str(value_a) == str(value_b)


def _difference_source_labels(source_files: list[str] | None) -> list[str]:
    """Return display labels for provenance lines in difference history."""
    labels: list[str] = []
    for raw in source_files or []:
        text = str(raw).strip()
        if not text:
            continue
        if "://" in text:
            labels.append(text)
        else:
            labels.append(Path(text).name)

    if labels:
        return labels
    return ["unknown", "unknown"]


def _time_coord_names_and_lengths(field: cf.Field) -> tuple[set[str], list[int]]:
    """Return T-axis coordinate identity names and their lengths."""
    names: set[str] = set()
    lengths: list[int] = []

    dim_t = field.dimension_coordinate(filter_by_axis=("T",), default=None)
    if dim_t is not None:
        names.add(str(dim_t.identity(default="unknown")))
        arr = getattr(dim_t, "array", None)
        if arr is not None:
            lengths.append(int(len(arr)))

    aux_t = field.auxiliary_coordinate(filter_by_axis=("T",), axis_mode="exact", default=None)
    if aux_t is not None:
        aux_name = str(aux_t.identity(default="unknown"))
        if aux_name not in names:
            names.add(aux_name)
            arr = getattr(aux_t, "array", None)
            if arr is not None:
                lengths.append(int(len(arr)))

    return names, sorted(lengths)


def _time_lengths_compatible_for_difference(lengths_a: list[int], lengths_b: list[int]) -> bool:
    """Return True when T-axis lengths can be differenced directly or by CF broadcast."""
    if lengths_a == lengths_b:
        return True
    if len(lengths_a) != len(lengths_b):
        return False
    return all(a == b or a == 1 or b == 1 for a, b in zip(lengths_a, lengths_b))


def _time_coordinates_equal(field_a: cf.Field, field_b: cf.Field) -> bool:
    """Return True when primary T coordinates have matching values/units."""
    time_a = field_a.dimension_coordinate(filter_by_axis=("T",), default=None)
    time_b = field_b.dimension_coordinate(filter_by_axis=("T",), default=None)
    if time_a is None or time_b is None:
        return time_a is time_b
    arr_a = getattr(time_a, "array", None)
    arr_b = getattr(time_b, "array", None)
    if arr_a is None or arr_b is None:
        return arr_a is arr_b
    if len(arr_a) != len(arr_b):
        return False
    return list(arr_a) == list(arr_b) and str(getattr(time_a, "Units", "")) == str(getattr(time_b, "Units", ""))


def _aligned_subtrahend_for_difference(
    minuend: cf.Field,
    subtrahend: cf.Field,
) -> tuple[cf.Field, str | None]:
    """Return subtrahend (possibly copied/aligned) and an optional history note."""
    _, minuend_lengths = _time_coord_names_and_lengths(minuend)
    _, subtrahend_lengths = _time_coord_names_and_lengths(subtrahend)
    if minuend_lengths != subtrahend_lengths:
        return subtrahend, None
    if not minuend_lengths or any(length <= 1 for length in minuend_lengths):
        return subtrahend, None
    if _time_coordinates_equal(minuend, subtrahend):
        return subtrahend, None

    adjusted = subtrahend.copy()
    source_time = minuend.dimension_coordinate(filter_by_axis=("T",), default=None)
    if source_time is None:
        return subtrahend, None

    target_time_key = None
    for key, construct in adjusted.constructs().items():
        identity = construct.identity(default="") if hasattr(construct, "identity") else ""
        if identity == "time":
            target_time_key = key
            break

    if target_time_key is None:
        return subtrahend, None

    axes = adjusted.get_data_axes(target_time_key)
    adjusted.del_construct(target_time_key)
    adjusted.set_construct(source_time.copy(), axes=axes)

    return adjusted, "Time coordinates of the subtracted field were shifted to match the field it was subtracted from."


def _coordinates_compatible_for_difference(fld_a: cf.Field, fld_b: cf.Field) -> bool:
    """Return True when coordinates are compatible for field differencing.

    Non-time coordinates must match exactly. T-axis coordinate values may differ,
    but the number of T-axis points must match.
    """
    coords_a = coordinate_info(fld_a)
    coords_b = coordinate_info(fld_b)

    t_names_a, t_lengths_a = _time_coord_names_and_lengths(fld_a)
    t_names_b, t_lengths_b = _time_coord_names_and_lengths(fld_b)

    # Ignore the 'key' element (last position) of coordinate info
    non_t_a = [entry for entry in coords_a[::-1] if entry[0] not in t_names_a]
    non_t_b = [entry for entry in coords_b[::-1] if entry[0] not in t_names_b]
    if non_t_a != non_t_b:
        return False

    return _time_lengths_compatible_for_difference(t_lengths_a, t_lengths_b)


def _difference_coordinate_mismatch_details(fld_a: cf.Field, fld_b: cf.Field) -> str | None:
    """Return descriptive coordinate mismatch details, or None if compatible."""
    coords_a = coordinate_info(fld_a)
    coords_b = coordinate_info(fld_b)

    t_names_a, t_lengths_a = _time_coord_names_and_lengths(fld_a)
    t_names_b, t_lengths_b = _time_coord_names_and_lengths(fld_b)

    # Keep T-axis relaxed: values may differ, but coordinate lengths
    # must match.
    details: list[str] = []
    if not _time_lengths_compatible_for_difference(t_lengths_a, t_lengths_b):
        details.append(f"T coordinate element counts differ (A={t_lengths_a}, B={t_lengths_b})")

    # Ignore the 'key' element (last position) of coordinate info
    non_t_a = {name: (values, units)
               for name, values, units in coords_a[::-1]
               if name not in t_names_a}
    non_t_b = {name: (values, units)
               for name, values, units in coords_b[::-1]
               if name not in t_names_b}

    names_a = set(non_t_a)
    names_b = set(non_t_b)
    only_a = sorted(names_a - names_b)
    only_b = sorted(names_b - names_a)
    if only_a:
        details.append(f"Coordinates only in A: {', '.join(only_a)}")
    if only_b:
        details.append(f"Coordinates only in B: {', '.join(only_b)}")

    for name in sorted(names_a & names_b):
        values_a, units_a = non_t_a[name]
        values_b, units_b = non_t_b[name]
        if len(values_a) != len(values_b):
            details.append(
                f"{name} element counts differ (A={len(values_a)}, B={len(values_b)})"
            )
            continue
        if units_a != units_b:
            details.append(f"{name} units differ (A={units_a!r}, B={units_b!r})")
            continue
        if values_a != values_b:
            details.append(f"{name} values differ")

    if not details:
        return None

    return "Two fields need the same coordinates: " + "; ".join(details)
