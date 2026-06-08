"""Regridding helpers for worker-side CF field operations."""

from __future__ import annotations

from datetime import datetime, timezone
import json

import cf
import numpy as np

from .metadata_operations import field_info

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

        if target_index < 0 or target_index >= len(fields):
            raise IndexError(f"Target field index out of range: {target_index}")

        target_field = fields[target_index]
        target_grid = target_field
        target_label = str(target_field.identity())
        source_indices = [idx for idx in selected_indices if idx != target_index]
        if not source_indices:
            raise ValueError("Select at least one source field to regrid.")
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
