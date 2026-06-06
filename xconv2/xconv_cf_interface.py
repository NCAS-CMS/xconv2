"""
Reusable worker-side helpers for CF data extraction.

These functions are imported into the worker execution namespace so generated
code snippets in ``cf_templates.py`` can call them directly.
"""

from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
import logging

import cf
import cfplot as cfp
import numpy as np
from matplotlib import pyplot as plt
from xconv2.cache_utils import estimate_hdf5_metadata_bytes_for_fields
from xconv2.cell_method_handler import cell_methods_string_from_field
from xconv2.lineplot import LinePlot
from xconv2.plot_layout_helpers import (
    annotation_text,
    apply_vertical_padding,
    estimate_layout_padding,
)


__all__ = [
    "field_info",
    "coordinate_info",
    "parse_coordinate_subspace_commands",
    "remove_fields_by_index",
    "save_selected_fields",
    "add_dimension_coordinate_bounds",
    "append_unary_xy_field_operation",
    "append_binary_field_operation",
    "regrid_from_config",
    "get_data_for_plotting",
    "save_selected_field_data",
    "annotation_text",
    "estimate_layout_padding",
    "apply_vertical_padding",
    "contour_data_range",
    "auto_contour_title",
    "run_contour_plot",
    "run_line_plot",
]


logger = logging.getLogger(__name__)

_REGRID_METHODS = {
    "linear",
    "bilinear",
    "conservative_1st",
    "conservative",
    "conservative_2nd",
    "patch",
    "nearest_stod",
    "nearest_dtos",
}


def _extract_regular_lonlat_spec(target_spec: object) -> dict[str, float | int]:
    """Normalize target specs for regular lon/lat grids from supported JSON shapes."""

    lon_section: dict[str, object] = {}
    lat_section: dict[str, object] = {}

    if isinstance(target_spec, dict):
        if isinstance(target_spec.get("longitude"), dict):
            lon_section = dict(target_spec["longitude"])
        if isinstance(target_spec.get("latitude"), dict):
            lat_section = dict(target_spec["latitude"])

        # Flat form from lat/lon dialog target_spec.
        if not lon_section and not lat_section:
            lon_section = dict(target_spec)
            lat_section = dict(target_spec)

    elif isinstance(target_spec, list):
        for entry in target_spec:
            if not isinstance(entry, dict):
                continue
            lon_candidate = entry.get("longitude")
            lat_candidate = entry.get("latitude")
            if isinstance(lon_candidate, dict):
                lon_section = dict(lon_candidate)
            if isinstance(lat_candidate, dict):
                lat_section = dict(lat_candidate)

    def _first_number(mapping: dict[str, object], keys: tuple[str, ...], *, as_int: bool) -> float | int:
        for key in keys:
            if key not in mapping:
                continue
            value = mapping.get(key)
            try:
                return int(value) if as_int else float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid value for {key!r}: {value!r}") from exc
        joined = ", ".join(keys)
        raise ValueError(f"Missing required target spec key(s): {joined}")

    nlon = int(_first_number(lon_section, ("nx", "nlon"), as_int=True))
    lon1 = float(_first_number(lon_section, ("lon1",), as_int=False))
    deltalon = float(_first_number(lon_section, ("deltax", "deltalon", "dlon"), as_int=False))
    nlat = int(_first_number(lat_section, ("ny", "nlat"), as_int=True))
    lat1 = float(_first_number(lat_section, ("lat1",), as_int=False))
    deltalat = float(_first_number(lat_section, ("deltay", "deltalat", "dlat"), as_int=False))

    if nlon <= 0 or nlat <= 0:
        raise ValueError("Grid dimensions nlon/nlat must be positive.")
    if deltalon <= 0.0 or deltalat <= 0.0:
        raise ValueError("Grid increments deltalon/deltalat must be positive.")

    return {
        "nlon": nlon,
        "lon1": lon1,
        "deltalon": deltalon,
        "nlat": nlat,
        "lat1": lat1,
        "deltalat": deltalat,
    }


def _regular_lonlat_target_from_spec(target_spec: object) -> tuple[list[object], str]:
    """Build a regular lon/lat destination from normalized JSON regrid spec."""
    spec = _extract_regular_lonlat_spec(target_spec)

    nlon = int(spec["nlon"])
    nlat = int(spec["nlat"])
    lon1 = float(spec["lon1"])
    lat1 = float(spec["lat1"])
    deltalon = float(spec["deltalon"])
    deltalat = float(spec["deltalat"])

    lon_values = lon1 + deltalon * np.arange(nlon, dtype=float)
    lat_values = lat1 + deltalat * np.arange(nlat, dtype=float)

    lon = cf.DimensionCoordinate(data=cf.Data(lon_values, "degrees_east"))
    lon.set_property("standard_name", "longitude")
    lat = cf.DimensionCoordinate(data=cf.Data(lat_values, "degrees_north"))
    lat.set_property("standard_name", "latitude")

    lon_bounds = np.column_stack((lon_values - 0.5 * deltalon, lon_values + 0.5 * deltalon))
    lat_bounds = np.column_stack((lat_values - 0.5 * deltalat, lat_values + 0.5 * deltalat))
    lon.set_bounds(cf.Bounds(data=cf.Data(lon_bounds, "degrees_east")))
    lat.set_bounds(cf.Bounds(data=cf.Data(lat_bounds, "degrees_north")))

    label = (
        f"regular_lonlat(nlon={nlon}, lon1={lon1:g}, deltalon={deltalon:g}, "
        f"nlat={nlat}, lat1={lat1:g}, deltalat={deltalat:g})"
    )
    # regrids accepts a sequence of destination coordinates.
    return [lon, lat], label

# Default kwargs for unary XY operations. These are applied automatically by
# worker-side field ops until UI controls for operation parameters are added.
_UNARY_XY_OPERATION_DEFAULT_KWARGS: dict[str, dict[str, object]] = {
    "grad": {"radius": "earth"},
    "laplacian": {"radius": "earth"},
}


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
        """ We need to loop over one-d coordinates and find coordinate arrays """

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
        if units.startswith('degrees'):
           vals = [f'{v:.2f}' for v in values]
        else:
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
            history += '\n'
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

    if coordinate_info(fld_a) != coordinate_info(fld_b):
        raise ValueError("Two fields need the same coordinates")

    if fld_a.identity() != fld_b.identity():
        raise ValueError("Two fields need the same identity")

    units_a = str(getattr(fld_a, "Units", ""))
    units_b = str(getattr(fld_b, "Units", ""))
    if units_a != units_b:
        raise ValueError("Two fields need the same units")

    normalized = operation.strip().lower()
    if normalized == "difference_ab":
        new_field = fld_a - fld_b
    elif normalized == "difference_ba":
        new_field = fld_b - fld_a
    else:
        raise ValueError(f"Unsupported binary field operation: {operation!r}")

    today = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    history = new_field.get_property("history", "")
    if history:
        history += "\n"
    history += (
        f"{new_field.identity()} derived from {fld_a.identity()} and {fld_b.identity()} "
        f"by {normalized} via cf-python {cf.__version__} ({today})."
    )
    if source_files:
        history += f" Source files: {', '.join(str(x) for x in source_files if str(x).strip())}."
    new_field.set_property("history", history)

    if source_files:
        new_field.set_property(
            "xconv_source_files",
            ", ".join(str(x) for x in source_files if str(x).strip()),
        )

    fields.append(new_field)
    return field_info([new_field])


def regrid_from_config(fields: list, regrid_config_json: str) -> list[dict[str, object]]:
    """Parse regrid JSON configuration, regrid selected fields, and append outputs."""

    try:
        config = json.loads(regrid_config_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid regrid configuration JSON: {exc}") from exc

    raw_indices = config.get("field_indices", [])
    if not isinstance(raw_indices, list):
        raise ValueError("Regrid configuration field_indices must be a list.")

    selected_indices: list[int] = []
    for raw_idx in raw_indices:
        try:
            idx = int(raw_idx)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(fields) and idx not in selected_indices:
            selected_indices.append(idx)

    if not selected_indices:
        raise ValueError("Regrid configuration did not include any valid field indices.")

    target = str(config.get("target", "")).strip().lower()
    method = str(config.get("method", "")).strip().lower()
    if not method:
        raise ValueError("Regrid configuration must include a method.")
    if method not in _REGRID_METHODS:
        raise ValueError(f"Unsupported regrid method: {method!r}")

    source_indices: list[int]
    target_grid: object
    target_label: str

    if target == "selected field":
        raw_target_index = config.get("target_field_index")
        if raw_target_index is None:
            raise ValueError("Regrid configuration missing target_field_index for selected-field target.")

        try:
            target_index = int(raw_target_index)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid target_field_index: {raw_target_index!r}") from exc

        if target_index not in selected_indices:
            raise ValueError("target_field_index must be one of the selected field indices.")
        if target_index < 0 or target_index >= len(fields):
            raise IndexError(f"Target field index out of range: {target_index}")

        target_field = fields[target_index]
        target_grid = target_field
        target_label = str(target_field.identity())
        source_indices = [idx for idx in selected_indices if idx != target_index]
        if not source_indices:
            raise ValueError("Select at least one source field in addition to the selected target field.")
    elif target == "healpix":
        target_spec = config.get("target_spec")
        if not isinstance(target_spec, dict):
            raise ValueError("Regrid configuration missing target_spec for healpix target.")

        raw_level = target_spec.get("level")
        try:
            level = int(raw_level)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid healpix level: {raw_level!r}") from exc
        if level < 0:
            raise ValueError("Healpix level must be non-negative.")

        target_grid = cf.Domain.create_healpix(level)
        target_label = f"healpix(level={level})"
        source_indices = selected_indices
    else:
        target_spec = config.get("target_spec")
        if target_spec is None:
            raise ValueError(f"Regrid target {target!r} did not provide target_spec.")
        target_grid, target_label = _regular_lonlat_target_from_spec(target_spec)
        source_indices = selected_indices

    regrid_kwargs: dict[str, object] = {"method": method}

    new_fields: list[object] = []
    for source_index in source_indices:
        src_field = fields[source_index]
        regridded = src_field.regrids(target_grid, **regrid_kwargs)

        today = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        history = regridded.get_property("history", "")
        if history:
            history += "\n"
        history += (
            f"{regridded.identity()} derived from {src_field.identity()} by regridding "
            f"to {target_label} using cf-python {cf.__version__} ({today})."
        )
        regridded.set_property("history", history)
        new_fields.append(regridded)

    for new_field in new_fields:
        fields.append(new_field)

    return field_info(new_fields)


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
        """ Make sure user has selected a proper chunk option """
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
    elif output_format == 'zarr':
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

def contour_data_range(pfld: object) -> tuple[float, float]:
    """
    Return contour min/max while tolerating backend indexing quirks.

    Primary path uses the field array directly so masked values are excluded.
    If that fails (for example with some h5netcdf/h5py indexing behaviors),
    return a safe default and let plotting continue.
    """
    try:
        arr = np.ma.array(pfld.array).compressed()
    except Exception as exc:
        logger.warning(
            "Falling back to default contour range due to backend read error: %s",
            exc,
        )
        return 0.0, 0.0

    if arr.size == 0:
        return 0.0, 0.0

    return float(arr.min()), float(arr.max())


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
        """ 
        Use to convert an object which might be a string
        into a number if possible, otherwise return the
        original entity.
        """
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
        GUI coordinate values may not correspond exactly to 
        coordinate values in the field (in terms of binary equivalence).
        Hence we use this to snap singleton bounds to the nearest
        actual coordinate value.
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
        # make sure we have numeric values
        lo = _parse_bound(lo)
        hi = _parse_bound(hi)
        if lo == hi:
            # deal with GUI to field value equivalence
            subspace_kwargs[coord_name] = _snap_singleton_bound(coord_name, lo)
        else:
            if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
                lo, hi = sorted((lo, hi))
            subspace_kwargs[coord_name] = cf.wi(lo, hi)

    pfld = field.subspace(**subspace_kwargs)

    # Remove subspaced-down-to-size-1 axes from the collapse selection
    # (if they're there it doesn't upset the collapse call, but it
    # does make creating a sensible plot title tricker).
    for coord_name in selection_spec:
        if coord_name not in collapse_by_coord:
            continue

        coord = pfld.dimension_coordinate(coord_name, default=None)
        if coord is None:
            continue
        if getattr(coord, "size", None) is not None and coord.size <= 1:
            del collapse_by_coord[coord_name]

    # Apply collapses based on GUI selections.
    #
    # Build up a collapse string, e.g. "time: mean", or "time: height:
    # mean", or "time: mean height: maximum", etc.
    #
    # Note: "time: height: mean" is not always the same as two
    #       separate consectutive collapses of "time: mean" and then
    #       "height: mean". It is presumed that when a user asks for a
    #       collapse over two axes that they mean this to the
    #       simulataneous collapse (i.e. "time: height: mean"), rather
    #       than the two seperate collapses.
    #
    # Note to selves: It would be nice to replace "time", "height",
    #                 etc. with domain axis keys "domainaxis0",
    #                 "domainaxis2", etc.
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
            # Try a weighted collapse
            pfld = pfld.collapse(instruction, weights=True)
        except ValueError:
            # Could find appropriate weights, so collapse un-weighted.
            pfld = pfld.collapse(instruction, weights=False)

    return pfld


def save_selected_field_data(field: object, filename: str) -> None:
    """Persist selected field data to disk using cf.write."""
    cf.write(field, filename)


def run_contour_plot(
    pfld: object,
    options: dict[str, object] | None,
    mapset: dict[str, object] | None = None,
    selection_spec: dict[str, tuple[object, object]] | None = None,
    collapse_by_coord: dict[str, str] | None = None,
) -> None:
    """
    Render a contour plot for a prepared field.

    Applies contour styling, level configuration, optional annotations, and
    optional file output using cf-plot and matplotlib.

    Args:
        pfld: Plot-ready field-like object.
        options: Contour options mapping from GUI state or saved script.
        mapset: Map projection options including:
            - map_projection: Projection name ('cyl', 'npstere', 'spstere', etc.)
            - bbox: Bounding box [lonmin, latmin, lonmax, latmax] for non-stereo projections
            - boundinglat: Bounding latitude for stereographic projections
            - map_resolution: Natural Earth resolution ('110m', '50m', '10m')
            - lat_0: Standard parallel or latitude of projection center
            - lon_0: Central meridian or longitude of projection center
        selection_spec: Coordinate selection bounds.
        collapse_by_coord: Collapse methods by coordinate name.

    Returns:
        None
    """
    options = options or {}
    mapset = mapset or {}
    selection_spec = selection_spec or {}
    collapse_by_coord = collapse_by_coord or {}

    # Only apply map projections if one was explicitly set
    if mapset.get("map_projection"):
        projection = mapset.get("map_projection")
        resolution = mapset.get("map_resolution", "110m")
        if projection in ['spstere','npstere']:

            cfp.mapset(proj=projection, 
                        resolution=resolution, 
                        boundinglat=mapset.get("boundinglat", -45 if projection == 'spstere' else 45),
                        lon_0=mapset.get("lon_0", 0.0),
            )
        else:
            bbox = mapset.get("bbox")
            if bbox and isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                lonmin, latmin, lonmax, latmax = tuple(bbox)
            else:
                lonmin, latmin, lonmax, latmax = None, None, None, None
            lon_0 = mapset.get("lon_0", 0.0)
            lat_0 = mapset.get("lat_0", 0.0)
        
            cfp.mapset(proj=projection, 
                        resolution=resolution,
                        lonmin=lonmin, lonmax=lonmax, latmin=latmin, latmax=latmax,
                        lon_0= lon_0, lat_0=lat_0
            )
            
    annotation_display = bool(options.get("annotation_display", False))
    filename = options.get("filename")
    title = options.get("title")
    page_title = options.get("page_title")
    page_title_display = bool(options.get("page_title_display", False))
    annotation_properties = options.get("annotation_properties", [])
    annotation_free_text = str(options.get("annotation_free_text", "") or "").strip()
    cscale = options.get("cscale")

    def _positive_float_option(key: str, default: float) -> float:
        raw = options.get(key, default)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return default
        if value <= 0:
            return default
        return value

    contour_title_fontsize = _positive_float_option("contour_title_fontsize", 10.5)
    page_title_fontsize = _positive_float_option("page_title_fontsize", 10.0)
    annotation_fontsize = _positive_float_option("annotation_fontsize", 8.0)

    fill = bool(options.get("fill", True))
    lines_enabled = bool(options.get("lines", False))
    line_labels = bool(options.get("line_labels", True))
    negative_linestyle = options.get("negative_linestyle", "solid")
    zero_thick = options.get("zero_thick", False)
    blockfill = bool(options.get("blockfill", False))
    blockfill_fast = options.get("blockfill_fast", None)

    mode = options.get("mode")
    levels = options.get("levels")
    auto_min = options.get("min")
    auto_max = options.get("max")
    intervals = options.get("intervals")
    page_margin_top = float(options.get("page_margin_top", 0.0) or 0.0)
    page_margin_bottom = float(options.get("page_margin_bottom", 0.0) or 0.0)

    page_margin_top = max(0.0, min(page_margin_top, 0.25))
    page_margin_bottom = max(0.0, min(page_margin_bottom, 0.25))



    if cscale:
        cfp.cscale(scale=cscale)
    else:
        cfp.cscale()

    contour_levels = None
    contour_min = None
    contour_max = None
    contour_step = None

    if mode == "explicit" and levels:
        contour_levels = sorted(float(v) for v in levels)
    elif (
        mode == "auto"
        and auto_min is not None
        and auto_max is not None
        and intervals is not None
    ):
        contour_min = float(auto_min)
        contour_max = float(auto_max)
        interval_count = max(int(intervals), 1)
        contour_step = (contour_max - contour_min) / float(interval_count)

    if not title:
        title = auto_contour_title(
            pfld=pfld,
            selection_spec=selection_spec,
            collapse_by_coord=collapse_by_coord,
        )

    contour_kwargs: dict[str, object] = {
        "fill": fill,
        "lines": lines_enabled,
        "line_labels": line_labels,
        "negative_linestyle": negative_linestyle,
        "zero_thick": zero_thick,
        "blockfill": blockfill,
    }
    if title:
        contour_kwargs["title"] = str(title)
    if blockfill_fast is not None:
        contour_kwargs["blockfill_fast"] = bool(blockfill_fast)

    def _apply_levels() -> None:
        if contour_levels is not None:
            cfp.levs(manual=contour_levels)
        elif contour_min is not None and contour_max is not None and contour_step is not None:
            cfp.levs(min=contour_min, max=contour_max, step=contour_step)
        else:
            cfp.levs()

    def _run_contour_prepass() -> None:
        prepass_kwargs = dict(contour_kwargs)
        prepass_kwargs["fill"] = False
        prepass_kwargs["lines"] = False
        prepass_kwargs["line_labels"] = False
        prepass_kwargs["blockfill"] = False
        prepass_kwargs.pop("blockfill_fast", None)
        prepass_kwargs.pop("title", None)

        # Keep prepass side-effect free for level configuration; levels are
        # applied once in the final render pass.
        try:
            cfp.con(pfld, **prepass_kwargs)
        except Exception as exc:
            logger.warning("Skipping contour prepass after cf-plot error: %s", exc)

    annotation_text_value = annotation_text(
        annotation_display=annotation_display,
        annotation_properties=annotation_properties,
        annotation_free_text=annotation_free_text,
    )
    top_padding, bottom_padding = estimate_layout_padding(
        page_title=page_title,
        page_title_display=page_title_display,
        page_title_fontsize=page_title_fontsize,
        annotation_text=annotation_text_value,
        annotation_fontsize=annotation_fontsize,
        run_prepass=_run_contour_prepass,
    )
    top_padding += page_margin_top
    bottom_padding += page_margin_bottom

    # Force cf-plot into embedded mode for worker rendering. Using cf-plot's
    # file mode can trigger an external viewer command on some platforms.
    cfp.gopen(user_plot=1)

    _apply_levels()

    if hasattr(cfp, "setvars"):
        # Always pass viewer=None to prevent cfplot from spawning an external
        # image viewer (e.g. ImageMagick display) after gclose().
        cfp.setvars(title_fontsize=contour_title_fontsize, viewer=None)

    map_title_fallback_used = False
    fallback_contour_title = str(contour_kwargs.get("title", "") or "")

    try:
        cfp.con(pfld, **contour_kwargs)
    except UnboundLocalError as exc:
        # cf-plot can fail in _map_title with "xpt" unbound for some
        # projection/title combinations. Retry once without title.
        if "xpt" in str(exc) and "title" in contour_kwargs:
            map_title_fallback_used = True
            logger.warning(
                "CFP_TITLE_FALLBACK retrying contour render without title after cf-plot _map_title error: %s",
                exc,
            )
            fallback_kwargs = dict(contour_kwargs)
            fallback_kwargs.pop("title", None)
            cfp.con(pfld, **fallback_kwargs)

            # Preserve a visible title after disabling map-title rendering.
            if fallback_contour_title and not (page_title_display and page_title):
                page_title = fallback_contour_title
                page_title_display = True
        else:
            raise

    if map_title_fallback_used:
        logger.info("CFP_TITLE_FALLBACK_APPLIED title rendered as page title")
        send_to_gui_fn = globals().get("send_to_gui")
        if callable(send_to_gui_fn):
            send_to_gui_fn("STATUS:Map title fallback applied (cf-plot _map_title bug workaround)")
    
    mycanvas = plt.gcf()
    if top_padding > 0 or bottom_padding > 0:
        # Reserve headroom for page title and bottom annotations even when
        # axes are not subplot-managed.
        apply_vertical_padding(mycanvas, top_padding, bottom_padding)

    if page_title_display and page_title:
        mycanvas.suptitle(str(page_title), y=0.995, fontsize=page_title_fontsize)

    if annotation_text_value:
        mycanvas.text(
            0.5,
            0.02,
            annotation_text_value,
            ha="center",
            va="bottom",
            fontsize=annotation_fontsize,
        )

    if filename is not None:
        mycanvas.savefig(str(filename))
        plt.close(mycanvas)

def run_line_plot(
    pfld: object,
    options: dict[str, object] | None,
    selection_spec: dict[str, tuple[object, object]] | None = None,
    collapse_by_coord: dict[str, str] | None = None,
) -> None:
    """Render line plots via the dedicated LinePlot helper class."""
    _ = (selection_spec, collapse_by_coord)
    plotter = LinePlot(pfld=pfld, options=options, 
                       collapse_by_coord=collapse_by_coord)
    plotter.render()


def auto_contour_title(
    pfld: object,
    selection_spec: dict[str, tuple[object, object]] | None,
    collapse_by_coord: dict[str, str] | None,
) -> str:
    """Derive default contour title from collapse metadata or singleton selections."""
    selection_spec = selection_spec or {}
    collapse_by_coord = collapse_by_coord or {}

    if collapse_by_coord:
        collapse_title = cell_methods_string_from_field(
            pfld, collapse_by_coord
        ).strip()
        if collapse_title:
            return collapse_title

    selections: list[str] = []
    for coord_name, bounds in selection_spec.items():
        if not isinstance(bounds, (tuple, list)) or len(bounds) < 2:
            continue
        lo, hi = bounds[0], bounds[1]
        if lo == hi:
            selections.append(f"{coord_name}={lo}")

    return ", ".join(selections)
