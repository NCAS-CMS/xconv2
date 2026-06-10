#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"

plantuml_bin="${PLANTUML_BIN:-}"
if [[ -z "$plantuml_bin" ]]; then
  if command -v plantuml >/dev/null 2>&1; then
    plantuml_bin="$(command -v plantuml)"
  elif [[ -x "/opt/homebrew/bin/plantuml" ]]; then
    plantuml_bin="/opt/homebrew/bin/plantuml"
  else
    echo "plantuml executable not found. Install PlantUML or set PLANTUML_BIN." >&2
    exit 1
  fi
fi

mkdir -p docs/uml/svg

"$plantuml_bin" -tsvg -o svg \
  docs/uml/alpha_core_window.pu \
  docs/uml/main_window_component_map.pu \
  docs/uml/core_window_gui_worker_signals.puml \
  docs/uml/core_window_options_sequence.puml \
  docs/uml/remote_access_module.puml \
  docs/uml/remote_worker_warmup_sequence.puml

echo "Generated SVG diagrams in docs/uml/svg"
