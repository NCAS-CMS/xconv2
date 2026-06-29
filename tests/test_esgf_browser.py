from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import pytest

import xconv2.esgf_browser as esgf_browser
from xconv2.esgf_browser import CMIP6STACBrowser, DEFAULT_FACETS, FacetSpec, cmip6_ls


@dataclass
class _Asset:
    href: str


@dataclass
class _Item:
    properties: dict
    assets: dict[str, _Asset]


class _FakeSearch:
    def __init__(self, items):
        self._items = items

    def items(self):
        return iter(self._items)


class _FakeClient:
    def __init__(self, items):
        self._items = items

    def search(self, *, collections, query):
        return _FakeSearch(self._items)


def test_split_virtual_path_normalizes_slashes() -> None:
    assert esgf_browser._split_virtual_path("/a/b/c/") == ["a", "b", "c"]
    assert esgf_browser._split_virtual_path("   ") == []


def test_cmip6_ls_returns_next_facet_values(monkeypatch) -> None:
    items = [
        _Item(properties={"cmip6:activity_id": "CMIP"}, assets={}),
        _Item(properties={"cmip6:activity_id": "DCPP"}, assets={}),
        _Item(properties={"cmip6:activity_id": "CMIP"}, assets={}),
    ]

    monkeypatch.setattr(CMIP6STACBrowser, "_get_client", lambda self: _FakeClient(items))
    esgf_browser._default_browser = None

    assert cmip6_ls("") == ["CMIP/", "DCPP/"]


def test_cmip6_ls_returns_leaf_asset_urls(monkeypatch) -> None:
    full_path = "/".join([f"value{i}" for i in range(len(DEFAULT_FACETS))])
    properties = {
        facet.aliases[0] if facet.aliases else facet.name: f"value{i}"
        for i, facet in enumerate(DEFAULT_FACETS)
    }
    items = [
        _Item(
            properties=properties,
            assets={
                "http": _Asset(href="https://example.org/data/file1.nc"),
                "s3": _Asset(href="s3://bucket/path/file1.nc"),
                "ftp": _Asset(href="ftp://example.org/file1.nc"),
            },
        )
    ]

    monkeypatch.setattr(CMIP6STACBrowser, "_get_client", lambda self: _FakeClient(items))

    browser = CMIP6STACBrowser(facets=DEFAULT_FACETS)
    assert browser.ls(full_path) == [
        "https://example.org/data/file1.nc",
        "s3://bucket/path/file1.nc",
    ]


def test_cmip6_ls_honors_alias_facets(monkeypatch) -> None:
    alias_only_facets = (
        FacetSpec("variant_id", ("variant_id", "variant_label")),
    )
    items = [
        _Item(properties={"variant_label": "r1i1p1f1"}, assets={}),
        _Item(properties={"variant_label": "r2i1p1f1"}, assets={}),
    ]

    monkeypatch.setattr(CMIP6STACBrowser, "_get_client", lambda self: _FakeClient(items))

    browser = CMIP6STACBrowser(facets=alias_only_facets)
    assert browser.ls("") == ["r1i1p1f1/", "r2i1p1f1/"]


def test_cmip6_ls_raises_import_error_when_pystac_missing(monkeypatch) -> None:
    """Verify that ImportError is raised when pystac-client is not available."""

    def _raise_import_error(self):
        raise ImportError("pystac-client is required")

    monkeypatch.setattr(CMIP6STACBrowser, "_get_client", _raise_import_error)

    browser = CMIP6STACBrowser()
    with pytest.raises(ImportError, match="pystac-client"):
        browser.ls("")


def test_cmip6_ls_live_api_available() -> None:
    """Test that live CMIP6 STAC API is available and queryable."""
    try:
        import pystac_client  # noqa: F401
    except ImportError:
        pytest.skip("pystac-client not installed")

    esgf_browser._default_browser = None
    result = cmip6_ls("")
    assert isinstance(result, list)
    assert all(item.endswith("/") for item in result), f"Expected directories, got: {result[:5]}"
    assert len(result) > 0, "Live CMIP6 API returned no results"


def test_stac_browser_caches_results(monkeypatch) -> None:
    """Test that STACBrowser caches query results."""
    items = [
        _Item(properties={"cmip6:activity_id": "CMIP"}, assets={}),
        _Item(properties={"cmip6:activity_id": "DCPP"}, assets={}),
    ]

    call_count = {"n": 0}

    class _CountingClient(_FakeClient):
        def search(self, *, collections, query):
            call_count["n"] += 1
            return super().search(collections=collections, query=query)

    monkeypatch.setattr(CMIP6STACBrowser, "_get_client", lambda self: _CountingClient(items))

    browser = CMIP6STACBrowser()

    result1 = browser.ls("")
    result2 = browser.ls("")
    assert result1 == result2
    assert len(browser._result_cache) == 1
    assert call_count["n"] == 1


def test_stac_browser_retries_with_alternate_alias(monkeypatch) -> None:
    """When primary query alias yields no items, browser retries alternate alias."""
    items = [
        _Item(
            properties={
                "cmip6:activity_id": "CMIP",
                "realm": "atmos",
                "cmip6:grid_label": "gn",
            },
            assets={"data": _Asset(href="https://example.org/data/file1.nc")},
        )
    ]

    class _RealmAliasClient:
        def search(self, *, collections, query):
            # Simulate endpoint behavior where cmip6:realm filter matches nothing,
            # but plain realm filter matches.
            if "cmip6:realm" in query:
                return _FakeSearch([])
            if "realm" in query:
                return _FakeSearch(items)
            return _FakeSearch(items)

    monkeypatch.setattr(CMIP6STACBrowser, "_get_client", lambda self: _RealmAliasClient())

    browser = CMIP6STACBrowser(
        facets=(
            FacetSpec("activity_id", ("cmip6:activity_id", "activity_id")),
            FacetSpec("realm", ("cmip6:realm", "realm")),
            FacetSpec("grid_label", ("cmip6:grid_label", "grid_label")),
        )
    )

    assert browser.ls("CMIP/atmos") == ["https://example.org/data/file1.nc"]


def test_stac_browser_retries_non_terminal_alias(monkeypatch) -> None:
    """Fallback should work when an earlier facet alias is wrong (not only last)."""
    items = [
        _Item(
            properties={
                "cmip6:activity_id": "CMIP",
                "realm": "atmos",
                "cmip6:grid_label": "gn",
                "cmip6:nominal_resolution": "250 km",
            },
            assets={"data": _Asset(href="https://example.org/data/file1.nc")},
        )
    ]

    class _RealmAliasClient:
        def search(self, *, collections, query):
            # Any query using cmip6:realm should fail to match, mimicking
            # endpoint behavior for this field naming.
            if "cmip6:realm" in query:
                return _FakeSearch([])
            if "realm" in query:
                return _FakeSearch(items)
            return _FakeSearch(items)

    monkeypatch.setattr(CMIP6STACBrowser, "_get_client", lambda self: _RealmAliasClient())

    browser = CMIP6STACBrowser(
        facets=(
            FacetSpec("activity_id", ("cmip6:activity_id", "activity_id")),
            FacetSpec("realm", ("cmip6:realm", "realm")),
            FacetSpec("grid_label", ("cmip6:grid_label", "grid_label")),
            FacetSpec("nominal_resolution", ("cmip6:nominal_resolution", "nominal_resolution")),
        )
    )

    assert browser.ls("CMIP/atmos/gn") == ["https://example.org/data/file1.nc"]


def test_stac_browser_auto_unrolls_singleton_children(monkeypatch) -> None:
    """Singleton branches should auto-descend until a branch point is reached."""
    items = [
        _Item(
            properties={
                "cmip6:activity_id": "CMIP",
                "cmip6:experiment_id": "historical",
                "cmip6:source_id": "UKESM1-0-LL",
                "cmip6:institution_id": "MOHC",
                "cmip6:table_id": "Amon",
                "cmip6:variable_id": "tas",
                "cmip6:variant_label": "r1i1p1f2",
                "realm": "atmos",
                "cmip6:grid_label": "gn",
                "cmip6:nominal_resolution": "250 km",
                "cmip6:cf_standard_name": "air_temperature",
                "cmip6:frequency": "mon",
            },
            assets={"data": _Asset(href="https://example.org/data/file1.nc")},
        )
    ]

    monkeypatch.setattr(CMIP6STACBrowser, "_get_client", lambda self: _FakeClient(items))

    browser = CMIP6STACBrowser()
    # Starts at nominal_resolution and auto-descends over cf_standard_name and
    # frequency to return leaf assets.
    path = "CMIP/historical/UKESM1-0-LL/MOHC/Amon/tas/r1i1p1f2/atmos/gn/250 km"
    assert browser.ls(path) == ["https://example.org/data/file1.nc"]


def test_stac_browser_raises_for_unknown_path_segment(monkeypatch) -> None:
    """Unknown facet values should raise FileNotFoundError, not return []."""
    items = [_Item(properties={"cmip6:activity_id": "CMIP"}, assets={})]

    monkeypatch.setattr(CMIP6STACBrowser, "_get_client", lambda self: _FakeClient(items))

    browser = CMIP6STACBrowser()
    with pytest.raises(FileNotFoundError, match="No entry 'fred'"):
        browser.ls("fred")


def test_stac_browser_raises_for_path_deeper_than_facets() -> None:
    """Path deeper than facet hierarchy should raise FileNotFoundError."""
    browser = CMIP6STACBrowser(facets=(FacetSpec("only", ("only",)),))
    with pytest.raises(FileNotFoundError, match="deeper than the supported facet hierarchy"):
        browser.ls("a/b")


def test_memory_cache_evicts_oldest_entry(monkeypatch) -> None:
    """In-memory cache should evict oldest entries when max size is exceeded."""
    items = [
        _Item(properties={"cmip6:activity_id": "CMIP"}, assets={}),
        _Item(properties={"cmip6:activity_id": "DCPP"}, assets={}),
    ]

    monkeypatch.setattr(CMIP6STACBrowser, "_get_client", lambda self: _FakeClient(items))

    browser = CMIP6STACBrowser(memory_cache_max_entries=2)
    browser.ls("")
    browser.ls("CMIP")
    browser.ls("DCPP")

    assert len(browser._result_cache) == 2
    assert "" not in browser._result_cache
    assert "CMIP" in browser._result_cache
    assert "DCPP" in browser._result_cache


def test_file_cache_persists_across_instances_and_evicts_oldest(monkeypatch, tmp_path) -> None:
    """SQLite file cache should persist results and enforce FIFO eviction."""
    items = [
        _Item(properties={"cmip6:activity_id": "CMIP"}, assets={}),
        _Item(properties={"cmip6:activity_id": "DCPP"}, assets={}),
    ]

    call_count = {"n": 0}

    class _CountingClient(_FakeClient):
        def search(self, *, collections, query):
            call_count["n"] += 1
            return super().search(collections=collections, query=query)

    cache_file = tmp_path / "esgf_cache.sqlite"

    monkeypatch.setattr(CMIP6STACBrowser, "_get_client", lambda self: _CountingClient(items))

    browser1 = CMIP6STACBrowser(
        memory_cache_max_entries=0,
        file_cache_path=cache_file,
        file_cache_max_entries=2,
    )
    browser1.ls("")
    browser1.ls("CMIP")
    browser1.ls("DCPP")

    assert call_count["n"] >= 3
    assert cache_file.exists()

    first_count = call_count["n"]

    browser2 = CMIP6STACBrowser(
        memory_cache_max_entries=0,
        file_cache_path=cache_file,
        file_cache_max_entries=2,
    )
    # Should hit file cache for most recent entries.
    assert browser2.ls("CMIP") == []
    assert browser2.ls("DCPP") == []
    assert call_count["n"] == first_count

    # Oldest key should have been evicted from file cache.
    with sqlite3.connect(cache_file) as conn:
        keys = [row[0] for row in conn.execute("SELECT key FROM cache_entries")]
    assert browser1._cache_key("") not in keys


def test_entity_kind_filter_netcdf_excludes_non_netcdf(monkeypatch) -> None:
    """entity_kind='netcdf' should drop thumbnails/kerchunk/zarr assets."""
    full_path = "/".join([f"value{i}" for i in range(len(DEFAULT_FACETS))])
    properties = {
        facet.aliases[0] if facet.aliases else facet.name: f"value{i}"
        for i, facet in enumerate(DEFAULT_FACETS)
    }
    items = [
        _Item(
            properties=properties,
            assets={
                "nc": _Asset(href="https://example.org/data/file1.nc"),
                "thumb": _Asset(href="https://example.org/thumbs/file.jpg"),
                "kerchunk": _Asset(href="https://example.org/meta/file.kr1.0.json"),
                "zarr": _Asset(href="https://example.org/store/data.zarr"),
            },
        )
    ]

    monkeypatch.setattr(CMIP6STACBrowser, "_get_client", lambda self: _FakeClient(items))

    browser = CMIP6STACBrowser(entity_kind="netcdf")
    assert browser.ls(full_path) == ["https://example.org/data/file1.nc"]


def test_entity_kind_filter_zarr_excludes_netcdf(monkeypatch) -> None:
    """entity_kind='zarr' should return only zarr-like assets."""
    full_path = "/".join([f"value{i}" for i in range(len(DEFAULT_FACETS))])
    properties = {
        facet.aliases[0] if facet.aliases else facet.name: f"value{i}"
        for i, facet in enumerate(DEFAULT_FACETS)
    }
    items = [
        _Item(
            properties=properties,
            assets={
                "nc": _Asset(href="https://example.org/data/file1.nc"),
                "zarr": _Asset(href="https://example.org/store/data.zarr"),
                "zmeta": _Asset(href="https://example.org/store/data/.zmetadata"),
                "thumb": _Asset(href="https://example.org/thumbs/file.jpg"),
            },
        )
    ]

    monkeypatch.setattr(CMIP6STACBrowser, "_get_client", lambda self: _FakeClient(items))

    browser = CMIP6STACBrowser(entity_kind="zarr")
    assert browser.ls(full_path) == [
        "https://example.org/store/data.zarr",
        "https://example.org/store/data/.zmetadata",
    ]


def test_entity_kind_invalid_value_raises() -> None:
    with pytest.raises(ValueError, match="entity_kind"):
        CMIP6STACBrowser(entity_kind="thumbnail")
