"""User-facing tooltip text for info dialog buttons.

Each constant is an (title, content) tuple where content may contain HTML/RichText.
Edit this file to update the help text shown throughout the application without
touching any Qt layout code.
"""

from __future__ import annotations

SELECTION_HELP: tuple[str, str] = (
    "Selection Help",
    """<b>About the Selection Controls</b><br>
<br>
Lorem ipsum dolor sit amet, consectetur adipiscing elit.
Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.
<br><br>
Use the range sliders to select subsets of your data along each dimension.
Check the <i>collapse</i> checkbox to reduce a dimension to a single value using a collapse method.
""",
)

COLLAPSE_METHODS: tuple[str, str] = (
    "Collapse Methods",
    """<b>About Collapse Methods</b><br>
<br>
Lorem ipsum dolor sit amet, consectetur adipiscing elit.
Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.
Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris.
<br><br>
Collapse methods reduce a dimension to a single value.
For documentation, visit
<a href="https://ncas-cms.github.io/cf-python/analysis.html#collapse-methods">the cf-python documentation</a>.
""",
)

PLOTTING_AND_EXPORTING: tuple[str, str] = (
    "Plotting and Exporting",
    """<b>About Plotting and Exporting</b><br>
<br>
Lorem ipsum dolor sit amet, consectetur adipiscing elit.
Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.
<br><br>
Your data can be plotted once it has been reduced to 1D or 2D.
Use the collapse methods in the Selection panel to reduce higher-dimensional data.
""",
)

REMOTE_CONFIGURATION: tuple[str, str] = (
    "Remote Configuration",
    """<p><b>Remote Configuration</b></p>
<p><i>xconv2</i> supports reading remote data held in object stores (S3), or
available via webservers which can support HTTP range requests (HTTPS),
e.g. ESGF nodes or JASMIN GWS public webservers, or accessible to 
you via SSH.</p>
<p><b>S3</b>: You will likely need to provide authentication credentials.
<i>xconv2</i> expects to find those in <a href="https://github.com/minio/mc/blob/224e602e59da2dd2e0fdc425399b3c0f13d21656/docs/minio-client-configuration-files.md">minio format</a> 
json files in either your minio config directory (<tt>~/.mc/</tt>) 
or the <i>xconv2</i> config directory (<tt>~/.xconv2</tt>).  
You can create a new configuration and it will be saved to the <i>xconv2</i> config directory.</p>
<p><b>HTTPS</b>: You can connect to any webserver that supports HTTP range requests.
This includes ESGF nodes and JASMIN GWS public webservers, all you need to do 
for these is to enter the base URL and a short name for the configuration.</p>
<p><b>SSH</b>: You can also connect to and navigate remote filesytems via SSH
and access <tt>HDF5/NetCDF4</tt> and <tt>pp/fields</tt> files provided you have a python with 
<i>pyfive</i>, <i>ppfive</i>, and <i>cbor2</i> installed on the server. You will need to identify
the remote python executable and provide authentication credentials (e.g. via <tt>.ssh/config</tt>).
</p>
""",
)

CACHE_MANAGEMENT: tuple[str, str] = (
    "Cache Management",
    """<b>About Cache Management</b><br>
<br>
Lorem ipsum dolor sit amet, consectetur adipiscing elit.
Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.
<br><br>
The cache stores remote data locally to speed up repeated access.
Use Refresh to update usage statistics, Prune to remove expired entries,
and Flush to clear all cached data for a remote.
""",
)
