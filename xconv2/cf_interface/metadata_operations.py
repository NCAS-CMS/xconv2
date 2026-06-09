"""Metadata and selection operations for worker-side CF helpers."""

from __future__ import annotations

import ast
from datetime import datetime, timezone
import logging

import cf
import numpy as np

from xconv2.cache_utils import estimate_hdf5_metadata_bytes_for_fields

logger = logging.getLogger(__name__)


def field_info(fields: list) -> list[dict[str, object]]:
    """
    Serialize field metadata for GUI transport.

    Build compact, delimited string rows that include field identity, coordinate
    descriptions, optional cell metadata, and property mappings.

    Args:
        fields: Iterable of CF field-like objects.

    Returns:
        list[dict[str, object]]: Structured rows ready for worker-to-GUI payload transfer.
    """

    def _iter_fields(items: object):
        if isinstance(items, cf.FieldList):
            for item in items:
                yield from _iter_fields(item)
            return

        if isinstance(items, (list, tuple)):
            for item in items:
                yield from _iter_fields(item)
            return

        yield items

    rows: list[dict[str, object]] = []
    for x in _iter_fields(fields):
        id_ = f"{x.identity().strip()}{x.shape}"
        props = x.properties()
        info = str(x)

        raw_chunk_shape = x.nc_dataset_chunksizes()

        if isinstance(raw_chunk_shape, np.ndarray):
            raw_chunk_shape = raw_chunk_shape.tolist()

        if isinstance(raw_chunk_shape, (tuple, list)) and all(
            isinstance(v, (int, np.integer)) for v in raw_chunk_shape
        ):
            chunk_shape = str(tuple(int(v) for v in raw_chunk_shape))
        elif isinstance(raw_chunk_shape, (tuple, list)) and all(
            isinstance(v, (tuple, list)) and len(v) > 0 for v in raw_chunk_shape
        ):
            compact: list[int] = []
            for axis_chunks in raw_chunk_shape:
                head = axis_chunks[0]
                if not isinstance(head, (int, np.integer)):
                    compact = []
                    break
                compact.append(int(head))
            chunk_shape = str(tuple(compact)) if compact else ""
        elif isinstance(raw_chunk_shape, (int, np.integer)):
            chunk_shape = str((int(raw_chunk_shape),))
        else:
            chunk_shape = ""

        rows.append(
            {
                "identity": id_,
                "detail": info,
                "properties": dict(props) if isinstance(props, dict) else props,
                "chunk_shape": chunk_shape,
            }
        )

    return rows


def coordinate_info(field: cf.Field) -> list[tuple[str, list[str], str]]:
    """
    Extract plottable 1D dimension-coordinate values with units.

    Reads dimension coordinates from a field and returns only coordinates with
    more than one value so the GUI can build useful range sliders. Also includes
    coordinate units in the output.

    Args:
        field: CF field-like object exposing dimension coordinate accessors.
    Returns:
        list[tuple[str, list[str], str]]: Coordinate identity with serialized values and units.
    """

    def _iter_one_d_constructs():
        """Loop over one-dimensional coordinates and yield coordinate arrays."""

        for axis in field.domain_axes():
            c = field.dimension_coordinate(filter_by_axis=(axis,), default=None)
            if c is None:
                c = field.auxiliary_coordinate(filter_by_axis=(axis,), axis_mode="exact", default=None)
            arr = getattr(c, "array", None)
            if arr is None or len(arr) <= 1:
                continue
            yield c, arr

    def _append_coordinate_values(construct: object, values: list) -> None:
        name = str(construct.identity(default="unknown"))
        if name in seen_names:
            return
        units = str(getattr(construct, "Units", ""))
        # Keep full-precision serialized values for subspace bounds.
        # UI formatting should round for display only.
        vals = [str(x) for x in values]
        coords.append((name, vals, units))
        seen_names.add(name)

    coords: list[tuple[str, list[str], str]] = []
    seen_names: set[str] = set()

    for construct, arr in _iter_one_d_constructs():
        _append_coordinate_values(construct, arr)

    if coords:
        return coords

    # Fallback for fields that expose only 2D coordinates (for example NEMO
    # latitude/longitude auxiliary coordinates). Derive global bounds from each
    # auxiliary coordinate and synthesize slider values from the resulting bbox
    # limits.
    two_d_coords = field.coordinates(filter_by_naxes=(2,))
    for construct in two_d_coords.values():
        arr = getattr(construct, "array", None)
        if arr is None:
            continue

        marr = np.ma.array(arr)
        if marr.ndim != 2 or marr.size <= 1:
            continue

        lo = float(np.nanmin(marr.filled(np.nan)))
        hi = float(np.nanmax(marr.filled(np.nan)))
        if np.isnan(lo) or np.isnan(hi):
            continue

        # Use the larger horizontal size so synthesized sliders retain useful
        # resolution without requiring direction-specific heuristics.
        count = int(max(marr.shape))
        if count <= 1:
            continue

        vals = np.linspace(lo, hi, num=count)
        _append_coordinate_values(construct, vals)

    return coords


def parse_coordinate_subspace_commands(commands_text: str) -> dict[str, tuple[object, object]]:
    """Parse newline-delimited coordinate bound commands into a selection mapping.

    Accepted line formats (comments begin with ``#``):
    - ``coord = lo:hi``
    - ``coord: lo, hi``
    - ``coord lo hi``
    - ``coord value`` (interpreted as ``value:value``)
    """

    def _parse_atom(text: str) -> object:
        token = text.strip()
        if not token:
            raise ValueError("Empty bound value in coordinate command")

        try:
            return ast.literal_eval(token)
        except (ValueError, SyntaxError):
            pass

        try:
            if "." not in token and "e" not in token.lower():
                return int(token)
        except ValueError:
            pass

        try:
            return float(token)
        except ValueError:
            return token

    def _parse_line(line: str) -> tuple[str, tuple[object, object]]:
        if "=" in line:
            lhs, rhs = line.split("=", 1)
            coord = lhs.strip()
            bounds_text = rhs.strip()
        elif ":" in line:
            lhs, rhs = line.split(":", 1)
            coord = lhs.strip()
            bounds_text = rhs.strip()
        else:
            parts = line.split()
            if len(parts) == 2:
                coord = parts[0].strip()
                value = _parse_atom(parts[1])
                return coord, (value, value)
            if len(parts) == 3:
                coord = parts[0].strip()
                return coord, (_parse_atom(parts[1]), _parse_atom(parts[2]))
            raise ValueError(
                "Expected 'coord=lo:hi', 'coord: lo,hi', or 'coord lo hi' format"
            )

        if not coord:
            raise ValueError("Coordinate name is missing")

        if ":" in bounds_text:
            lo_text, hi_text = bounds_text.split(":", 1)
        elif "," in bounds_text:
            lo_text, hi_text = bounds_text.split(",", 1)
        else:
            value = _parse_atom(bounds_text)
            return coord, (value, value)

        return coord, (_parse_atom(lo_text), _parse_atom(hi_text))

    selections: dict[str, tuple[object, object]] = {}
    for line_no, raw_line in enumerate(commands_text.splitlines(), start=1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue

        try:
            coord, bounds = _parse_line(line)
        except ValueError as exc:
            raise ValueError(f"Invalid bounds command on line {line_no}: {exc}") from exc

        selections[coord] = bounds

    return selections


def remove_fields_by_index(fields: list, indices: list[int]) -> int:
    """Remove selected field indices in-place and return count removed."""
    if not indices:
        return 0

    removed = 0
    for idx in sorted(set(int(i) for i in indices), reverse=True):
        if 0 <= idx < len(fields):
            del fields[idx]
            removed += 1

    return removed


def save_selected_fields(
    fields: list,
    indices: list[int],
    destination: str,
    output_format: str,
    output_chunk_by_index: dict[int, str] | None = None,
) -> int:
    """Persist selected fields to disk in NetCDF or Zarr format."""
    selected_indices = [i for i in sorted(set(indices)) if 0 <= i < len(fields)]
    selected = [fields[i] for i in selected_indices]
    if not selected:
        raise ValueError("No valid selected fields to save.")

    output_format = output_format.strip().lower()
    if output_format not in {"nc", "zarr"}:
        raise ValueError(f"Unsupported output format: {output_format!r}")

    def _parse_chunk_text(chunk_text: str) -> tuple[int, ...] | None:
        """Parse user-provided chunk text into a chunk tuple."""
        text = str(chunk_text).strip()
        if not text:
            return None

        try:
            parsed = ast.literal_eval(text)
        except (ValueError, SyntaxError) as exc:
            raise ValueError(f"Invalid chunk shape {chunk_text!r}. Use tuple syntax like (64, 64).") from exc

        if isinstance(parsed, int):
            if parsed <= 0:
                raise ValueError(f"Chunk size must be positive: {chunk_text!r}")
            return (int(parsed),)

        if not isinstance(parsed, (tuple, list)):
            raise ValueError(f"Chunk shape must be an int or tuple of ints: {chunk_text!r}")

        if not parsed:
            raise ValueError("Chunk shape cannot be empty.")

        out: list[int] = []
        for dim in parsed:
            if not isinstance(dim, (int, np.integer)):
                raise ValueError(f"Chunk shape contains non-integer value: {chunk_text!r}")
            if int(dim) <= 0:
                raise ValueError(f"Chunk size must be positive: {chunk_text!r}")
            out.append(int(dim))

        return tuple(out)

    chunk_map = output_chunk_by_index or {}

    def _normalize_chunk_shape(value: object) -> tuple[int, ...] | None:
        if value is None:
            return None

        if isinstance(value, np.ndarray):
            value = value.tolist()

        if isinstance(value, (int, np.integer)):
            return (int(value),)

        if isinstance(value, (tuple, list)):
            if all(isinstance(v, (int, np.integer)) for v in value):
                return tuple(int(v) for v in value)

            # Partition-per-axis chunks, choose first chunk size along each axis.
            if all(isinstance(v, (tuple, list)) and len(v) > 0 for v in value):
                out: list[int] = []
                for axis_chunks in value:
                    head = axis_chunks[0]
                    if not isinstance(head, (int, np.integer)):
                        return None
                    out.append(int(head))
                return tuple(out)

        return None

    for idx, field in zip(selected_indices, selected):
        requested_text = str(chunk_map.get(idx, "")).strip()
        current = field.nc_dataset_chunksizes()
        before_chunks = _normalize_chunk_shape(current)

        identity = field.identity()

        if not requested_text:
            logger.debug(
                "save_selected_fields field_index=%s identity=%s rechunked=False before=%s after=%s reason=no-requested-chunk",
                idx,
                identity,
                before_chunks,
                before_chunks,
            )
            continue

        requested = _parse_chunk_text(requested_text)
        if requested is None:
            logger.debug(
                "save_selected_fields field_index=%s identity=%s rechunked=False before=%s after=%s reason=invalid-requested-chunk",
                idx,
                identity,
                before_chunks,
                before_chunks,
            )
            continue

        if before_chunks == requested:
            logger.debug(
                "save_selected_fields field_index=%s identity=%s rechunked=False before=%s after=%s reason=already-matching",
                idx,
                identity,
                before_chunks,
                before_chunks,
            )
            continue

        field.nc_set_dataset_chunksizes(requested)
        field.data.rechunk(tuple(requested), inplace=True)
        logger.debug(
            "save_selected_fields field_index=%s identity=%s rechunked=True before=%s after=%s",
            idx,
            identity,
            before_chunks,
            requested,
        )

    meta_block_size: int | None = None
    if output_format == "nc":
        meta_block_size = estimate_hdf5_metadata_bytes_for_fields(
            selected,
            btree_entry_size_bytes=64,
            overhead_factor=1.20,
            attribute_allowance_bytes=2048,
        )

        h5py_options = {"meta_block_size": meta_block_size}
        cf.write(selected, destination, fmt="NETCDF4", h5py_options=h5py_options)
    elif output_format == "zarr":
        cf.write(selected, destination, fmt="ZARR")

    logger.info(
        "save_selected_fields completed destination=%s format=%s field_count=%d meta_block_size=%s",
        destination,
        output_format,
        len(selected),
        meta_block_size,
    )

    return len(selected)


def add_dimension_coordinate_bounds(
    fields: list,
    field_index: int,
) -> tuple[list[dict[str, object]], list[str]]:
    """
    Add missing bounds to dimension coordinates on the selected field.

    Existing bounds are left untouched. When a coordinate has only a single
    cell, its own cellsize is used if available so bounds can still be created.
    """

    if field_index < 0 or field_index >= len(fields):
        raise IndexError(f"Field index out of range for add_bounds: {field_index}")

    fld = fields[field_index]
    updated_coordinate_names = []
    coords = fld.dimension_coordinates()
    coord_iterable = coords.values() if hasattr(coords, "values") else coords

    for coord in coord_iterable:
        has_bounds = coord.has_bounds()
        if has_bounds:
            logger.debug("Coordinate %r already has bounds, skipping", coord)
            continue

        created = False

        try:
            coord.create_bounds(inplace=True)
            created = True
        except ValueError:
            cellsize = getattr(coord, "cellsize", None)
            if cellsize is not None:
                try:
                    coord.create_bounds(cellsize=cellsize, inplace=True)
                    created = True
                except Exception as exc:
                    logger.warning(
                        "Failed to create bounds for coordinate %r with cellsize %r: %s",
                        coord,
                        cellsize,
                        exc,
                    )
            else:
                logger.warning("Unable to create bounds for coordinate %r: no cellsize available", coord)
        except Exception as exc:
            logger.warning("Failed to create bounds for coordinate %r: %s", coord, exc)

        if created:
            updated_coordinate_names.append(coord.identity())

    return field_info(fields), updated_coordinate_names


def get_data_for_plotting(
    field: object,
    selection_spec: dict[str, tuple[object, object]],
    collapse_by_coord: dict[str, str],
) -> object:
    """
    Build plot-ready data from selection and collapse directives.

    Parses selection bounds, applies subspace extraction, and then applies any
    requested collapses in sequence.

    Args:
        field: CF field-like object to subset and collapse.
        selection_spec: Mapping of coordinate name to low/high bound pair.
        collapse_by_coord: Mapping of coordinate name to collapse method.

    Returns:
        object: Subspaced and optionally collapsed field-like object.
    """

    def _parse_bound(value: object) -> object:
        """Convert textual bounds to numbers when possible."""
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

    def _snap_singleton_bound(coord_name: str, value: object) -> object:
        """
        Snap singleton GUI bounds to nearest coordinate values in the field.

        GUI coordinate values may not correspond exactly to coordinate values in
        the field in terms of binary equivalence.
        """

        coord = field.dimension_coordinate(coord_name, default=None)
        if coord is None:
            coord = field.auxiliary_coordinate(coord_name, default=None)
        if coord is None:
            return value

        arr = coord.array

        try:
            numeric = np.ma.array(arr, dtype=float).compressed()
        except Exception:
            return value
        if numeric.size == 0:
            return value

        try:
            value_as_float = float(value)
        except (TypeError, ValueError):
            return value

        nearest_index = int(np.abs(numeric - value_as_float).argmin())
        nearest = float(numeric[nearest_index])
        if isinstance(value, int) and nearest.is_integer():
            return int(nearest)
        return nearest

    subspace_kwargs: dict[str, object] = {}
    for coord_name, bounds in selection_spec.items():
        lo, hi = bounds
        lo = _parse_bound(lo)
        hi = _parse_bound(hi)
        if lo == hi:
            subspace_kwargs[coord_name] = _snap_singleton_bound(coord_name, lo)
        else:
            if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
                lo, hi = sorted((lo, hi))
            subspace_kwargs[coord_name] = cf.wi(lo, hi)

    pfld = field.subspace(**subspace_kwargs)

    # Remove subspaced-down-to-size-1 axes from the collapse selection.
    for coord_name in selection_spec:
        if coord_name not in collapse_by_coord:
            continue

        coord = pfld.dimension_coordinate(coord_name, default=None)
        if coord is None:
            continue
        if getattr(coord, "size", None) is not None and coord.size <= 1:
            del collapse_by_coord[coord_name]

    # Apply collapses based on GUI selections.
    if collapse_by_coord:
        instruction = []

        axes_methods = tuple(collapse_by_coord.items())
        previous_method = axes_methods[0][1]
        for axis, method in axes_methods:
            if method != previous_method:
                instruction.append(previous_method)

            instruction.append(f"{axis}:")
            previous_method = method

        instruction.append(axes_methods[-1][1])

        instruction = " ".join(instruction)

        try:
            pfld = pfld.collapse(instruction, weights=True)
        except ValueError:
            pfld = pfld.collapse(instruction, weights=False)

    return pfld


def subset_field_to_reference_xy_domain(field: cf.Field, reference_field: cf.Field) -> cf.Field:
    """Subset ``field`` to the X/Y bounds of ``reference_field`` using cf.wi."""

    def _axis_coord(fld: cf.Field, axis: str):
        coord = fld.dimension_coordinate(filter_by_axis=(axis,), default=None)
        if coord is None:
            coord = fld.auxiliary_coordinate(filter_by_axis=(axis,), axis_mode="exact", default=None)
        return coord

    def _axis_bounds(fld: cf.Field, axis: str) -> tuple[float, float] | None:
        coord = _axis_coord(fld, axis)
        if coord is None:
            return None
        try:
            values = np.ma.array(coord.array, dtype=float).compressed()
        except Exception:
            return None
        if values.size == 0:
            return None
        lo = float(np.nanmin(values))
        hi = float(np.nanmax(values))
        if np.isnan(lo) or np.isnan(hi):
            return None
        return (lo, hi) if lo <= hi else (hi, lo)

    subspace_kwargs: dict[str, object] = {}
    for axis_name in ("X", "Y"):
        bounds = _axis_bounds(reference_field, axis_name)
        if bounds is None:
            continue

        target_coord = _axis_coord(field, axis_name)
        if target_coord is None:
            continue

        target_name = str(target_coord.identity(default="")).strip()
        if not target_name:
            continue

        lo, hi = bounds
        subspace_kwargs[target_name] = cf.wi(lo, hi)

    if not subspace_kwargs:
        return field

    try:
        return field.subspace(**subspace_kwargs)
    except Exception:
        logger.exception("Failed to subset field to reference XY domain; falling back to original field")
        return field


def append_selection_field_operation(
    fields: list,
    field_index: int,
    selection_spec: dict[str, tuple[object, object]],
    collapse_by_coord: dict[str, str],
) -> list[dict[str, object]]:
    """Apply selection/collapse (plot path) to a field, append result, return metadata rows."""
    if field_index < 0 or field_index >= len(fields):
        raise IndexError(f"Field index out of range for apply selection: {field_index}")

    fld = fields[field_index]
    result = get_data_for_plotting(
        fld,
        dict(selection_spec or {}),
        dict(collapse_by_coord or {}),
    )

    if isinstance(result, cf.FieldList):
        new_fields = list(result)
    elif isinstance(result, (list, tuple)):
        new_fields = list(result)
    else:
        new_fields = [result]

    today = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for new_field in new_fields:
        history = new_field.get_property("history", "")
        if history:
            history += "\n"
        history += (
            f"{new_field.identity()} derived from {fld.identity()} by apply selection "
            f"via cf-python {cf.__version__} ({today})."
        )
        new_field.set_property("history", history)
        fields.append(new_field)

    return field_info(new_fields)


def save_selected_field_data(field: object, filename: str) -> None:
    """Persist selected field data to disk using cf.write."""
    cf.write(field, filename)
