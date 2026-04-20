"""User-facing tooltip text for info dialog buttons.

Each constant is an (title, content) tuple where content may contain HTML/RichText.
Edit this file to update the help text shown throughout the application without
touching any Qt layout code.
"""

from __future__ import annotations

SELECTION_HELP: tuple[str, str] = (
    "Selection Help",
    """<p><b>Selection Controls</b></p>
The selection controls are designed to use the dimensionality of the first field 
selected to determine how to subset data for further analysis and plotting.
Only dimensions of length greater than 1 are shown, and if the data has a
two-dimensional auxiliary lat/lon coordinate, this will be collapsed into
two proxy one-dimensional coordinates for selection purposes. </p>
<p> The collapse option allows you to reduce a dimension to a single value using a 
method from a set of collapse method which will appear in a dialog when you click the collapse 
button.</p>
<p> Note that once the mouse is in the selection area, you can use also us the 
up/down arrow keys to navigate between the dimensions and the left/right arrow keys 
for fine adjustments to the range sliders</p>
<p> The properties button allows you to look at the properties of the selected field,
while the little arrow key to the right toggles the display of the field description
above the plot area.</p>
""",
)

PLOTTING_AND_EXPORTING: tuple[str, str] = (
    "Plotting and Exporting",
    """<p><b>Plotting and Exporting</b></p>
<p><i>xconv2</i> supports plotting and exporting your data once it has been reduced to 1D or 2D.
The plotting options exposed depend on the number and dimensionality of the fields selected,
and will only be shown once the dimensionality is reduced to 1D or 2D. <p>
<p> The plotting options are designed to be flexible and powerful, but the exact options 
available depend on the data itself, and some may not work as intended. Don't be 
surprised if you encounter bugs, but please do raise issues for functionality which is 
broken or not working as expected.</p>
<p> Export options are also available once you have reduced your data to 1D or 2D, and will allow you 
to export any combinatino of the plot itself, the data used for the plot, and the code used to
generate the plot.</p>
<p> The current code export option is a work in progress; the code generated is not at all suitable for 
easy re-use at the moment. We will refactor the code generation in a future release to make it more
modular, readable, and reusable. </p>
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
    """<b>Cache Management</b><br>
<p> Each remote configuration can be configured with a disk cache to speed up repeated 
access to remote data (this is done on the cache configuration menu). 
The cache stores data as it is acccessed, and xconv2 will check
the cache before attempting to access remote data. How much cache and how long
it keep data as "valid" is configurable on the cache configuration menu.</p>
<p> Using cached remote data will be much faster than accessing it remotely.</p>
<p> This menu can be used to manage the cache: you can refresh usage statistics, 
prune expired entries, or flush all cached data for a remote configuration. Note
that as multiple remote configurations can share a cache, flushing a cache will 
affect all remotes that use it.</p>
""",
)
