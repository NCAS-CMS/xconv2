"""PyInstaller build spec for xconv2.

Builds two executables:
- xconv2 (GUI)
- cf-worker (backend worker process launched by the GUI)

The GUI expects cf-worker to be located beside the main executable, so both are
included in the same macOS app bundle.
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


block_cipher = None
project_root = Path(SPECPATH)


# -- Data files ---------------------------------------------------------------

# xconv2 package assets (icons, SVG, UI files) — needed by both GUI and worker.
xconv2_datas = collect_data_files("xconv2", include_py_files=False)

# cfplot colourmap files (.rgb) — only the worker renders plots.
cfplot_datas = collect_data_files("cfplot", include_py_files=False)

# GUI bundle: xconv2 assets only.
gui_datas = xconv2_datas

# Worker bundle: xconv2 assets + cfplot colourmaps + UDUNITS XML.
worker_datas = xconv2_datas + cfplot_datas

# UDUNITS database XML — cfunits requires it at runtime in frozen mode.
udunits_xml_candidates = [
    Path(sys.prefix) / "share" / "udunits" / "udunits2.xml",
    Path(os.environ.get("CONDA_PREFIX", "")) / "share" / "udunits" / "udunits2.xml",
]
for xml_path in udunits_xml_candidates:
    if xml_path and xml_path.exists():
        worker_datas.append((str(xml_path), "udunits"))
        break

# UDUNITS native library — only the worker calls cfunits.
udunits_binaries = []
udunits_lib_candidates = [
    Path(sys.prefix) / "lib" / "libudunits2.dylib",
    Path(sys.prefix) / "lib" / "libudunits2.0.dylib",
    Path(os.environ.get("CONDA_PREFIX", "")) / "lib" / "libudunits2.dylib",
    Path(os.environ.get("CONDA_PREFIX", "")) / "lib" / "libudunits2.0.dylib",
]
for lib_path in udunits_lib_candidates:
    if lib_path and lib_path.exists():
        udunits_binaries.append((str(lib_path), "."))
        break


# -- Hidden imports -----------------------------------------------------------

# GUI process uses no dynamic imports from the scientific stack; static analysis
# from pyinstaller_gui_entry.py is sufficient.  Keep this list minimal.
gui_hiddenimports: list[str] = []

# Worker uses dynamic imports inside cf/cfplot; collect all submodules so
# frozen code can resolve them at runtime.
worker_hiddenimports: list[str] = []
for _pkg in ("xconv2", "cf", "cfdm", "cfplot"):
    worker_hiddenimports.extend(collect_submodules(_pkg))


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
    "matplotlib.tests", "matplotlib.testing",
    "numpy.tests", "numpy.testing",
    "scipy.tests",
    "cf.test",
    "cfplot.test",
]


# -- Worker executable --------------------------------------------------------
# Built first as a self-contained one-file binary so it can be embedded inside
# the GUI's one-dir COLLECT below.  The worker performs all heavy imports
# (cf, cfplot, scipy, …) in its own process; the GUI never loads them.

worker_analysis = Analysis(
    [str(project_root / "pyinstaller_worker_entry.py")],
    pathex=[str(project_root)],
    binaries=udunits_binaries,
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
    worker_analysis.binaries,
    worker_analysis.zipfiles,
    worker_analysis.datas,
    [],
    name="cf-worker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX corrupts macOS ARM64 bytecode archives
    upx_exclude=[],
    runtime_tmpdir=None,
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
# them directly on launch.  The self-contained worker binary is embedded in
# the same MacOS/ directory so _get_worker_path() can locate it at runtime.

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
)

# COLLECT merges the GUI launcher, its pre-extracted dependencies, and the
# one-file worker binary into a single directory.  BUNDLE wraps this directory
# into the final .app structure.
#
# Resulting layout inside xconv2.app/Contents/MacOS/:
#   xconv2        — GUI launcher (fast, no extraction)
#   cf-worker     — worker self-extracting archive (runs in background)
#   _internal/    — pre-extracted GUI Python runtime and .so files
#
# The one-file worker EXE is referenced by its assembled output path (DISTPATH
# / "cf-worker") rather than by the EXE object itself.  Passing an EXE object
# to COLLECT causes PyInstaller to iterate over all of the EXE's embedded
# source-file TOC entries and try to copy them individually, which fails.
_worker_built = str(Path(DISTPATH) / "cf-worker")
gui_coll = COLLECT(
    gui_exe,
    gui_analysis.binaries,
    gui_analysis.zipfiles,
    gui_analysis.datas,
    [("cf-worker", _worker_built, "BINARY")],
    strip=False,
    upx=False,
    upx_exclude=[],
    name="xconv2",
)

# Keep both executables in the same .app so CFVMain can find cf-worker at:
# Path(sys.executable).parent / "cf-worker"
app = BUNDLE(
    gui_coll,
    name="xconv2.app",
    icon=None,
    bundle_identifier="org.xconv2.app",
)