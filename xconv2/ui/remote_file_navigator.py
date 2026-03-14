from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True)
class RemoteFilesystemSpec:
    """Normalized filesystem construction details for a remote picker session."""

    protocol: str
    storage_options: dict[str, Any]
    root_path: str
    display_name: str
    uri_scheme: str
    uri_authority: str


@dataclass(frozen=True)
class RemoteEntry:
    """Single normalized directory entry from an fsspec ls call."""

    path: str
    name: str
    is_dir: bool
    size: int | None


def _value_from_keys(details: dict[str, Any], *keys: str) -> str:
    """Return the first non-empty string value among candidate keys."""
    for key in keys:
        value = details.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def build_remote_filesystem_spec(config: dict[str, Any]) -> RemoteFilesystemSpec:
    """Translate remote configuration state into fsspec filesystem arguments."""
    protocol = str(config.get("protocol", "")).upper()
    remote = config.get("remote", {})
    if not isinstance(remote, dict):
        raise ValueError("Remote configuration is malformed")

    details = remote.get("details", {})
    if not isinstance(details, dict):
        details = {}

    if protocol == "S3":
        alias = str(remote.get("alias") or "S3")
        url = _value_from_keys(details, "url") or _value_from_keys(remote, "url")
        key = _value_from_keys(details, "accessKey", "access_key") or _value_from_keys(remote, "access_key")
        secret = _value_from_keys(details, "secretKey", "secret_key") or _value_from_keys(remote, "secret_key")

        storage_options: dict[str, Any] = {"anon": not (key and secret)}
        if key and secret:
            storage_options["key"] = key
            storage_options["secret"] = secret
        if url:
            storage_options["client_kwargs"] = {"endpoint_url": url}

        return RemoteFilesystemSpec(
            protocol="s3",
            storage_options=storage_options,
            root_path="",
            display_name=alias,
            uri_scheme="s3",
            uri_authority="",
        )

    if protocol == "SSH":
        alias = str(remote.get("alias") or "SSH")
        hostname = _value_from_keys(details, "hostname") or _value_from_keys(remote, "hostname")
        user = _value_from_keys(details, "user") or _value_from_keys(remote, "user")
        identity_file = _value_from_keys(details, "identityfile", "identity_file") or _value_from_keys(remote, "identity_file")
        if not hostname:
            raise ValueError("SSH configuration is missing a hostname")

        storage_options = {"host": hostname}
        if user:
            storage_options["username"] = user
        if identity_file:
            storage_options["key_filename"] = identity_file

        return RemoteFilesystemSpec(
            protocol="sftp",
            storage_options=storage_options,
            root_path="/",
            display_name=alias,
            uri_scheme="ssh",
            uri_authority=hostname,
        )

    if protocol == "HTTP":
        url = _value_from_keys(details, "url", "base_url") or _value_from_keys(remote, "url", "base_url")
        if not url:
            raise ValueError("HTTP remote navigation is not configured yet")
        return RemoteFilesystemSpec(
            protocol="http",
            storage_options={},
            root_path=url,
            display_name="HTTP",
            uri_scheme="",
            uri_authority="",
        )

    raise ValueError(f"Unsupported remote protocol: {protocol}")


def create_filesystem(spec: RemoteFilesystemSpec):
    """Create the underlying fsspec filesystem instance lazily."""
    try:
        import fsspec  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised at runtime
        raise RuntimeError("fsspec is required for remote navigation") from exc

    return fsspec.filesystem(spec.protocol, **spec.storage_options)


def normalize_remote_entries(entries: list[Any]) -> list[RemoteEntry]:
    """Normalize fsspec ls results into tree-friendly entries sorted dirs-first."""
    normalized: list[RemoteEntry] = []
    for entry in entries:
        if isinstance(entry, str):
            raw_path = entry
            is_dir = raw_path.endswith("/")
            size: int | None = None
        elif isinstance(entry, dict):
            raw_path = str(entry.get("name") or entry.get("Key") or "")
            entry_type = str(entry.get("type", "")).lower()
            is_dir = entry_type in {"directory", "dir"} or raw_path.endswith("/")
            raw_size = entry.get("size")
            size = int(raw_size) if isinstance(raw_size, int) else None
        else:
            continue

        cleaned = raw_path.rstrip("/") if raw_path not in {"", "/"} else raw_path
        if not cleaned and raw_path not in {"", "/"}:
            continue

        if cleaned in {"", "/"}:
            display_name = cleaned or "/"
        else:
            display_name = PurePosixPath(cleaned).name or cleaned

        normalized.append(RemoteEntry(path=cleaned or raw_path, name=display_name, is_dir=is_dir, size=size))

    return sorted(normalized, key=lambda item: (not item.is_dir, item.name.lower()))


def build_remote_uri(spec: RemoteFilesystemSpec, path: str) -> str:
    """Build a user-facing remote URI from a filesystem path."""
    cleaned = path.strip()
    if spec.uri_scheme == "s3":
        return f"s3://{cleaned.lstrip('/')}"
    if spec.uri_scheme == "ssh":
        remote_path = cleaned if cleaned.startswith("/") else f"/{cleaned}"
        return f"ssh://{spec.uri_authority}{remote_path}"
    return cleaned


class RemoteFileNavigatorDialog(QDialog):
    """Lazy-loaded tree browser backed by an fsspec filesystem."""

    _ROLE_DATA = Qt.UserRole
    _PLACEHOLDER = "Loading..."

    def __init__(self, parent: QWidget | None, config: dict[str, Any]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Remote File Navigator")
        self.resize(820, 560)

        self._config = config
        self._selected_uri = ""
        self._spec = build_remote_filesystem_spec(config)
        self._filesystem = create_filesystem(self._spec)

        layout = QVBoxLayout(self)

        header = QLabel(f"Browsing {self._spec.display_name}")
        layout.addWidget(header)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Type", "Size"])
        self.tree.itemExpanded.connect(self._on_item_expanded)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.tree)

        footer = QWidget()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.addWidget(QLabel("Selection:"))
        self.selection_label = QLabel("No file selected")
        footer_layout.addWidget(self.selection_label, 1)
        layout.addWidget(footer)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.open_button = buttons.addButton("Open", QDialogButtonBox.AcceptRole)
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._populate_root()

    def _populate_root(self) -> None:
        """Load the top-level listing for the configured remote filesystem."""
        try:
            entries = self._list_entries(self._spec.root_path)
        except Exception as exc:  # pragma: no cover - UI error path
            QMessageBox.critical(self, "Remote navigation failed", str(exc))
            return

        self.tree.clear()
        for entry in entries:
            self.tree.addTopLevelItem(self._create_item(entry))

    def _list_entries(self, path: str) -> list[RemoteEntry]:
        """Call filesystem ls and normalize the returned entries."""
        listing = self._filesystem.ls(path, detail=True)
        if not isinstance(listing, list):
            raise RuntimeError(f"Unexpected listing result for {path!r}")
        return normalize_remote_entries(listing)

    def _create_item(self, entry: RemoteEntry) -> QTreeWidgetItem:
        """Create a tree item for a normalized remote entry."""
        item = QTreeWidgetItem([
            entry.name,
            "Folder" if entry.is_dir else "File",
            "" if entry.size is None else str(entry.size),
        ])
        item.setData(0, self._ROLE_DATA, {
            "path": entry.path,
            "is_dir": entry.is_dir,
            "loaded": False,
        })
        if entry.is_dir:
            item.addChild(QTreeWidgetItem([self._PLACEHOLDER]))
        return item

    def _on_item_expanded(self, item: QTreeWidgetItem) -> None:
        """Lazy-load directory contents when a tree item is expanded."""
        data = item.data(0, self._ROLE_DATA) or {}
        if not isinstance(data, dict) or not data.get("is_dir") or data.get("loaded"):
            return

        try:
            entries = self._list_entries(str(data.get("path", "")))
        except Exception as exc:  # pragma: no cover - UI error path
            QMessageBox.warning(self, "Listing failed", str(exc))
            return

        item.takeChildren()
        for entry in entries:
            item.addChild(self._create_item(entry))
        data["loaded"] = True
        item.setData(0, self._ROLE_DATA, data)

    def _on_selection_changed(self) -> None:
        """Update selection state and enable Open only for files."""
        selected = self.tree.selectedItems()
        if not selected:
            self._selected_uri = ""
            self.selection_label.setText("No file selected")
            self.open_button.setEnabled(False)
            return

        item = selected[0]
        data = item.data(0, self._ROLE_DATA) or {}
        if not isinstance(data, dict):
            return

        path = str(data.get("path", ""))
        is_dir = bool(data.get("is_dir"))
        self._selected_uri = "" if is_dir else build_remote_uri(self._spec, path)
        self.selection_label.setText(path or "No file selected")
        self.open_button.setEnabled(not is_dir and bool(path))

    def _on_item_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        """Open a file immediately on double click."""
        data = item.data(0, self._ROLE_DATA) or {}
        if isinstance(data, dict) and not bool(data.get("is_dir")):
            self.accept()

    def selected_uri(self) -> str:
        """Return the currently selected remote file URI."""
        return self._selected_uri

    def accept(self) -> None:  # type: ignore[override]
        """Require a file selection before closing with success."""
        if not self._selected_uri:
            QMessageBox.warning(self, "No file selected", "Select a remote file before opening.")
            return
        super().accept()

    @classmethod
    def get_remote_selection(
        cls,
        parent: QWidget | None,
        config: dict[str, Any],
    ) -> tuple[str, bool]:
        """Show the remote navigator and return the selected file URI."""
        dialog = cls(parent, config)
        if dialog.exec() != QDialog.Accepted:
            return "", False
        return dialog.selected_uri(), True