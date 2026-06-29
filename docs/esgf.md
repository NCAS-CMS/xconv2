# Building an ESGF file navigator

The objective here is to have an ESGF file navigator which effectively behaves like
our existing https file systems for getting files, but uses the pystac_client to navigate the system. (Some ESGF nodes do not allow browsing, even though they are http servers).

Things I anticipate needing to do are: handle multiple possible endpoints for a given entry and possibly multiple possible data types.  However, we only plan to support things here which can be retrieved via http/https (i.e. things where the endpoint url is either standard https or s3).

The first step should be a file browser using the pystac client.  

We can use [https://api.stac.ceda.ac.uk](https://api.stac.ceda.ac.uk) as both our test and production target for this functionality.

Step 1: Build a file browser function: to allow browsing of the CMIP6 collection available at this endpoint as if it were a file system.  To do that, we need to have a default facet order. For this, I suggest we run with experiment_id, sub_experiment_id, source_id, institution_id, table_id, variable_id, variant_id, realm, grid_label, nominal_reoslutin, cf standard name, frequency. Hopefully at that point we will have a standard atomic dataset of files.  We will eventually use this as the `ls` capability, but for this step, let's just build the function and see what it produces.

Current prototype (Step 1) is available in `xconv2.esgf_browser.cmip6_ls`.

Quick IPython trial:

```python
from xconv2.esgf_browser import cmip6_ls

# Root level (activity_id values)
cmip6_ls("")

# Drill down by selecting one returned value (without trailing slash)
cmip6_ls("CMIP")

cmip6_ls("CMIP/historical")
```

Each intermediate level returns virtual directories ending in `/`.

**Note:** The live API query may take a moment on the first call. If you get an empty list,
check that `pystac-client` is installed: `pip install pystac-client`
