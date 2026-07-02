#### Upstream Branches

#### Remote Cache Diagnostics

- See [dev_remote_cache_diagnostics.md](dev_remote_cache_diagnostics.md) for cache-miss causes, diagnostics toggle usage, and quick triage steps.

From time to time we need to update from the branches into your working develolpment python. This is how I do that:

`pip install --upgrade git+https://github.com/USERNAME/REPOSITORY.git@BRANCH_NAME`

e.g.

pip install --upgrade git+https://github.com/davidhassell/cfdm.git@pyfive-netcdf
pip install --upgrade git+https://github.com/davidhassell/cf-python.git@kerchunk-read
pip install --upgrade git+https://github.com/bnlawrence/cf-plot.git@main