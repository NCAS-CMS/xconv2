# Get a list of fields in a file using their identity, things that look
# like <CF Field: air_pressure_at_mean_sea_level(time(30), latitude(721), longitude(1440)) Pa>
# and strip the gubbins off the front and back

# Emit list[str] so GUI transport and tests use a stable, serializable contract.
# FIXME: Expand this so it's more tutorial like and useful to readers of code
field_list = "fields = [f\"{x.identity()}\\x1f{str(x)}\\x1f{x.properties()}\" for x in f]\n"

