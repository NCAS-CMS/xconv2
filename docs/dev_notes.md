#### Running Tests

GUI tests default to Qt's offscreen platform so they do not open desktop
windows. Set `QT_QPA_PLATFORM=cocoa` on macOS (or the appropriate platform
plugin elsewhere) before running pytest if visible windows are needed.

Use `pytest -m "not integration"` for the fast suite. Docker-backed remote
integration tests require MinIO and can take several minutes: cache tests
deliberately wait 12 seconds after reads for MinIO's metrics to update.
Run `pytest -vv --durations=10 tests/test_worker_remote_integration.py` to see
which test is running and the slowest completed tests.

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
