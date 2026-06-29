"""Minimal ESGF/STAC browsing helpers for interactive exploration.

Step 1 prototype: expose CMIP6 facet browsing as a filesystem-like ``ls``.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from collections import OrderedDict
from typing import Any, Iterable, Sequence
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

CMIP6_DEFAULT_STAC_ENDPOINT = "https://api.stac.ceda.ac.uk"
CMIP6_DEFAULT_COLLECTION = "cmip6"


@dataclass(frozen=True)
class FacetSpec:
    """Describe one virtual directory level in a faceted STAC hierarchy.

    Parameters
    ----------
    name : str
        Canonical, human-readable facet name used for diagnostics and path
        semantics (for example ``"experiment_id"``).
    aliases : tuple[str, ...]
        Ordered STAC property keys to use for this facet. The first alias is
        the preferred query key; later aliases are fallbacks for providers that
        expose the same concept under different property names.
    """

    name: str
    aliases: tuple[str, ...]


CMIP6_DEFAULT_FACETS: tuple[FacetSpec, ...] = (
    FacetSpec("activity_id", ("cmip6:activity_id", "activity_id")),
    FacetSpec("experiment_id", ("cmip6:experiment_id", "experiment_id")),
    FacetSpec("source_id", ("cmip6:source_id", "source_id")),
    FacetSpec("institution_id", ("cmip6:institution_id", "institution_id")),
    FacetSpec("table_id", ("cmip6:table_id", "table_id")),
    FacetSpec("variable_id", ("cmip6:variable_id", "variable_id")),
    FacetSpec("variant_id", ("cmip6:variant_label", "variant_id", "variant_label")),
    FacetSpec("realm", ("cmip6:realm", "realm")),
    FacetSpec("grid_label", ("cmip6:grid_label", "grid_label")),
    # Keep a compatibility alias for the typo in the initial notes.
    FacetSpec("nominal_resolution", ("cmip6:nominal_resolution", "nominal_resolution", "nominal_reoslutin")),
    FacetSpec("cf_standard_name", ("cmip6:cf_standard_name", "cf_standard_name", "standard_name", "cf:standard_name")),
    FacetSpec("frequency", ("cmip6:frequency", "frequency")),
)

# Backward-compatible aliases.
DEFAULT_STAC_ENDPOINT = CMIP6_DEFAULT_STAC_ENDPOINT
DEFAULT_COLLECTION = CMIP6_DEFAULT_COLLECTION
DEFAULT_FACETS = CMIP6_DEFAULT_FACETS


class FacetedSTACBrowser:
    """Filesystem-like browser over any STAC collection with facet paths.

    The class provides an ``ls``-style interface over a configured facet
    hierarchy. It maintains:
    - one lazy STAC client per browser instance, and
    - an in-memory result cache keyed by requested path.

    This separation keeps network logic encapsulated while preserving a simple
    call pattern for UI and interactive usage.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        collection: str,
        facets: Sequence[FacetSpec],
        max_items: int = 500,
        allowed_schemes: Sequence[str] = ("http", "https", "s3"),
        entity_kind: str = "all",
        memory_cache_max_entries: int = 1024,
        file_cache_path: str | Path | None = None,
        file_cache_max_entries: int = 16384,
    ) -> None:
        """Initialize a browser session.

        Parameters
        ----------
        endpoint : str
            STAC API base URL. Needed so callers can target production,
            staging, or alternative providers without code changes.
        collection : str
            Collection identifier (for example ``"cmip6"``). Required because
            many STAC catalogs host multiple collections.
        facets : Sequence[FacetSpec]
            Ordered virtual-directory schema used to interpret path segments.
            This controls both navigation order and query construction.
        max_items : int
            Upper bound on item iteration per query. Prevents very large
            responses from causing slow or memory-heavy directory listings.
        allowed_schemes : Sequence[str]
            URI schemes accepted when returning leaf assets (for example
            ``http``, ``https``, ``s3``). This filters non-data links and
            keeps output compatible with downstream readers.
        entity_kind : str
            Asset type filter for leaf results. Supported values:
            ``"all"`` (default), ``"netcdf"``, ``"zarr"``.
        memory_cache_max_entries : int
            Maximum number of path results retained in the in-memory cache.
            Set to 0 to disable memory caching.
        file_cache_path : str | Path | None
            Optional path to a SQLite cache file used to persist path results
            between browser instances. If ``None``, file caching is disabled.
        file_cache_max_entries : int
            Maximum number of entries retained in the file cache. Oldest
            entries are evicted first when this limit is exceeded.
        """
        self.endpoint = endpoint
        self.collection = collection
        self.facets = facets
        self.max_items = max_items
        self.allowed_schemes = allowed_schemes
        normalized_entity_kind = str(entity_kind).strip().lower()
        if normalized_entity_kind not in {"all", "netcdf", "zarr"}:
            raise ValueError("entity_kind must be one of: all, netcdf, zarr")
        self.entity_kind = normalized_entity_kind
        self.memory_cache_max_entries = max(0, int(memory_cache_max_entries))
        self.file_cache_path = Path(file_cache_path) if file_cache_path is not None else None
        self.file_cache_max_entries = max(0, int(file_cache_max_entries))

        self._client: Any = None
        self._result_cache: OrderedDict[str, list[str]] = OrderedDict()

    def _get_client(self) -> Any:
        """Return the lazily initialized ``pystac_client.Client`` instance.

        The client is created on first use so simply constructing a browser does
        not require immediate network or dependency availability.

        Returns
        -------
        Any
            Active STAC client object.

        Raises
        ------
        ImportError
            If ``pystac-client`` is not installed.
        """
        if self._client is None:
            try:
                from pystac_client import Client  # type: ignore[import-not-found]
            except ImportError as exc:
                raise ImportError(
                    "pystac-client is required for ESGF browsing. Install with: pip install pystac-client"
                ) from exc
            self._client = Client.open(self.endpoint)
        return self._client

    def ls(self, path: str = "") -> list[str]:
        """List entries for a virtual path in the facet hierarchy.

        The method behaves like a filesystem ``ls``:
        - returns directory-like entries (with trailing ``/``) at branch points,
        - auto-descends through singleton branches, and
        - returns asset URLs at leaf level.

        Parameters
        ----------
        path : str
            Slash-delimited facet values from root to current depth. Empty
            string means root listing.

        Returns
        -------
        list[str]
            Entries for the requested level. Directory entries end with ``/``;
            leaf results are filtered asset URL strings.

        Raises
        ------
        FileNotFoundError
            If the provided path is deeper than configured facets or contains an
            unknown segment for its parent path.
        """
        cached = self._cache_get(path)
        if cached is not None:
            return cached

        selected_values = _split_virtual_path(path)
        if len(selected_values) > len(self.facets):
            raise FileNotFoundError(
                f"Path '{path}' is deeper than the supported facet hierarchy ({len(self.facets)} levels)."
            )

        self._validate_selected_path(selected_values=selected_values)

        rolling_selected = list(selected_values)
        query = _build_query(facets=self.facets, selected_values=rolling_selected)

        items = self._fetch_items_with_alias_fallback(
            query=query,
            selected_values=rolling_selected,
        )

        # Auto-descend singleton branches until a branch point (or assets).
        while len(rolling_selected) < len(self.facets):
            next_facet = self.facets[len(rolling_selected)]
            values = _collect_facet_values(items=items, facet=next_facet)

            if len(values) == 0:
                # If no further facet values are exposed, return any assets now.
                result = _collect_asset_hrefs(
                    items=items,
                    allowed_schemes=self.allowed_schemes,
                    entity_kind=self.entity_kind,
                )
                self._cache_put(path, result)
                return result

            if len(values) > 1:
                result = [f"{value}/" for value in values]
                self._cache_put(path, result)
                return result

            # Singleton child: descend automatically and continue.
            rolling_selected.append(values[0])
            query = _build_query(facets=self.facets, selected_values=rolling_selected)
            items = self._fetch_items_with_alias_fallback(
                query=query,
                selected_values=rolling_selected,
            )

        result = _collect_asset_hrefs(
            items=items,
            allowed_schemes=self.allowed_schemes,
            entity_kind=self.entity_kind,
        )

        self._cache_put(path, result)
        return result

    def _cache_key(self, path: str) -> str:
        """Return a namespace-qualified cache key for file-backed storage."""
        facet_bits = [f"{facet.name}:{'|'.join(facet.aliases)}" for facet in self.facets]
        namespace = "::".join(
            [
                self.endpoint,
                self.collection,
                ",".join(facet_bits),
                str(self.max_items),
                ",".join(self.allowed_schemes),
                self.entity_kind,
            ]
        )
        return f"{namespace}||{path}"

    def _cache_get(self, path: str) -> list[str] | None:
        """Return cached result from memory or file cache, if available."""
        if path in self._result_cache:
            return list(self._result_cache[path])

        if self.file_cache_path is None or self.file_cache_max_entries == 0:
            return None

        key = self._cache_key(path)
        value = self._sqlite_get(key)
        if value is None:
            return None
        if self.memory_cache_max_entries > 0:
            self._cache_put_memory(path, value)
        return value

    def _cache_put(self, path: str, value: list[str]) -> None:
        """Store a result in configured caches while enforcing size limits."""
        if self.memory_cache_max_entries > 0:
            self._cache_put_memory(path, value)

        if self.file_cache_path is None or self.file_cache_max_entries == 0:
            return

        key = self._cache_key(path)
        self._sqlite_put(key, value)
        self._sqlite_evict_if_needed()

    def _cache_put_memory(self, path: str, value: list[str]) -> None:
        """Store one result in the in-memory cache with FIFO eviction."""
        self._result_cache.pop(path, None)
        self._result_cache[path] = list(value)
        while len(self._result_cache) > self.memory_cache_max_entries:
            self._result_cache.popitem(last=False)

    def _open_cache_db(self) -> sqlite3.Connection:
        """Open the SQLite cache database and ensure schema exists."""
        if self.file_cache_path is None:
            raise RuntimeError("File cache is not configured")

        self.file_cache_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.file_cache_path))
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cache_entries (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                seq INTEGER NOT NULL
            )
            """
        )
        conn.commit()
        return conn

    def _sqlite_get(self, key: str) -> list[str] | None:
        """Return a cached value from SQLite for the given key, if present."""
        try:
            with self._open_cache_db() as conn:
                row = conn.execute(
                    "SELECT value FROM cache_entries WHERE key = ?",
                    (key,),
                ).fetchone()
        except Exception:
            logger.warning("Unable to read SQLite cache at %s", self.file_cache_path)
            return None

        if row is None:
            return None

        try:
            import json

            parsed = json.loads(row[0])
            if isinstance(parsed, list):
                return [str(v) for v in parsed]
        except Exception:
            return None
        return None

    def _sqlite_put(self, key: str, value: list[str]) -> None:
        """Write or update a cache entry in SQLite.

        Updating an existing key refreshes its sequence so newest writes are
        retained when FIFO eviction runs.
        """
        try:
            import json

            encoded = json.dumps(value, separators=(",", ":"))
            seq = time.time_ns()
            with self._open_cache_db() as conn:
                conn.execute(
                    """
                    INSERT INTO cache_entries (key, value, seq)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value, seq=excluded.seq
                    """,
                    (key, encoded, seq),
                )
                conn.commit()
        except Exception:
            logger.warning("Unable to write SQLite cache at %s", self.file_cache_path)

    def _sqlite_evict_if_needed(self) -> None:
        """Evict oldest SQLite cache rows when max size is exceeded."""
        try:
            with self._open_cache_db() as conn:
                row = conn.execute("SELECT COUNT(*) FROM cache_entries").fetchone()
                count = int(row[0]) if row is not None else 0
                overflow = count - self.file_cache_max_entries
                if overflow > 0:
                    conn.execute(
                        """
                        DELETE FROM cache_entries
                        WHERE key IN (
                            SELECT key FROM cache_entries
                            ORDER BY seq ASC
                            LIMIT ?
                        )
                        """,
                        (overflow,),
                    )
                    conn.commit()
        except Exception:
            logger.warning("Unable to evict SQLite cache entries at %s", self.file_cache_path)

    def _validate_selected_path(self, *, selected_values: Sequence[str]) -> None:
        """Validate each path segment against available values at that depth.

        This is used to provide explicit filesystem-like errors for invalid
        paths, rather than returning empty listings that are ambiguous.

        Parameters
        ----------
        selected_values : Sequence[str]
            Parsed path segments to validate in order.

        Raises
        ------
        FileNotFoundError
            If any segment does not exist under its already-selected parent.
        """
        for depth, selected in enumerate(selected_values):
            parent_values = selected_values[:depth]
            parent_query = _build_query(facets=self.facets, selected_values=parent_values)
            parent_items = self._fetch_items_with_alias_fallback(
                query=parent_query,
                selected_values=parent_values,
            )

            facet = self.facets[depth]
            available = _collect_facet_values(items=parent_items, facet=facet)
            if selected not in available:
                parent_path = "/".join(parent_values) if parent_values else "<root>"
                raise FileNotFoundError(
                    f"No entry '{selected}' for facet '{facet.name}' under {parent_path}."
                )

    def _fetch_items_with_alias_fallback(
        self,
        *,
        query: dict[str, dict[str, str]],
        selected_values: Sequence[str],
    ) -> list[Any]:
        """Fetch items and retry query keys using facet aliases when needed.

        Some CEDA CMIP6 properties are mixed between namespaced and plain keys
        (for example ``realm`` vs ``cmip6:realm``). If the primary query
        returns no items, this method retries by swapping each selected facet's
        query key with its alternate aliases.

        Parameters
        ----------
        query : dict[str, dict[str, str]]
            Base STAC query built from selected path values.
        selected_values : Sequence[str]
            Path segments currently being queried. Needed to know which facet
            aliases are eligible for retry.

        Returns
        -------
        list[Any]
            Matching STAC items (possibly empty if no alias combination works).
        """
        items = list(self._iter_stac_items_internal(query=query))
        if items or not selected_values:
            return items

        # Retry by swapping each selected facet to alternate aliases one at a
        # time. This handles mixed property naming where a non-terminal facet
        # key differs (e.g. realm vs cmip6:realm).
        for depth, selected_value in enumerate(selected_values):
            facet = self.facets[depth]
            primary_key = facet.aliases[0] if facet.aliases else facet.name
            for alias in facet.aliases[1:]:
                if alias == primary_key:
                    continue
                retry_query = dict(query)
                retry_query.pop(primary_key, None)
                retry_query[alias] = {"eq": selected_value}
                retry_items = list(self._iter_stac_items_internal(query=retry_query))
                if retry_items:
                    return retry_items

        return items

    def _iter_stac_items_internal(
        self,
        *,
        query: dict[str, dict[str, str]],
    ) -> Iterable[Any]:
        """Run one STAC search and yield items up to ``max_items``.

        Parameters
        ----------
        query : dict[str, dict[str, str]]
            STAC query object in ``{"property": {"eq": value}}`` form.

        Returns
        -------
        Iterable[Any]
            Iterator of matching STAC items, truncated to configured limit.

        Raises
        ------
        Exception
            Propagates search/client exceptions after logging context.
        """
        try:
            client = self._get_client()
            search = client.search(collections=[self.collection], query=query)
            items = search.items()
            return islice(items, self.max_items)
        except Exception as exc:
            logger.error(
                f"Failed to query STAC endpoint {self.endpoint} collection {self.collection} with query {query}: {exc}",
                exc_info=True,
            )
            raise


class CMIP6STACBrowser(FacetedSTACBrowser):
    """CMIP6-configured browser over the default ESGF STAC endpoint.

    This subclass supplies CMIP6 collection defaults while still allowing
    callers to override endpoint, facets, and filtering behavior.
    """

    def __init__(
        self,
        *,
        endpoint: str = CMIP6_DEFAULT_STAC_ENDPOINT,
        collection: str = CMIP6_DEFAULT_COLLECTION,
        facets: Sequence[FacetSpec] = CMIP6_DEFAULT_FACETS,
        max_items: int = 500,
        allowed_schemes: Sequence[str] = ("http", "https", "s3"),
        entity_kind: str = "all",
        memory_cache_max_entries: int = 256,
        file_cache_path: str | Path | None = None,
        file_cache_max_entries: int = 1024,
    ) -> None:
        super().__init__(
            endpoint=endpoint,
            collection=collection,
            facets=facets,
            max_items=max_items,
            allowed_schemes=allowed_schemes,
            entity_kind=entity_kind,
            memory_cache_max_entries=memory_cache_max_entries,
            file_cache_path=file_cache_path,
            file_cache_max_entries=file_cache_max_entries,
        )


# Default browser instance for backward-compatible function interface
_default_browser: CMIP6STACBrowser | None = None


def cmip6_ls(
    path: str = "",
    *,
    endpoint: str = CMIP6_DEFAULT_STAC_ENDPOINT,
    collection: str = CMIP6_DEFAULT_COLLECTION,
    facets: Sequence[FacetSpec] = CMIP6_DEFAULT_FACETS,
    max_items: int = 5000,
    allowed_schemes: Sequence[str] = ("http", "https", "s3"),
    entity_kind: str = "all",
    memory_cache_max_entries: int = 256,
    file_cache_path: str | Path | None = None,
    file_cache_max_entries: int = 1024,
) -> list[str]:
    """Convenience wrapper around a shared ``CMIP6STACBrowser`` instance.

    This function is the simple public entry point for interactive use. It
    reuses a module-level browser when configuration matches, and rebuilds it
    when configuration changes.

    Parameters
    ----------
    path : str
        Slash-delimited facet path to list.
    endpoint : str
        STAC API base URL for the browser session.
    collection : str
        STAC collection identifier.
    facets : Sequence[FacetSpec]
        Ordered facet schema that defines the virtual directory hierarchy.
    max_items : int
        Per-query upper bound for item iteration.
    allowed_schemes : Sequence[str]
        URL schemes accepted for leaf asset output.
    entity_kind : str
        Asset type filter for leaf results: ``all``, ``netcdf``, or ``zarr``.
    memory_cache_max_entries : int
        Maximum number of path results retained in memory.
    file_cache_path : str | Path | None
        Optional path to a SQLite file used for persistent cache entries.
    file_cache_max_entries : int
        Maximum number of entries retained in the file cache.

    Returns
    -------
    list[str]
        Directory entries (``.../``) or leaf asset URLs.

    Raises
    ------
    FileNotFoundError
        If ``path`` is invalid under the configured hierarchy.
    """
    global _default_browser
    
    # Create or reuse default browser with matching config
    if _default_browser is None or (
        _default_browser.endpoint != endpoint
        or _default_browser.collection != collection
        or _default_browser.facets != facets
        or _default_browser.max_items != max_items
        or tuple(_default_browser.allowed_schemes) != tuple(allowed_schemes)
        or _default_browser.entity_kind != str(entity_kind).strip().lower()
        or _default_browser.memory_cache_max_entries != memory_cache_max_entries
        or _default_browser.file_cache_path != (Path(file_cache_path) if file_cache_path is not None else None)
        or _default_browser.file_cache_max_entries != file_cache_max_entries
    ):
        _default_browser = CMIP6STACBrowser(
            endpoint=endpoint,
            collection=collection,
            facets=facets,
            max_items=max_items,
            allowed_schemes=allowed_schemes,
            entity_kind=entity_kind,
            memory_cache_max_entries=memory_cache_max_entries,
            file_cache_path=file_cache_path,
            file_cache_max_entries=file_cache_max_entries,
        )
    
    return _default_browser.ls(path)


def _split_virtual_path(path: str) -> list[str]:
    """Normalize a user path into facet segments.

    Parameters
    ----------
    path : str
        User-provided path that may include whitespace or leading/trailing
        slashes.

    Returns
    -------
    list[str]
        Clean, non-empty path components.
    """
    cleaned = path.strip("/").strip()
    if not cleaned:
        return []
    return [part for part in cleaned.split("/") if part]


def _build_query(*, facets: Sequence[FacetSpec], selected_values: Sequence[str]) -> dict[str, dict[str, str]]:
    """Build a STAC ``eq`` query from selected facet path values.

    Parameters
    ----------
    facets : Sequence[FacetSpec]
        Ordered facet schema aligned with path positions.
    selected_values : Sequence[str]
        Path values chosen so far.

    Returns
    -------
    dict[str, dict[str, str]]
        Query mapping in STAC format. Uses each facet's primary alias as the
        initial query key.
    """
    query: dict[str, dict[str, str]] = {}
    for idx, selected in enumerate(selected_values):
        facet = facets[idx]
        # Query using cmip6: prefixed name if available
        query_name = facet.aliases[0] if facet.aliases else facet.name
        query[query_name] = {"eq": selected}
    return query


def _collect_facet_values(*, items: Sequence[Any], facet: FacetSpec) -> list[str]:
    """Extract distinct candidate values for one facet from STAC items.

    Parameters
    ----------
    items : Sequence[Any]
        STAC items to inspect.
    facet : FacetSpec
        Facet definition including property-key aliases to check.

    Returns
    -------
    list[str]
        Sorted unique values for the requested facet.
    """
    values: set[str] = set()
    for item in items:
        properties = getattr(item, "properties", {}) or {}
        for alias in facet.aliases:
            if alias not in properties:
                continue
            values.update(_normalize_to_strings(properties.get(alias)))
    return sorted(values)


def _collect_asset_hrefs(
    *,
    items: Sequence[Any],
    allowed_schemes: Sequence[str],
    entity_kind: str = "all",
) -> list[str]:
    """Collect distinct asset URLs from items, filtered by URI scheme.

    Parameters
    ----------
    items : Sequence[Any]
        STAC items whose assets should be scanned.
    allowed_schemes : Sequence[str]
        Lower/upper-case-insensitive URI schemes to keep.
    entity_kind : str
        Asset type filter for leaf results: ``all``, ``netcdf``, or ``zarr``.

    Returns
    -------
    list[str]
        Sorted unique asset ``href`` values that match allowed schemes.
    """
    allowed = {scheme.lower() for scheme in allowed_schemes}
    normalized_kind = str(entity_kind).strip().lower()
    hrefs: set[str] = set()
    for item in items:
        assets = getattr(item, "assets", {}) or {}
        for asset in assets.values():
            href = str(getattr(asset, "href", "")).strip()
            if not href:
                continue
            if urlparse(href).scheme.lower() in allowed:
                if _asset_matches_entity_kind(asset=asset, href=href, entity_kind=normalized_kind):
                    hrefs.add(href)
    return sorted(hrefs)


def _asset_matches_entity_kind(*, asset: Any, href: str, entity_kind: str) -> bool:
    """Return True when an asset matches the requested entity-kind filter."""
    if entity_kind == "all":
        return True

    href_l = href.lower()
    media_type = str(getattr(asset, "media_type", "")).lower()
    title = str(getattr(asset, "title", "")).lower()

    if entity_kind == "netcdf":
        if href_l.endswith((".nc", ".nc4", ".cdf")):
            return True
        if "netcdf" in media_type:
            return True
        return False

    if entity_kind == "zarr":
        if ".zarr" in href_l or href_l.endswith(("zarr.json", ".zmetadata", ".zgroup", ".zarray")):
            return True
        if "zarr" in media_type or "zarr" in title:
            return True
        return False

    # Constructor currently validates entity_kind; keep permissive fallback.
    return True


def _normalize_to_strings(value: Any) -> set[str]:
    """Normalize scalar or sequence property values to non-empty strings.

    Parameters
    ----------
    value : Any
        Property value from STAC item metadata. May be scalar, iterable, or
        ``None``.

    Returns
    -------
    set[str]
        Normalized string values with whitespace-trimmed empties removed.
    """
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        out: set[str] = set()
        for entry in value:
            entry_text = str(entry).strip()
            if entry_text:
                out.add(entry_text)
        return out
    value_text = str(value).strip()
    if not value_text:
        return set()
    return {value_text}


__all__ = [
    "CMIP6_DEFAULT_COLLECTION",
    "CMIP6_DEFAULT_FACETS",
    "CMIP6_DEFAULT_STAC_ENDPOINT",
    "DEFAULT_COLLECTION",
    "DEFAULT_FACETS",
    "DEFAULT_STAC_ENDPOINT",
    "CMIP6STACBrowser",
    "FacetedSTACBrowser",
    "FacetSpec",
    "cmip6_ls",
]
