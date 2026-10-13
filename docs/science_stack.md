**Help > Science Stack...** opens a non-modal, selectable report from
`cf.environment(display=False)` in the local science worker. It includes
library versions and paths, with concise installation-source labels alongside
the Python libraries in that list. Unrelated installed packages are omitted.
Import names are mapped to distribution names, and metadata is matched to the
reported import path. Multiple metadata records are explicitly flagged; the
reported runtime version is used to disambiguate records claiming the same path
where possible. A warning is not proof that multiple versions are being imported.

Conda packages are identified from their `INSTALLER` metadata. Any local
build-directory origin retained by a Conda package is not treated as a local
developer install. Pip packages without a direct origin are labelled **pip index
install**: pip does not retain which index was used, so PyPI cannot be
distinguished from another index. Missing or ambiguous metadata is labelled
**unknown source**, not assumed to be PyPI.

For pip Git installs, the report flags the VCS origin and shows the requested
branch/tag/commit and resolved commit, even if the package's declared version
looks like a release. Local-source, editable, and direct archive/URL installs
are also flagged. Source URLs are omitted to avoid exposing credentials.
The user's home-directory prefix in paths is replaced with `~` so copied
reports do not expose it. Paths outside the home directory remain unchanged.
This uses `INSTALLER` and pip's `direct_url.json` metadata, not version-number heuristics.
Missing metadata is **not** proof of a release install; separately built wheels
may lose origin information, and editable sources can change after installation.
The report describes the local worker, not a remote server's Python environment.