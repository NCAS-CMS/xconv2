from __future__ import annotations

import json
from pathlib import Path

import pytest

from xconv2 import science_environment


class _Distribution:
    def __init__(self, name, version="1.0", installer=None, origin=None, files=(), base="/site"):
        self.metadata = {"Name": name}
        self.version = version
        self.installer = installer
        self.origin = origin
        self.files = files
        self.base = Path(base)

    def read_text(self, filename):
        return self.installer if filename == "INSTALLER" else self.origin

    def locate_file(self, file):
        return self.base / file


@pytest.fixture
def metadata(monkeypatch):
    def install(distributions, mapping=None):
        monkeypatch.setattr(science_environment, "distributions", lambda: distributions)
        monkeypatch.setattr(science_environment, "packages_distributions", lambda: mapping or {})
    install([])
    return install


@pytest.mark.parametrize(("installer", "origin", "expected"), [
    ("conda", '{"dir_info": {}, "url": "file:///build/work"}', "Conda"),
    ("pip\n", None, "pip index"),
    (None, None, "unknown source"),
    ("custom", None, "unknown source"),
    ("pip", '{"dir_info": {"editable": true}}', "local editable"),
    ("pip", '{"dir_info": {}}', "local source"),
    ("pip", '{"archive_info": {}}', "direct archive/URL"),
    ("pip", '{"vcs_info": {"vcs": "git", "commit_id": "abc", "requested_revision": "main"}}',
     "git; ref: main; commit: abc"),
])
def test_installation_sources(metadata, installer, origin, expected):
    metadata([_Distribution("numpy", installer=installer, origin=origin, files=["numpy/__init__.py"])])
    report = science_environment.science_environment_report(["numpy: 1.0 /site/numpy/__init__.py"])
    assert f"[{expected}]" in report
    assert "/build/work" not in report


def test_only_stack_packages_are_reported_and_import_names_are_mapped(metadata):
    metadata([
        _Distribution("cf-python", installer="pip", files=["cf/__init__.py"]),
        _Distribution("cf-plot", installer="pip", origin='{"dir_info": {"editable": true}}',
                      files=["cfplot/__init__.py"]),
        _Distribution("cfdm", installer="conda", files=["cfdm/core/__init__.py"]),
        _Distribution("unrelated-package"),
    ])
    report = science_environment.science_environment_report([
        "cf: 1.0 /site/cf/__init__.py",
        "cfplot: 1.0 /site/cfplot/__init__.py",
        "cfdm.core: 1.0 /site/cfdm/core/__init__.py",
    ])
    assert "cf: 1.0 /site/cf/__init__.py [pip index]" in report
    assert "cfplot: 1.0 /site/cfplot/__init__.py [local editable]" in report
    assert "cfdm.core: 1.0 /site/cfdm/core/__init__.py [Conda]" in report
    assert "unrelated-package" not in report
    assert "Installation sources" not in report


def test_duplicate_numpy_metadata_matches_runtime_version_and_warns(metadata, caplog):
    metadata([
        _Distribution("numpy", "2.4.4", "pip", files=["numpy/__init__.py"]),
        _Distribution("numpy", "2.4.6", "conda", files=["numpy/__init__.py"]),
    ], {"numpy": ["numpy", "numpy"]})
    report = science_environment.science_environment_report(["numpy: 2.4.6 /site/numpy/__init__.py"])
    assert report.count("numpy:") == 1
    assert "[Conda; WARNING: multiple metadata records: numpy 2.4.4, numpy 2.4.6]" in report
    assert "Multiple installation metadata records for numpy" in caplog.text


def test_repeated_discovery_of_same_metadata_is_not_a_conflict(metadata, caplog):
    distribution = _Distribution("numpy", installer="conda", files=["numpy/__init__.py"])
    metadata([distribution, distribution])
    report = science_environment.science_environment_report(["numpy: 1.0 /site/numpy/__init__.py"])
    assert "[Conda]" in report
    assert "WARNING" not in report
    assert not caplog.text


def test_import_path_prevents_unrelated_editable_checkout_from_being_selected(metadata):
    metadata([
        _Distribution("PyActiveStorage", "0.4.0", "pip", files=["activestorage/__init__.py"]),
        _Distribution("ActiveStorage", "0.4.0", "pip",
                      origin=json.dumps({"dir_info": {"editable": True}, "url": "file:///checkout"})),
    ], {"activestorage": ["PyActiveStorage", "ActiveStorage"]})
    report = science_environment.science_environment_report(["activestorage: 0.4.0 /site/activestorage/__init__.py"])
    assert "[pip index;" in report
    assert "local editable" not in report
    assert "WARNING: multiple metadata records" in report


def test_editable_checkout_path_is_matched_without_recorded_source_files(metadata):
    metadata([_Distribution("cf-plot", installer="pip", origin=json.dumps({
        "dir_info": {"editable": True}, "url": "file:///checkout%20space",
    }))])
    report = science_environment.science_environment_report(["cfplot: 1.0 /checkout space/cfplot/__init__.py"])
    assert "[local editable]" in report


def test_conflicting_same_version_metadata_is_not_arbitrarily_selected(metadata):
    metadata([
        _Distribution("numpy", installer="pip", files=["numpy/__init__.py"]),
        _Distribution("numpy", installer="conda", files=["/site/numpy/__init__.py"], base="/other"),
    ])
    report = science_environment.science_environment_report(["numpy: 1.0 /site/numpy/__init__.py"])
    assert "[unknown source; WARNING:" in report


def test_unmatched_metadata_path_is_unknown(metadata):
    metadata([_Distribution("numpy", installer="conda", files=["numpy/__init__.py"])])
    assert "[unknown source]" in science_environment.science_environment_report(
        ["numpy: 1.0 /other/numpy/__init__.py"])


def test_runtime_metadata_version_mismatch_is_reported(metadata):
    metadata([_Distribution("numpy", "2.0", "conda", files=["numpy/__init__.py"])])
    assert "[Conda; metadata version: 2.0]" in science_environment.science_environment_report(
        ["numpy: 1.0 /site/numpy/__init__.py"])


@pytest.mark.parametrize("origin", ["{broken", "[]", "{}", '{"vcs_info": "invalid"}',
                                  '{"vcs_info": {"vcs": "git"}}'])
def test_invalid_metadata_is_reported_and_logged(metadata, caplog, origin):
    metadata([_Distribution("cfdm", origin=origin, files=["cfdm/__init__.py"])])
    report = science_environment.science_environment_report(["cfdm: 1.0 /site/cfdm/__init__.py"])
    assert "metadata error" in report
    assert "Cannot read installation provenance for cfdm" in caplog.text


def test_unreadable_metadata_does_not_hide_other_libraries(metadata, caplog):
    class _UnreadableDistribution(_Distribution):
        def read_text(self, filename):
            raise OSError("Unreadable metadata")

    metadata([
        _UnreadableDistribution("numpy", files=["numpy/__init__.py"]),
        _Distribution("cfdm", installer="conda", files=["cfdm/__init__.py"]),
    ])
    report = science_environment.science_environment_report([
        "numpy: 1.0 /site/numpy/__init__.py", "cfdm: 1.0 /site/cfdm/__init__.py",
    ])
    assert "metadata error" in report
    assert "cfdm: 1.0 /site/cfdm/__init__.py [Conda]" in report
    assert "Unreadable metadata" in caplog.text


def test_native_libraries_platform_python_and_unavailable_modules_are_unchanged(metadata):
    lines = ["Platform: macOS", "Python: 3.12 /bin/python", "HDF5 library: 1.14",
             "udunits2 library: /lib/udunits.so", "esmpy/ESMF: not available"]
    report = science_environment.science_environment_report(lines)
    assert "\n".join(lines) in report


def test_sources_omit_urls_and_keep_vcs_ref_and_commit(metadata):
    metadata([_Distribution("cf-python", installer="pip", files=["cf/__init__.py"], origin=json.dumps({
        "url": "https://example.org/private",
        "vcs_info": {"vcs": "git", "requested_revision": "main", "commit_id": "abc"},
    }))])
    report = science_environment.science_environment_report(["cf: 1.0 /site/cf/__init__.py"])
    assert "[git; ref: main; commit: abc]" in report
    assert "example.org" not in report


@pytest.mark.parametrize("as_string", [False, True])
def test_report_redacts_home_directory_from_all_paths(metadata, monkeypatch, as_string):
    monkeypatch.setattr(science_environment.Path, "home", lambda: Path("/Users/alice"))
    lines = ["cf: 1.0 /Users/alice/env/cf/__init__.py", "other: 1.0 /opt/other/__init__.py"]
    report = science_environment.science_environment_report("\n".join(lines) if as_string else lines)
    assert "/Users/alice" not in report
    assert "cf: 1.0 ~/env/cf/__init__.py" in report
    assert "other: 1.0 /opt/other/__init__.py" in report


@pytest.mark.parametrize(("home", "text", "expected"), [
    ("/Users/alice", "/Users/alice-other/pkg /Users/alice2/pkg", "/Users/alice-other/pkg /Users/alice2/pkg"),
    ("/Users/a.b", "/Users/a.b/pkg /Users/axb/pkg", "~/pkg /Users/axb/pkg"),
    ("/home/alice", "/home/alice\n/home/alice/pkg", "~\n~/pkg"),
    (r"C:\Users\alice", r"C:\Users\alice\env\pkg", r"~\env\pkg"),
    (r"C:\Users\alice", "C:/Users/alice/env/pkg", "~/env/pkg"),
    ("/Users/alice", "File not found: '/Users/alice/env/pkg'", "File not found: '~/env/pkg'"),
])
def test_redaction_respects_path_boundaries_and_handles_errors(monkeypatch, home, text, expected):
    monkeypatch.setattr(science_environment.Path, "home", lambda: Path(home))
    assert science_environment.redact_home_directory(text) == expected
