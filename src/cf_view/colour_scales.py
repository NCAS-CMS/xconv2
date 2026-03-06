from __future__ import annotations

from functools import lru_cache

cscales=['viridis', 'magma', 'inferno', 'plasma', 'parula', 'gray', 'hotcold_18lev', 'hotcolr_19lev',\
         'mch_default', 'perc2_9lev', 'percent_11lev', 'precip2_15lev', 'precip2_17lev', 'precip3_16lev',\
         'precip4_11lev', 'precip4_diff_19lev', 'precip_11lev', 'precip_diff_12lev', 'precip_diff_1lev', 'rh_19lev',\
         'spread_15lev', 'amwg', 'amwg_blueyellowred', 'BlueDarkRed18', 'BlueDarkOrange18', 'BlueGreen14', 'BrownBlue12',\
         'Cat12', 'cmp_flux', 'cosam12', 'cosam', 'GHRSST_anomaly', 'GreenMagenta16',\
         'nrl_sirkes', 'nrl_sirkes_nowhite','prcp_1', 'prcp_2',\
         'prcp_3', 'radar', 'radar_1', 'seaice_1', 'seaice_2', 'so4_21',\
         'StepSeq25', 'sunshine_9lev', 'sunshine_diff_12lev', 'temp_19lev', 'temp_diff_18lev', 'temp_diff_1lev',\
         'topo_15lev', 'wgne15', 'wind_17lev', 'amwg256', 'BkBlAqGrYeOrReViWh200', 'BlAqGrYeOrRe', 'BlAqGrYeOrReVi200',\
         'BlGrYeOrReVi200', 'BlRe', 'BlueRed', 'BlueRedGray', 'BlueWhiteOrangeRed', 'BlueYellowRed', 'BlWhRe', 'cmp_b2r',\
         'cmp_haxby', 'detail', 'extrema', 'GrayWhiteGray', 'GreenYellow', 'helix', 'helix1', 'hotres', 'matlab_hot',\
         'matlab_hsv', 'matlab_jet', 'matlab_lines', 'ncl_default', 'ncview_default', 'OceanLakeLandSnow', 'rainbow',\
         'rainbow_white_gray', 'rainbow_white', 'rainbow_gray', 'tbr_240_300', 'tbr_stdev_0_30', 'tbr_var_0_500',\
         'tbrAvg1', 'tbrStd1', 'tbrVar1', 'thelix', 'ViBlGrWhYeOrRe', 'wh_bl_gr_ye_re', 'WhBlGrYeRe', 'WhBlReWh',\
         'WhiteBlue', 'WhiteBlueGreenYellowRed', 'WhiteGreen', 'WhiteYellowOrangeRed', 'WhViBlGrYeOrRe', 'WhViBlGrYeOrReWh',\
         'wxpEnIR', '3gauss', '3saw', 'posneg_2', 'posneg_1',\
         'os250kmetres', 'wiki_1_0_2', 'wiki_1_0_3', 'wiki_2_0',\
         'wiki_2_0_reduced', 'arctic', 'scale1', 'scale2', 'scale3', 'scale4', 'scale5', 'scale6', 'scale7', 'scale8',\
         'scale9', 'scale10', 'scale11', 'scale12', 'scale13', 'scale14', 'scale15', 'scale16', 'scale17' , 'scale18',\
         'scale19', 'scale20', 'scale21', 'scale22', 'scale23', 'scale24', 'scale25', 'scale26', 'scale27', 'scale28',\
         'scale29', 'scale30', 'scale31', 'scale32', 'scale33', 'scale34', 'scale35', 'scale36', 'scale37', 'scale38',\
         'scale39', 'scale40', 'scale41', 'scale42', 'scale43', 'scale44']


@lru_cache(maxsize=512)
def get_colour_scale_hexes(scale_name: str) -> tuple[str, ...]:
    """Return a tuple of colour hex values for a cf-plot colour scale."""
    try:
        import cfplot as cfp
        import cfplot.cfplot as cfp_module

        cfp.cscale(scale=scale_name)
        colors = getattr(cfp_module.plotvars, "cs", None)
        if isinstance(colors, list):
            return tuple(str(color) for color in colors)
    except Exception:
        return tuple()

    return tuple()