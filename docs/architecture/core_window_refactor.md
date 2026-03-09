# Core Window Refactor Summary

Date: 2026-03-08
Branch: `refactor/core-window-phase1-phase2`

## Overview

`xconv2/core_window.py` was refactored in three phases to reduce class size and separate concerns by feature. The core window now acts as a coordinator that delegates behavior to focused UI controllers.

## Final Structure

- `xconv2/core_window.py`
- Coordinator for window lifecycle, top-level wiring, and extension hooks used by `CFVMain`.

- `xconv2/ui/settings_store.py`
- Settings load/save, validation, legacy migration, recent-file persistence, and default save-path helpers.

- `xconv2/ui/menu_controller.py`
- Menu bar construction and recent-menu refresh logic.

- `xconv2/ui/selection_controller.py`
- Slider and collapse UI behavior, selected-range labels, and plot-summary enable/disable logic.

- `xconv2/ui/field_metadata_controller.py`
- Field list population, field detail display, property parsing, property table dialog, and CSV export.

- `xconv2/ui/plot_view_controller.py`
- Plot panel widgets, render/update of plot image, resize/aspect fitting, and save code/save plot button flows.

- `xconv2/ui/contour_options_controller.py`
- Contour options dialog, annotation chooser, color-scale chooser, and scale-preview rendering.

## UML Artifacts

- `docs/uml/core_window.puml`
- `docs/uml/core_window_gui_worker_signals.puml`
- `docs/uml/core_window_options_sequence.puml`

These diagrams provide both class-level structure and key sequence flows for interaction paths.

## Implementation Notes

- Public method names on `CFVCore` were retained where practical, with internal delegation added to controllers.
- Worker orchestration remains in `xconv2/main_window.py`.
- Dialog and feature behavior was preserved while moving logic into separate files.

## Refactor Commit History

- `2fb1273` - refactor: extract menu and settings from core window
- `9ba1ddd` - refactor: extract selection and field metadata controllers
- `1925cea` - refactor: extract plot view and contour options controllers

## Next Steps

- Add targeted unit tests for each controller class to reduce reliance on broad integration testing.
- Introduce narrow host protocols (typing `Protocol`) for controller dependencies to reduce coupling.
- Optionally split `ContourOptionsController` into smaller dialog-builder components if it continues to grow.
