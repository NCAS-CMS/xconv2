# macOS App Build Notes

This document captures what we have learned while repeatedly building and testing the macOS app bundle for xconv2.

## TL;DR

Use the `work26t` conda environment, build with the project spec, and test startup behavior from `dist/xconv2.app`.

## Known-good environment

- OS: macOS (Apple Silicon)
- Python env: `work26t`
- Project root: `xconv2`
- Build spec: `xconv2.spec`

## Build commands (the magic incantations)

Run from repository root:

```bash
conda run -n work26t pyinstaller --clean --noconfirm xconv2.spec
```

Quickly check the end of build output:

```bash
conda run -n work26t pyinstaller --clean --noconfirm xconv2.spec 2>&1 | tail -20
```

Expected successful ending includes:

- `Building BUNDLE ... completed successfully`
- `Build complete! The results are available in: .../dist`

## Build artifacts

After a successful build, expect:

- `dist/xconv2.app`
- `dist/xconv2` (onedir bundle)

## Build a DMG (the other magic incantation)

From repository root, after `dist/xconv2.app` exists:

```bash
rm -f dist/xconv2.dmg && hdiutil create -volname "xconv2" -srcfolder dist/xconv2.app -ov -format UDZO dist/xconv2.dmg
```

Quick verify:

```bash
hdiutil imageinfo dist/xconv2.dmg | rg -n "Format|Class|Software Version"
```

(I think I used `create_dmg` before, so we need to test this properly.)


## Startup performance lessons

We saw severe startup lag when heavy scientific/plotting imports happened eagerly at process start.

### What helped

1. GUI import-path slimming:
- Avoid importing wide `xconv2.cf_interface` trees at GUI startup.
- Import only narrow modules needed by dialogs.

2. Worker lazy runtime loading:
- Keep worker control/session plumbing light at import time.
- Load heavy modules (`cf`, `cfplot`, `matplotlib`, `numpy`, cf-interface helpers) on first task that needs them.

3. Avoid test-driven design constraints that force eager imports:
- Prefer real lightweight CF I/O fixtures (`cf.example_field` + temp NetCDF) instead of monkeypatching worker internals.

## Remote access notes

HTTP directory listing can require a trailing slash on some servers (for example OldDAP2-style endpoints).

Implemented behavior:

- If HTTP/HTTPS `ls(path)` fails and path does not end with `/`, retry with `path + "/"`.

## Typical warnings during PyInstaller build

These appeared during successful builds and were non-blocking on macOS:

- `Library user32 required via ctypes not found`
- `Library msvcrt required via ctypes not found`

These are Windows-related and expected to be absent on macOS.

## Useful validation commands

### Verify worker-focused tests

```bash
conda run -n work26t python -m pytest tests/test_worker_remote_tasks.py tests/test_worker_save_script.py tests/test_worker_remote_integration.py -q
```

### Spot old worker monkeypatch patterns (should return no matches)

```bash
rg -n "monkeypatch\.setattr\(worker\.cf|monkeypatch\.setattr\(worker\.cf_interface" tests
```

### Quick GUI-related sanity test

```bash
conda run -n work26t python -m pytest tests/test_remote_configuration_dialog.py -q
```

## If startup is still very slow

1. Profile import cost in env:

```bash
conda run -n work26t python -X importtime -c "import xconv2.gui" 2> /tmp/xconv2_importtime.log
conda run -n work26t python -X importtime -c "import xconv2.worker" 2> /tmp/xconv2_worker_importtime.log
```

2. Check for accidental eager imports in startup paths:
- `xconv2/gui.py`
- `xconv2/core_window.py`
- `xconv2/main_window.py`
- `xconv2/worker.py`

3. Keep heavyweight libs behind lazy initializer boundaries in worker.

## Release reminders

Before sharing a build:

1. Rebuild from a clean state with the command above.
2. Launch `dist/xconv2.app` and verify first window appears promptly.
3. Open a local file and one remote file (if configured).
4. Confirm no regressions in worker/remote tests.
