"""PyInstaller build spec for xconv2.

Builds two executables:
- xconv2 (GUI)
- cf-worker (backend worker process launched by the GUI)

Both executables share a single _internal/ directory produced by COLLECT so
there is no per-launch extraction overhead.  On macOS the result is wrapped in
a .app bundle; on Linux the COLLECT directory is the distributable artifact.

Target platforms: macOS (arm64/x86_64) and Linux (x86_64).
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


block_cipher = None
project_root = Path(SPECPATH)

# -- SSL/curl exclusions for Linux --------------------------------------------
# On Linux, exclude libssl, libcrypto and libcurl from the bundle and let them
# come from the host system. This avoids OpenSSL version mismatches between
# the bundled libssl and the system libcurl. Not needed on macOS where the
# system ships its own curl/ssl.

def filter_system_libs(binaries):
    if sys.platform == "darwin":
        return binaries
    exclude_prefixes = ("libssl.so", "libcrypto.so", "libcurl.so")
    return [b for b in binaries if not any(
        Path(b[0]).name.startswith(prefix) for prefix in exclude_prefixes
	)]
	    
# -- Data files ---------------------------------------------------------------

# xconv2 package assets (icons, SVG, UI files) — needed by both GUI and worker.
xconv2_datas = collect_data_files("xconv2", include_py_files=False)

# Project metadata needed at runtime for version reporting in frozen builds.
project_metadata_datas = [
    (str(project_root / "pyproject.toml"), "."),
]

# cfplot colourmap files (.rgb) — only the worker renders plots.
cfplot_datas = collect_data_files("cfplot", include_py_files=False)

# GUI bundle: xconv2 assets only.
gui_datas = xconv2_datas + project_metadata_datas

# Worker bundle: xconv2 assets + cfplot colourmaps + UDUNITS XML.
worker_datas = xconv2_datas + cfplot_datas + project_metadata_datas

# Some optional remote-read dependencies are imported dynamically via cf/cfdm
# and may be dropped from PYZ by modulegraph. Ship their package sources as
# data (including .py) as a deterministic fallback.
worker_datas += collect_data_files("pyfive", include_py_files=True)
worker_datas += collect_data_files("p5rem", include_py_files=True)
# pyfive imports its own version via importlib.metadata.version("pyfive") at
# import time. Include dist-info metadata so this works in frozen builds.
worker_datas += copy_metadata("pyfive")
worker_datas += copy_metadata("p5rem")

# Force-include the s3fs and fsspec source code
#worker_datas += collect_data_files("s3fs", include_py_files=True)
#worker_datas += collect_data_files("fsspec", include_py_files=True)
worker_datas += collect_data_files("aiobotocore", include_py_files=True)
# botocore needs its JSON data files to function
worker_datas += collect_data_files("botocore", include_py_files=False)

# Force-include urllib3 source code
#worker_datas += collect_data_files("urllib3", include_py_files=True)
worker_datas += copy_metadata("urllib3")

# Add metadata for the filesystem handlers
worker_datas += copy_metadata("s3fs")
worker_datas += copy_metadata("fsspec")
worker_datas += copy_metadata("aiobotocore")

# UDUNITS database XML — cfunits requires it at runtime in frozen mode.
# udunits2.xml uses <import href="udunits2-base.xml"> etc., so we must bundle
# the entire udunits/ directory, not just the top-level file.
udunits_dir_candidates = [
    Path(sys.prefix) / "share" / "udunits",
    Path(os.environ.get("CONDA_PREFIX", "")) / "share" / "udunits",
]
for udunits_dir in udunits_dir_candidates:
    if udunits_dir and udunits_dir.is_dir():
        for xml_file in udunits_dir.glob("*.xml"):
            worker_datas.append((str(xml_file), "udunits"))
        break

# UDUNITS native library — only the worker calls cfunits.
# Library name differs by platform: .dylib on macOS, .so.0 on Linux.
udunits_binaries = []
if sys.platform == "darwin":
    _udunits_lib_names = ["libudunits2.dylib", "libudunits2.0.dylib"]
else:
    _udunits_lib_names = ["libudunits2.so.0", "libudunits2.so"]
_udunits_lib_roots = [
    Path(sys.prefix) / "lib",
    Path(os.environ.get("CONDA_PREFIX", "")) / "lib",
]
udunits_lib_candidates = [
    root / name
    for root in _udunits_lib_roots
    for name in _udunits_lib_names
]
for lib_path in udunits_lib_candidates:
    if lib_path and lib_path.exists():
        udunits_binaries.append((str(lib_path), "."))
        break
	
# BLAS/CBLAS native library
blas_binaries = []
if sys.platform == "darwin":
    _blas_lib_names = ["libcblas.dylib", "libcblas.3.dylib", "libopenblas.dylib"]
else:
    # Linux names
    _blas_lib_names = ["libcblas.so.3", "libcblas.so", "libopenblas.so.0", "libopenblas.so.3"]

_lib_roots = [
    Path(sys.prefix) / "lib",
    Path(os.environ.get("CONDA_PREFIX", "")) / "lib",
]

blas_lib_candidates = [
    root / name
    for root in _lib_roots
    for name in _blas_lib_names
]

for lib_path in blas_lib_candidates:
    if lib_path.exists():
        blas_binaries.append((str(lib_path), "."))
        # Important: libcblas often depends on libblas or libopenblas. 
        # If we find OpenBLAS, it usually contains both, so we can stop.
        break

# NEW: Aggressive SSL capture for Conda/Linux
ssl_binaries = []
if sys.platform == "linux":
    # Try to find where your current python's libs are
    lib_path = Path(sys.prefix) / "lib"
    # We need the actual files, not just symlinks
    for lib_pattern in ["libssl.so*", "libcrypto.so*"]:
        for f in lib_path.glob(lib_pattern):
            if f.is_file() and not f.is_symlink():
                ssl_binaries.append((str(f), "."))
    
    # Also find the Python SSL extension itself just in case
    ext_path = Path(sys.prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "lib-dynload"
    for f in ext_path.glob("_ssl*"):
        ssl_binaries.append((str(f), "lib-dynload"))

# -- Hidden imports -----------------------------------------------------------

# GUI process uses no dynamic imports from the scientific stack; static analysis
# from pyinstaller_gui_entry.py is sufficient.  Keep this list minimal.
gui_hiddenimports: list[str] = []

# Worker uses dynamic imports inside cf/cfplot; collect all submodules so
# frozen code can resolve them at runtime.
worker_hiddenimports: list[str] = []
for _pkg in ("xconv2", "cf", "cfdm", "cfplot", "pyfive", "cbor2", "p5rem", "paramiko", "s3fs", "fsspec", "urllib3", "aiohttp"):
    worker_hiddenimports.extend(collect_submodules(_pkg))

# Sometimes collect_submodules misses specific sub-dependencies if
# they aren't explicitly imported in the package's __init__.py.
worker_hiddenimports.extend(
    [
	's3fs.core',
	'fsspec.implementations.local',
	'fsspec.implementations.s3fs',
	'aiobotocore'
	'botocore',
	'importlib_metadata', # fsspec uses this for discovery
    ]
)

# Add urllib3 and the standard ssl module to your worker_hiddenimports
# list:
worker_hiddenimports.extend([
    'urllib3',
    'urllib3.util',
    'urllib3.util.ssl_',
    'ssl',  # Ensure the standard library ssl is tracked
    'engineio', # Often used in these stacks
])
# -- Excludes -----------------------------------------------------------------

# Modules excluded from the GUI bundle.  The GUI only needs PySide6 + the
# xconv2 GUI modules + remote-access helpers.  Everything on the scientific
# data-processing path lives exclusively in the worker.
GUI_EXCLUDES = [
    # Scientific stack — worker-only
    "cf", "cfdm", "cfplot", "cfunits",
    "scipy", "scipy.interpolate", "scipy.stats", "scipy.signal", "scipy.special",
    "cartopy",
    "dask", "dask.distributed",
    "netCDF4", "xarray", "h5py", "h5netcdf",
    # Worker-specific xconv2 submodules (importing them drags in cf/scipy)
    "xconv2.worker",
    "xconv2.xconv_cf_interface",
    "xconv2.lineplot",
    "xconv2.cell_method_handler",
    "xconv2.plot_layout_helpers",
    # Heavyweight tooling never needed in the GUI process
    "IPython", "ipykernel", "ipython_genutils",
    "jupyter", "jupyter_client", "jupyter_core",
    "notebook", "nbformat", "nbconvert",
    "tornado", "bokeh",
    "tkinter", "_tkinter",
    "matplotlib",
]

# Modules excluded from the worker bundle.
WORKER_EXCLUDES = [
    # GUI toolkit — not used in the headless worker
    "PySide6", "shiboken6",
    "tkinter", "_tkinter",
    # Distributed scheduler — only local/threaded Dask is used
    "dask.distributed",
    # Large unused tooling
    "IPython", "ipykernel", "ipython_genutils",
    "jupyter", "jupyter_client", "jupyter_core",
    "notebook", "nbformat", "nbconvert",
    "tornado", "bokeh",
    # Test suites (large, never executed at runtime)
    # NOTE: numpy.testing is NOT excluded — scipy.interpolate imports it transitively
    # via scipy/_lib/array_api_compat at module level.
    "matplotlib.tests", "matplotlib.testing",
    "numpy.tests",
    "scipy.tests",
    "cf.test",
    "cfplot.test",
]


# -- Worker executable (one-dir mode) ----------------------------------------
# The worker shares the same COLLECT/_internal directory as the GUI, so there
# is no extraction step and no duplication of shared libraries.  Both
# executables get exclude_binaries=True; all binaries and datas from both
# analyses are merged into a single COLLECT below.

worker_analysis = Analysis(
    [str(project_root / "pyinstaller_worker_entry.py")],
    pathex=[str(project_root)],
    binaries=udunits_binaries + blas_binaries + ssl_binaries,
    datas=worker_datas,
    hiddenimports=worker_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=WORKER_EXCLUDES,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
# optimize=2 strips docstrings from bundled .pyc files (~10-15 % size saving).
worker_pyz = PYZ(worker_analysis.pure, worker_analysis.zipped_data, cipher=block_cipher, optimize=2)
worker_exe = EXE(
    worker_pyz,
    worker_analysis.scripts,
    [],
    [],
    [],
    [],
    name="cf-worker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX corrupts macOS ARM64 bytecode archives
    upx_exclude=[],
    runtime_tmpdir=None,
    exclude_binaries=True,  # one-dir mode: binaries/datas go to COLLECT, not EXE
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)


# -- GUI executable (one-dir mode) --------------------------------------------
# One-dir avoids the startup extraction penalty of one-file bundles.  All
# shared .so/.dylib files live pre-extracted in _internal/ so the OS can load
# them directly on launch.

gui_analysis = Analysis(
    [str(project_root / "pyinstaller_gui_entry.py")],
    pathex=[str(project_root)],
    binaries=[],  # GUI has no extra native libraries
    datas=gui_datas,
    hiddenimports=gui_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=GUI_EXCLUDES,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
gui_pyz = PYZ(gui_analysis.pure, gui_analysis.zipped_data, cipher=block_cipher, optimize=2)

# Icon: .icns for macOS, .png for Linux (used for .desktop integration).
if sys.platform == "darwin":
    _gui_icon = str(project_root / "xconv2" / "assets" / "cf-logo.icns")
else:
    _gui_icon = str(project_root / "xconv2" / "assets" / "cf-logo.svg")

# In one-dir mode the EXE is the lightweight launcher stub only; binaries,
# zipfiles, and datas are passed to COLLECT instead.
gui_exe = EXE(
    gui_pyz,
    gui_analysis.scripts,
    [],
    [],
    [],
    [],
    name="xconv2",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    exclude_binaries=True,  # one-dir mode: binaries/datas go to COLLECT, not EXE
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_gui_icon,
)

# COLLECT merges both executables and their combined dependencies into a single
# directory.  On macOS this is wrapped by BUNDLE into a .app; on Linux the
# COLLECT directory is the distributable artifact.
#
# Layout (macOS: inside xconv2.app/Contents/Frameworks/, Linux: dist/xconv2/):
#   xconv2        — GUI launcher
#   cf-worker     — worker launcher (no extraction on launch)
#   _internal/    — shared .so/.dylib files from both analyses
#
# Both EXEs share one _internal/ — no duplication, no temp-dir extraction.
## gui_coll = COLLECT(
##     gui_exe,
##     worker_exe,
##     filter_system_libs(gui_analysis.binaries + worker_analysis.binaries),
##     gui_analysis.zipfiles + worker_analysis.zipfiles,
##     gui_analysis.datas + worker_analysis.datas,
##     strip=False,
##     upx=False,
##     upx_exclude=[],
##     name="xconv2",
## ) 

#gui_coll = COLLECT(
#    gui_exe,
#    worker_exe,
#    gui_analysis.binaries + worker_analysis.binaries,
#    gui_analysis.zipfiles + worker_analysis.zipfiles,
#    gui_analysis.datas + worker_analysis.datas,
#    strip=False,
#    upx=False,
#    upx_exclude=[],
#    name="xconv2",
#)

# Keep both executables in the same .app so CFVMain can find cf-worker at:
# Path(sys.executable).parent / "cf-worker"
if sys.platform == "darwin":
    app = BUNDLE(
        gui_coll,
        name="xconv2.app",
        icon=_gui_icon,
        bundle_identifier="org.xconv2.app",
        info_plist={
            # Show in Dock and App Switcher like a normal foreground app.
            # PyInstaller defaults this to True for windowed apps, which
            # produces a background-agent app with no Dock icon.
            "LSUIElement": False,
            # Human-readable name shown in Dock / About This Mac.
            "CFBundleName": "xconv2",
            "CFBundleDisplayName": "xconv2",
            "CFBundleShortVersionString": "1.0",
        },
    )
