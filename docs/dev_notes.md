#### Running Tests

GUI tests default to Qt's offscreen platform so they do not open desktop
windows. Set `QT_QPA_PLATFORM=cocoa` on macOS (or the appropriate platform
plugin elsewhere) before running pytest if visible windows are needed.

Neither the application nor its tests require Docker (engine, CLI, or Python
SDK). Docker was previously used only to provision disposable S3 and HTTPS test
servers, not to provide application functionality. Remote S3/HTTPS access still
works with ordinary remote services.

Install test dependencies with `pip install -e ".[test]"`, or use
`environment-integration.yml`. Run `pytest -m "not integration"` for the fast
suite.

HTTPS integration tests run a loopback-only Python HTTPS server with a temporary
self-signed certificate, HTTP range support, and directory listing disabled.
They require `cryptography`, included in the test extra.

S3 integration tests run a native [SeaweedFS](https://github.com/seaweedfs/seaweedfs)
`weed mini` process. The integration conda environment and CI install version
**4.48** from conda-forge, which puts `weed` on the environment's `PATH`.
To add it to an existing conda environment, activate that environment and run:

```bash
conda install -c conda-forge seaweedfs=4.48
weed version
```

SeaweedFS is only a test dependency, not an application/runtime requirement.
No running service, root privileges, container runtime, or MinIO installation is needed. The fixture
uses temporary storage and randomly allocated loopback ports for all components,
then stops the process after the tests. SeaweedFS's usual S3 port is **8333**,
not MinIO's 9000; tests discover their allocated endpoint automatically.

Run `pytest -m integration -vv --durations=10` for the integration suite, or
`pytest` for everything. Without `weed`, S3 tests are explicitly marked with
`pytest.mark.skipif`; without `cryptography`, HTTPS tests are similarly marked.
An installed but broken SeaweedFS fails the tests with its startup log rather
than silently skipping. The full-month da193a cache tests also have an explicit
data-availability skip because those large files are not distributed.

Cache assertions measure real S3 response body bytes through a local counting
proxy, including range reads. They retain the second-read threshold of less than
4 KiB and do not depend on MinIO-specific metrics or settling sleeps.

Tests should use visible `pytest.mark.skip` / `pytest.mark.skipif` decorators for
optional prerequisites, not `pytest.importorskip` in shared configuration.
Required application dependencies such as `cf` and `pyfive` are imported
normally so a broken installation fails visibly. MinIO-style `~/.mc/config.json`
credential files remain supported for compatibility with existing S3 settings;
this file format does not require a MinIO server.

#### Field Selection Defaults

Source-tinted field rows use explicit black text on their light backgrounds,
independent of the system's light/dark palette. Unsaved generated fields retain
their red italic text; saving them switches to black text on the source tint.

Each single-field selection uses the same initial contour selection: the last
two varying coordinates retain their full range, and additional varying
coordinates (such as time and level) start at index zero. Once the worker
returns coordinates, the selected field is plotted automatically. This also
applies when switching between fields in the same file or returning to a
previously selected field. Multi-field selection does not trigger this plot.
Changing the field selection immediately clears and repaints the previous plot
before field details, coordinates, or the replacement plot are prepared.
Coordinate requests disable worker image emission so they cannot resend the
previous matplotlib figure while the new field's plot is being rendered.

#### Upstream Branches

From time to time we need to update from the branches into your working develolpment python. This is how I do that:

`pip install --upgrade git+https://github.com/USERNAME/REPOSITORY.git@BRANCH_NAME`

e.g.

```bash
pip install --upgrade "cfdm @ https://github.com/davidhassell/cfdm/archive/refs/heads/pyfive-netcdf.tar.gz"
pip install --upgrade "cf-python @ https://github.com/davidhassell/cf-python/archive/refs/heads/kerchunk-read.tar.gz"
pip install --upgrade git+https://github.com/bnlawrence/cf-plot.git@main
```

The cfdm and cf-python dependencies use branch archives rather than Git
checkouts because the upstream repositories contain paths that differ only
by case. On case-insensitive filesystems, pip's initial Git checkout can appear modified
and prevent switching to the requested branch. The archive avoids that
checkout and branch-switch step while still tracking `pyfive-netcdf` for cfdm
and `kerchunk-read` for cf-python.

Clearly we don't want to be doing this long-term!
