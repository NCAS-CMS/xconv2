# Get a list of fields in a file using their identity, things that look
# like <CF Field: air_pressure_at_mean_sea_level(time(30), latitude(721), longitude(1440)) Pa>
# and strip the gubbins off the front and back

#field_list = "fields = [x.__repr__()[11:-1] for x in f]\n"
field_list = "fields = [(x.identity(),str(x)) for x in f]\n"