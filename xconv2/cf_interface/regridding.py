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

_TARGET_OPTIONS  = ['regular lonlat', 'healpix', 'selected field']


class XconvRegridder:
    """ 
    Methods for working with the actual configuration of regridding.
    Provides the regridding specification and defines the serialisation
    and deserialisation of the configuration. Carries out regridding.
    """
    def __init__(self, json_str: str) -> None:
        """ Validate expected json configuration:
        {
            "field_indices": [0, 1, 2],
            "target": "selected field" | "healpix" | "regular lonlat",
            "method": "linear" | "bilinear" | "conservative_1st" | "conservative" | "conservative_2nd" | "patch" | "nearest_stod" | "nearest_dtos",
            // For target = "selected field":
            "target_field_index": 3,
            // For target = "healpix":
            "target_spec": {"level": 4},
            // For target = "regular lonlat":
            // Or nested form:
            "target_spec": {
                "longitude": {"nx": 360, "lon1": 0.0, "delta": 1.0},
                "latitude": {"ny": 180, "lat1": -90.0, "delta": 1.0}
            }
        """
        try:
            config = json.loads(json_str)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid regrid configuration JSON: {exc}") from exc
        self.json = json_str

        self.field_indices = config.get("field_indices", [])
        if not isinstance(self.field_indices, list):
            raise ValueError("Regrid configuration field_indices must be a list.")

        method = str(config.get("method", "None")).strip().lower()
        if method not in _REGRID_METHODS:
            raise ValueError(f"Unsupported regrid method: {method!r}")

        target = str(config.get("target", "None")).strip().lower()
        if target not in _TARGET_OPTIONS:
            raise ValueError(f"Unsupported regrid target: {target!r}")
        
        target_spec = config.get("target_spec")

        if target == "selected field":
            spec = self._extract_target_spec_for_selected_field(target_spec)
        elif target == "healpix":
            spec = self._extract_target_spec_for_healpix(target_spec)   
        elif target == "regular lonlat":
            spec = self._extract_target_spec_for_regular_lonlat(target_spec)

        self.keywords = self._build_regrid_keywords(method, target, spec)
        self.method = method


    @staticmethod
    def _domain_description(field) -> str:
        domain = field.domain
        if domain is None:
            return "unknown domain"
        dimensions = getattr(domain, "dimensions", None)
        if dimensions is None:
            dimensions = getattr(domain, "directions", None)
        if dimensions is None:
            return domain.__class__.__name__
        try:
            return f"{domain.__class__.__name__} with {len(dimensions)} dimensions"
        except TypeError:
            return domain.__class__.__name__
    

    def do_regrid(self, fields: list) -> list[dict[str, object]]:
        """ Carry out the regridding operation on the selected fields. """
        
        new_fields = []
        today = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        history = ""

        for source_index in self.field_indices:
            src_field = fields[source_index]
            kwargs = dict(self.keywords)
            target = kwargs.pop("target", None)

            if target == "healpix":
                level = kwargs.pop("level")
                dst = cf.Domain.create_healpix(level)
                regridded = src_field.regrids(dst, **kwargs)
            elif target == "regular lonlat":
                longitude = kwargs.pop("longitude")
                latitude = kwargs.pop("latitude")
                dst = _build_lonlat_coordinate_list(longitude, latitude)
                regridded = src_field.regrids(dst, **kwargs)
            else:
                regridded = src_field.regrids(**kwargs)

            history += (
                f"{regridded.identity()} regridded from {self._domain_description(src_field)}\n"
                f"to {self._domain_description(regridded)} using cf-python {cf.__version__}\n"
                f"with method {self.method} on {today}.\n"
            )
            regridded.set_property("history", history)
            new_fields.append(regridded)
            fields.append(regridded)

        return field_info(new_fields)


    @staticmethod
    def _extract_target_spec_for_healpix(target_spec):
        """ 
           "target_spec": {"level": 4},
        """
        try:
            level = int(target_spec.get("level"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid healpix level: {target_spec!r}") from exc
        if level < 0:
            raise ValueError("Healpix level must be non-negative.")
        return {"level": level}
    

    @staticmethod
    def _extract_target_spec_for_regular_lonlat(target_spec):
        """ "target_spec": {
                "longitude": {"nx": 360, "lon1": 0.0, "delta": 1.0},
                "latitude": {"ny": 180, "lat1": -90.0, "delta": 1.0}
            }
        """
        try:
            lon_spec = target_spec.get("longitude")
            lat_spec = target_spec.get("latitude")
            nx, lon1, dlon = lon_spec["nx"], lon_spec["lon1"], lon_spec["delta"]
            ny, lat1, dlat = lat_spec["ny"], lat_spec["lat1"], lat_spec["delta"]
        except (AttributeError, TypeError, KeyError) as exc:
            raise ValueError("Invalid target_spec for regular lonlat target.") from exc
        return (nx, lon1, dlon, ny, lat1, dlat)
    

    @staticmethod
    def _extract_target_spec_for_selected_field(target_spec):
        """ Find the target field index for the selected field target option. """
        try:
            target_index = int(target_spec.get("target_field_index"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid target_field_index: {target_spec!r}") from exc
        return target_index

    def _build_regrid_keywords(self, method, target, spec):
        """ Build the keywords to pass to the regrids method based on the target and method. """
        if target == "selected field":
            # We will need to extract the information fromn the selected field at the time of 
            # regridding, because the initialisation method does not include the fields argument.
            raise NotImplementedError("Regridding to a selected field is not yet implemented.")
        elif target == "healpix":
            return {"method": method, "target": "healpix", "level": spec["level"]}
        elif target == "regular lonlat":
            nx, lon1, dlon, ny, lat1, dlat = spec
            return {"method": method, "target": "regular lonlat", 
                    "longitude": {"nx": nx, "lon1": lon1, "delta": dlon},
                    "latitude": {"ny": ny, "lat1": lat1, "delta": dlat}}
        else:
            raise ValueError(f"Unsupported regrid target: {target!r}")  


def _build_lonlat_coordinate_list(longitude: dict, latitude: dict) -> list:
    """Build a list of cf.DimensionCoordinate objects for a regular lon/lat grid.
    
    Args:
        longitude: dict with keys nx, lon1, delta
        latitude:  dict with keys ny, lat1, delta
    Returns:
        [lat_dc, lon_dc] as required by cf.Field.regrids()
    """
    nx, lon1, dlon = longitude["nx"], longitude["lon1"], longitude["delta"]
    ny, lat1, dlat = latitude["ny"], latitude["lat1"], latitude["delta"]

    lon_vals = lon1 + np.arange(nx) * dlon
    lon_bounds = np.column_stack([lon_vals - dlon / 2, lon_vals + dlon / 2])
    lat_vals = lat1 + np.arange(ny) * dlat
    lat_bounds = np.column_stack([lat_vals - dlat / 2, lat_vals + dlat / 2])

    lon_dc = cf.DimensionCoordinate(
        data=cf.Data(lon_vals, units="degrees_east"),
        bounds=cf.Bounds(data=cf.Data(lon_bounds, units="degrees_east")),
    )
    lat_dc = cf.DimensionCoordinate(
        data=cf.Data(lat_vals, units="degrees_north"),
        bounds=cf.Bounds(data=cf.Data(lat_bounds, units="degrees_north")),
    )
    return [lat_dc, lon_dc]


def regrid_from_config(fields, config_json):
    return XconvRegridder(config_json).do_regrid(fields)
