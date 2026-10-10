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
