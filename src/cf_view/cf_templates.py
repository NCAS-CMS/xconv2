# Get a list of fields in a file using their identity, things that look
# like <CF Field: air_pressure_at_mean_sea_level(time(30), latitude(721), longitude(1440)) Pa>
# and strip the gubbins off the front and back

import textwrap

# Emit list[str] so GUI transport and tests use a stable, serializable contract.
# FIXME: Expand this so it's more tutorial like and useful to readers of code
field_list = "fields = [f\"{x.identity()}\\x1f{str(x)}\\x1f{x.properties()}\" for x in f]\n"

# Shared collapse options for GUI selection and future worker command expansion.
collapse_methods = ["mean", "range", "max", "min"]




def coordinate_list(index: int) -> str:
    """Generate worker code that emits 1D dimension-coordinate values for a field."""
    return textwrap.dedent(
        f"""
        fld = f[{index}]
        fld.squeeze(inplace=True) # make it easier for the GUI to handle coordinates with length 1
        coords = []
        for key in fld.dimension_coordinates():
            c = fld.coordinate(key)
            arr = getattr(c, 'array', None)
            if arr is None:
                continue
            if len(arr) <= 1:
                continue
            vals = [str(x) for x in arr]
            coords.append((c.identity(default='unknown'), vals))
        send_to_gui('COORD', coords)
        """
    ).lstrip()
    

