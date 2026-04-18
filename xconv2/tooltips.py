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
    """<b>About Remote Configuration</b><br>
<br>
Lorem ipsum dolor sit amet, consectetur adipiscing elit.
Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.
<br><br>
Configure S3, HTTPS, or SSH remote connections here.
Each protocol tab allows you to select an existing configuration or add a new one.
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
