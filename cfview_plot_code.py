import cf
import cfplot as cfp
from matplotlib import pyplot as plt

f = cf.read('/Users/bnl28/data/data/ecmwf/fixed_ecmwf_instants.nc')
fld = f[1]
fld.squeeze(inplace=True)

selection_spec = {'time': ('1754049600', '1756555200'), 'latitude': ('90.0', '-90.0'), 'longitude': ('0.0', '359.75')}
collapse_by_coord = {'time': 'mean'}


def _parse_bound(value):
    """Coerce serialized coordinate values into numeric scalars when possible."""
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


subspace_kwargs = {}
for coord_name, bounds in selection_spec.items():
    lo, hi = bounds
    lo = _parse_bound(lo)
    hi = _parse_bound(hi)
    print(coord_name, lo, hi)
    if lo == hi:
        subspace_kwargs[coord_name] = lo
    else:
        if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
            lo, hi = sorted((lo, hi))
        subspace_kwargs[coord_name] = cf.wi(lo, hi)

print('Subspacing')
fld = fld.subspace(**subspace_kwargs)

print('Collapsing')
# Apply collapses based on GUI selections
for axis, method in collapse_by_coord.items():
    if method == 'mean':
        fld = fld.collapse("mean", axes=axis, weights=False)
    else:
        fld = fld.collapse(method, axes=axis)

cfp.gopen(file='test.png')
cfp.con(fld)
cfp.gclose()