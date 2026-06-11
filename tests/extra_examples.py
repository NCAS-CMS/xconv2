"""
cf_example_fields_new.py
========================
Two new example field constructs for cf-python, ready to be used as
candidates for inclusion alongside the existing cf.example_field() set.

Field A  (index suggestion: 14)
    air_temperature  modelled on a real output file
    Dimensions: time(4) x model_level_number(5) x latitude(8) x longitude(10)
    Highlights:
      - atmosphere_hybrid_height_coordinate reference (with a, b, orog ancillaries)
      - level_height and sigma auxiliary coordinates, each with bounds
      - forecast_period (1-D, co-indexed with time)
      - forecast_reference_time and realization as scalar-like auxiliary coords
      - latitude_longitude coordinate reference with earth_radius datum

Field B  (index suggestion: 15)
    sea_water_potential_temperature on a curvilinear ocean model grid.
    Dimensions: time(3) x depth(4) x nj(9) x ni(11)
    Highlights:
      - 2-D latitude(nj, ni) and longitude(nj, ni) auxiliary coordinates
        (the defining feature of curvilinear / tripolar ocean grids)
      - Native (i, j) dimension coordinates with no direct geographic meaning
      - depth with cell bounds
      - latitude_longitude coordinate reference

Usage
-----
    import cf
    import cf_example_fields_new as ex

    f = ex.example_field_A()
    g = ex.example_field_B()

    print(f)
    print(g)

    # Write to netCDF
    cf.write(f, 'example_A.nc')
    cf.write(g, 'example_B.nc')
"""

import numpy as np
import cf


def example_field_A():
    """Return an air_temperature field  modelled on a real output file

    The field has:
      - time(4) x model_level_number(5) x latitude(8) x longitude(10)
      - atmosphere_hybrid_height_coordinate with a, b, and surface_altitude
        domain ancillaries
      - level_height and sigma auxiliary coordinates (each with bounds)
      - forecast_period co-indexed with time
      - forecast_reference_time and realization as scalar (size-1) aux coords
      - latitude_longitude grid mapping with earth_radius datum

    Returns
    -------
    cf.Field
    """
    np.random.seed(42)

    f = cf.Field()
    f.set_properties({
        'standard_name': 'air_temperature',
        'long_name': 'TEMPERATURE ON THETA LEVELS',
        'units': 'K',
        'Conventions': 'CF-1.13',
        'institution': 'CF Example Factory',
    })

    # Domain axes
    ax_t  = f.set_construct(cf.DomainAxis(4),  key='domainaxis0')
    ax_z  = f.set_construct(cf.DomainAxis(5),  key='domainaxis1')
    ax_y  = f.set_construct(cf.DomainAxis(8),  key='domainaxis2')
    ax_x  = f.set_construct(cf.DomainAxis(10), key='domainaxis3')
    ax_sc = f.set_construct(cf.DomainAxis(1),  key='domainaxis4')  # for scalar-like aux coords

    # Main data array
    f.set_data(
        cf.Data(np.random.uniform(200, 320, (4, 5, 8, 10)).astype('f4'), units='K'),
        axes=(ax_t, ax_z, ax_y, ax_x))

    # ------------------------------------------------------------------ #
    # Dimension coordinates
    # ------------------------------------------------------------------ #

    # time
    f.set_construct(
        cf.DimensionCoordinate(
            properties={'standard_name': 'time', 'axis': 'T',
                        'units': 'hours since 1970-01-01 00:00:00',
                        'calendar': 'gregorian'},
            data=cf.Data(np.array([0., 3., 6., 9.], dtype='f4'))),
        axes=(ax_t,), key='dimensioncoordinate0')

    # model_level_number
    f.set_construct(
        cf.DimensionCoordinate(
            properties={'standard_name': 'model_level_number', 'units': '1',
                        'axis': 'Z', 'positive': 'up'},
            data=cf.Data(np.array([1, 2, 3, 4, 5], dtype='i4'))),
        axes=(ax_z,), key='dimensioncoordinate1')

    # latitude
    f.set_construct(
        cf.DimensionCoordinate(
            properties={'standard_name': 'latitude', 'units': 'degrees_north', 'axis': 'Y'},
            data=cf.Data(np.array([-75., -45., -15., 0., 15., 30., 45., 75.], dtype='f4'))),
        axes=(ax_y,), key='dimensioncoordinate2')

    # longitude
    f.set_construct(
        cf.DimensionCoordinate(
            properties={'standard_name': 'longitude', 'units': 'degrees_east', 'axis': 'X'},
            data=cf.Data(np.array([0., 36., 72., 108., 144., 180., 216., 252., 288., 324.],
                                  dtype='f4'))),
        axes=(ax_x,), key='dimensioncoordinate3')

    # ------------------------------------------------------------------ #
    # Auxiliary coordinates
    # ------------------------------------------------------------------ #

    # level_height with bounds (on model levels)
    lh_vals = np.array([20., 80., 200., 400., 700.], dtype='f4')
    lh_bnds = np.array([[0., 50.], [50., 140.], [140., 300.],
                         [300., 550.], [550., 900.]], dtype='f4')
    f.set_construct(
        cf.AuxiliaryCoordinate(
            properties={'long_name': 'level_height', 'units': 'm', 'positive': 'up'},
            data=cf.Data(lh_vals, units='m'),
            bounds=cf.Bounds(data=cf.Data(lh_bnds, units='m'))),
        axes=(ax_z,), key='auxiliarycoordinate0')

    # sigma with bounds (on model levels)
    sigma_vals = np.array([0.9973, 0.9893, 0.9751, 0.9546, 0.9237], dtype='f4')
    sigma_bnds = np.array([[1.000, 0.993], [0.993, 0.982], [0.982, 0.965],
                            [0.965, 0.939], [0.939, 0.908]], dtype='f4')
    f.set_construct(
        cf.AuxiliaryCoordinate(
            properties={'long_name': 'sigma', 'units': '1'},
            data=cf.Data(sigma_vals, units='1'),
            bounds=cf.Bounds(data=cf.Data(sigma_bnds, units='1'))),
        axes=(ax_z,), key='auxiliarycoordinate1')

    # forecast_period (1-D, same axis as time)
    f.set_construct(
        cf.AuxiliaryCoordinate(
            properties={'standard_name': 'forecast_period', 'units': 'hours'},
            data=cf.Data(np.array([0., 3., 6., 9.], dtype='f4'))),
        axes=(ax_t,), key='auxiliarycoordinate2')

    # forecast_reference_time — scalar (size-1 domain axis)
    f.set_construct(
        cf.AuxiliaryCoordinate(
            properties={'standard_name': 'forecast_reference_time',
                        'units': 'hours since 1970-01-01 00:00:00',
                        'calendar': 'gregorian'},
            data=cf.Data(np.array([0.], dtype='f4'))),
        axes=(ax_sc,), key='auxiliarycoordinate3')

    # realization — scalar (same size-1 axis)
    f.set_construct(
        cf.AuxiliaryCoordinate(
            properties={'standard_name': 'realization', 'units': '1'},
            data=cf.Data(np.array([1], dtype='i4'))),
        axes=(ax_sc,), key='auxiliarycoordinate4')

    # ------------------------------------------------------------------ #
    # Cell method
    # ------------------------------------------------------------------ #
    f.set_construct(cf.CellMethod(axes='area', method='mean'))

    # ------------------------------------------------------------------ #
    # Coordinate references
    # ------------------------------------------------------------------ #

    # latitude_longitude grid mapping
    cr_ll = cf.CoordinateReference()
    cr_ll.coordinate_conversion.set_parameter('grid_mapping_name', 'latitude_longitude')
    cr_ll.datum.set_parameter('longitude_of_prime_meridian', 0.0)
    cr_ll.datum.set_parameter('earth_radius', 6371229.0)
    f.set_construct(cr_ll)

    # atmosphere_hybrid_height_coordinate — domain ancillaries a, b, orog
    da_orog = cf.DomainAncillary(
        properties={'standard_name': 'surface_altitude', 'units': 'm'},
        data=cf.Data(np.random.uniform(0, 500, (8, 10)).astype('f4'), units='m'))
    key_orog = f.set_construct(da_orog, axes=(ax_y, ax_x))

    da_a = cf.DomainAncillary(
        properties={'units': 'm',
                    'long_name': 'vertical coordinate formula term: a(k)'},
        data=cf.Data(lh_vals.copy(), units='m'))
    key_a = f.set_construct(da_a, axes=(ax_z,))

    da_b = cf.DomainAncillary(
        properties={'units': '1',
                    'long_name': 'vertical coordinate formula term: b(k)'},
        data=cf.Data(sigma_vals.copy(), units='1'))
    key_b = f.set_construct(da_b, axes=(ax_z,))

    cr_hh = cf.CoordinateReference()
    cr_hh.coordinate_conversion.set_parameter(
        'standard_name', 'atmosphere_hybrid_height_coordinate')
    cr_hh.coordinate_conversion.set_domain_ancillaries(
        {'a': key_a, 'b': key_b, 'orog': key_orog})
    f.set_construct(cr_hh)

    return f


def example_field_B():
    """Return a sea_water_potential_temperature field on a curvilinear ocean grid.

    The field has:
      - time(3) x depth(4) x nj(9) x ni(11)
      - 2-D latitude(nj, ni) and longitude(nj, ni) auxiliary coordinates
        (the characteristic feature of curvilinear / tripolar ocean grids)
      - Native integer (i, j) dimension coordinates
      - depth dimension coordinate with cell bounds
      - latitude_longitude coordinate reference

    Returns
    -------
    cf.Field
    """
    np.random.seed(7)

    g = cf.Field()
    g.set_properties({
        'standard_name': 'sea_water_potential_temperature',
        'units': 'degC',
        'Conventions': 'CF-1.13',
        'comment': (
            'Ocean model output on a curvilinear native grid. '
            'True geographic coordinates are given by the 2-D auxiliary '
            'latitude and longitude coordinate variables.'
        ),
    })

    # Domain axes
    gax_t = g.set_construct(cf.DomainAxis(3),  key='domainaxis0')
    gax_z = g.set_construct(cf.DomainAxis(4),  key='domainaxis1')
    gax_j = g.set_construct(cf.DomainAxis(9),  key='domainaxis2')
    gax_i = g.set_construct(cf.DomainAxis(11), key='domainaxis3')

    # Main data array
    g.set_data(
        cf.Data(np.random.uniform(0, 30, (3, 4, 9, 11)).astype('f4'), units='degC'),
        axes=(gax_t, gax_z, gax_j, gax_i))

    # ------------------------------------------------------------------ #
    # Dimension coordinates
    # ------------------------------------------------------------------ #

    # time (monthly mid-points for Jan–Mar 1990)
    g.set_construct(
        cf.DimensionCoordinate(
            properties={'standard_name': 'time', 'axis': 'T',
                        'units': 'days since 1990-01-01', 'calendar': 'gregorian'},
            data=cf.Data(np.array([15., 46., 74.], dtype='f4'))),
        axes=(gax_t,), key='dimensioncoordinate0')

    # depth with bounds
    depth_vals = np.array([5., 25., 75., 200.], dtype='f4')
    depth_bnds = np.array([[0., 10.], [10., 50.], [50., 100.], [100., 500.]], dtype='f4')
    g.set_construct(
        cf.DimensionCoordinate(
            properties={'standard_name': 'depth', 'units': 'm',
                        'axis': 'Z', 'positive': 'down'},
            data=cf.Data(depth_vals, units='m'),
            bounds=cf.Bounds(data=cf.Data(depth_bnds, units='m'))),
        axes=(gax_z,), key='dimensioncoordinate1')

    # native j (y-index) — no geographic meaning
    g.set_construct(
        cf.DimensionCoordinate(
            properties={'long_name': 'cell index along second dimension', 'units': '1'},
            data=cf.Data(np.arange(9, dtype='i4'))),
        axes=(gax_j,), key='dimensioncoordinate2')

    # native i (x-index) — no geographic meaning
    g.set_construct(
        cf.DimensionCoordinate(
            properties={'long_name': 'cell index along first dimension', 'units': '1'},
            data=cf.Data(np.arange(11, dtype='i4'))),
        axes=(gax_i,), key='dimensioncoordinate3')

    # ------------------------------------------------------------------ #
    # 2-D auxiliary coordinates — the key feature of this example
    # ------------------------------------------------------------------ #
    j_idx, i_idx = np.meshgrid(np.arange(9), np.arange(11), indexing='ij')

    # Simulated curvilinear grid: a slightly tilted subregion of the
    # North Atlantic, similar to what a tripolar model would produce.
    lat2d = (40.0 + j_idx * 2.5 + i_idx * 0.2).astype('f4')   # 40–62 °N
    lon2d = (-30.0 + i_idx * 3.0 - j_idx * 0.3).astype('f4')  # -30–0 °E approx

    g.set_construct(
        cf.AuxiliaryCoordinate(
            properties={'standard_name': 'latitude', 'units': 'degrees_north'},
            data=cf.Data(lat2d, units='degrees_north')),
        axes=(gax_j, gax_i), key='auxiliarycoordinate0')

    g.set_construct(
        cf.AuxiliaryCoordinate(
            properties={'standard_name': 'longitude', 'units': 'degrees_east'},
            data=cf.Data(lon2d, units='degrees_east')),
        axes=(gax_j, gax_i), key='auxiliarycoordinate1')

    # ------------------------------------------------------------------ #
    # Cell methods
    # ------------------------------------------------------------------ #
    g.set_construct(cf.CellMethod(axes='time', method='mean'))
    g.set_construct(cf.CellMethod(axes='depth', method='point'))

    # ------------------------------------------------------------------ #
    # Coordinate reference
    # ------------------------------------------------------------------ #
    cr_gll = cf.CoordinateReference()
    cr_gll.coordinate_conversion.set_parameter('grid_mapping_name', 'latitude_longitude')
    cr_gll.datum.set_parameter('earth_radius', 6371229.0)
    g.set_construct(cr_gll)

    return g


if __name__ == '__main__':
    f = example_field_A()
    g = example_field_B()

    print("=" * 60)
    print("Field A  —  N2560-style hybrid-height temperature")
    print("=" * 60)
    print(f)
    print()
    print("=" * 60)
    print("Field B  —  Curvilinear ocean grid with 2-D lat/lon")
    print("=" * 60)
    print(g)