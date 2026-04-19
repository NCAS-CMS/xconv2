from __future__ import annotations

import json
from pathlib import Path
import shlex
from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from xconv2.aaa.aaa_config import get_locations
from xconv2.tooltips import COLLAPSE_METHODS, REMOTE_CONFIGURATION

try:
    from p5rem import discover_remote_conda_envs
except ImportError:
    discover_remote_conda_envs = None


class InfoMessageDialog(QDialog):
    """Dialog for displaying information with a title and rich-text content."""

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        content: str,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(title)

        layout = QVBoxLayout(self)

        content_label = QLabel(content)
        content_label.setTextFormat(Qt.RichText)
        content_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        content_label.setOpenExternalLinks(True)
        content_label.setWordWrap(True)
        layout.addWidget(content_label)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)

        # Size to fit content: fix width first so word-wrap height is correct
        content_label.setFixedWidth(380)
        content_label.adjustSize()
        line_height = content_label.fontMetrics().lineSpacing()
        needed_height = content_label.sizeHint().height() + close_button.sizeHint().height() + layout.contentsMargins().top() + layout.contentsMargins().bottom() + layout.spacing() * 2 + line_height
        self.resize(400, min(max(needed_height, 150), 700))

    @classmethod
    def show_info(
        cls,
        parent: QWidget | None,
        title: str,
        content: str,
    ) -> None:
        """Show the info dialog."""
        dialog = cls(parent, title, content)
        dialog.show()


def create_info_button(
    parent: QWidget | None,
    title: str,
    content: str,
    icon_size: int = 16,
) -> QPushButton:
    """Create a small icon button that opens an info dialog when clicked.
    
    Args:
        parent: Parent widget
        title: Dialog title
        content: Dialog content (can include HTML/RichText)
        icon_size: Size of the icon in pixels
        
    Returns:
        A QPushButton configured as an info button
    """
    button = QPushButton()
    button.setMaximumWidth(icon_size + 8)
    button.setMaximumHeight(icon_size + 8)
    button.setToolTip("Click for more information")
    
    # Load the tooltip icon
    icon_path = Path(__file__).parent.parent / "assets" / "tooltip.svg"
    if icon_path.exists():
        pixmap = QPixmap(str(icon_path))
        if not pixmap.isNull():
            pixmap = pixmap.scaledToWidth(icon_size, Qt.SmoothTransformation)
            button.setIcon(QIcon(pixmap))
            button.setIconSize(pixmap.size())
    
    # Connect to show the info dialog
    button.clicked.connect(
        lambda: InfoMessageDialog.show_info(parent, title, content)
    )
    
    return button


class InputDialogCustom(QDialog):
    """Reusable item chooser with optional rich-text documentation below input."""

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        label: str,
        items: list[str],
        current_index: int,
        editable: bool,
        flags: Qt.WindowType,
        input_method_hints: Qt.InputMethodHint,
        doc_text: str,
        info_button_title: str = "",
        info_button_content: str = "",
    ) -> None:
        super().__init__(parent, flags)
        self.setWindowTitle(title)

        layout = QVBoxLayout(self)

        # Add label with optional info button
        label_row = QHBoxLayout()
        label_row.setContentsMargins(0, 0, 0, 0)
        label_row.setSpacing(4)
        prompt = QLabel(label)
        label_row.addWidget(prompt)
        
        if info_button_title and info_button_content:
            info_button = create_info_button(
                parent,
                info_button_title,
                info_button_content,
                icon_size=14
            )
            label_row.addStretch(1)
            label_row.addWidget(info_button)
        
        label_widget = QWidget()
        label_widget.setLayout(label_row)
        layout.addWidget(label_widget)

        self.item_combo = QComboBox()
        self.item_combo.addItems(items)
        self.item_combo.setEditable(editable)
        self.item_combo.setInputMethodHints(input_method_hints)
        if items:
            self.item_combo.setCurrentIndex(max(0, min(current_index, len(items) - 1)))
        layout.addWidget(self.item_combo)

        if doc_text:
            doc_label = QLabel(doc_text)
            doc_label.setTextFormat(Qt.RichText)
            doc_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
            doc_label.setOpenExternalLinks(True)
            doc_label.setWordWrap(True)
            layout.addWidget(doc_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @classmethod
    def getItem(
        cls,
        parent: QWidget | None,
        title: str,
        label: str,
        items: list[str],
        current: int = 0,
        editable: bool = True,
        flags: Qt.WindowType = Qt.WindowType.Widget,
        inputMethodHints: Qt.InputMethodHint = Qt.InputMethodHint.ImhNone,
        doc_text: str = "",
        info_button_title: str = "",
        info_button_content: str = "",
    ) -> tuple[str, bool]:
        """Mirror QInputDialog.getItem with extra ``doc_text`` rich-text content and optional info button."""
        dialog = cls(
            parent,
            title,
            label,
            items,
            current,
            editable,
            flags,
            inputMethodHints,
            doc_text,
            info_button_title,
            info_button_content,
        )
        if dialog.exec() != QDialog.Accepted:
            return "", False
        return dialog.item_combo.currentText(), True


class OpenGlobDialog(QDialog):
    """Dialog for selecting a base directory and glob expression."""

    def __init__(self, parent: QWidget | None, initial_directory: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Open Glob")

        layout = QVBoxLayout(self)

        directory_label = QLabel("Base folder:")
        layout.addWidget(directory_label)

        directory_row = QHBoxLayout()
        self.directory_edit = QLineEdit(initial_directory)
        browse_button = QPushButton("Browse...")
        browse_button.clicked.connect(self._choose_directory)
        directory_row.addWidget(self.directory_edit, 1)
        directory_row.addWidget(browse_button)
        layout.addLayout(directory_row)

        pattern_label = QLabel("Glob pattern:")
        layout.addWidget(pattern_label)

        self.pattern_edit = QLineEdit("*.nc")
        self.pattern_edit.setPlaceholderText("Examples: *.nc, run*/atm_*.nc, **/*.nc")
        layout.addWidget(self.pattern_edit)

        hint = QLabel("Use shell-style wildcards. Recursive matching is supported with **.")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose_directory(self) -> None:
        """Prompt for a base directory used to resolve glob patterns."""
        start_dir = self.directory_edit.text().strip() or str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, "Select Base Folder", start_dir)
        if selected:
            self.directory_edit.setText(selected)

    @classmethod
    def get_glob_expression(
        cls,
        parent: QWidget | None,
        initial_directory: str,
    ) -> tuple[str, bool]:
        """Return a '<base>/<pattern>' expression and acceptance state."""
        dialog = cls(parent, initial_directory)
        if dialog.exec() != QDialog.Accepted:
            return "", False

        base_dir = dialog.directory_edit.text().strip()
        pattern = dialog.pattern_edit.text().strip()
        if not base_dir or not pattern:
            return "", False

        expression = str((Path(base_dir).expanduser() / pattern))
        return expression, True


class OpenURIDialog(QDialog):
    """Dialog for collecting a URI to open directly."""

    def __init__(self, parent: QWidget | None, default_uri: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Open URI")
        self._quit_requested = False

        layout = QVBoxLayout(self)

        uri_label = QLabel("URI:")
        layout.addWidget(uri_label)

        self.uri_edit = QLineEdit(default_uri)
        self.uri_edit.setPlaceholderText("Examples: s3://bucket/path, https://host/path, ssh://user@host/path")
        layout.addWidget(self.uri_edit)

        buttons = QDialogButtonBox()
        cancel_button = buttons.addButton(QDialogButtonBox.Cancel)
        open_button = buttons.addButton("Open", QDialogButtonBox.AcceptRole)
        quit_button = buttons.addButton("Quit", QDialogButtonBox.DestructiveRole)
        cancel_button.clicked.connect(self.reject)
        open_button.clicked.connect(self.accept)
        quit_button.clicked.connect(self._accept_quit)
        layout.addWidget(buttons)

    def _accept_quit(self) -> None:
        """Mark explicit quit intent before closing with accept."""
        self._quit_requested = True
        self.accept()

    @classmethod
    def get_uri(cls, parent: QWidget | None, default_uri: str = "") -> tuple[str, bool, bool]:
        """Return entered URI, open-accepted flag, and explicit-quit flag."""
        dialog = cls(parent, default_uri=default_uri)
        if dialog.exec() != QDialog.Accepted:
            return "", False, False

        if dialog._quit_requested:
            return "", False, True

        uri = dialog.uri_edit.text().strip()
        return uri, bool(uri), False


class RemoteConfigurationDialog(QDialog):
    """Collect remote configuration details before opening a remote navigator."""

    _S3_MODES = ["Select from existing", "Add new"]
    _HTTPS_MODES = ["Select from existing", "Add new"]
    _SSH_MODES = ["Select from existing", "Add new"]
    _DISK_CACHE_MODES = ["Disabled", "Blocks", "Files"]
    _EXPIRY_OPTIONS = ["Never", "1 day", "7 days", "30 days"]
    _RESULT_SAVED_ONLY = 2
    _WIDE_FIELD_CHARS = 50
    _DISK_LOCATION_CHARS = 30

    def __init__(self, parent: QWidget | None, state: dict[str, Any] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Remote Configuration")
        self.resize(760, 620)

        self._s3_locations = self._load_s3_locations(state)
        self._ssh_runtime_preferences = self._extract_ssh_runtime_preferences(state)
        self._ssh_hosts = self._apply_ssh_runtime_preferences(
            self._load_ssh_hosts(),
            self._ssh_runtime_preferences,
        )
        self._http_locations = self._load_http_locations(state)
        self._ssh_add_new_remote_python_options: dict[str, str] = {"python3": "python3"}

        layout = QVBoxLayout(self)

        intro_row = QHBoxLayout()
        remote_info_button = create_info_button(
            self,
            *REMOTE_CONFIGURATION,
            icon_size=18
        )
        intro_row.addWidget(remote_info_button)
        intro_row.addWidget(QLabel("Select remote configuration type"))
        intro_row.addStretch(1)
        layout.addLayout(intro_row)

        self.protocol_tabs = QTabWidget()
        self.protocol_tabs.addTab(self._build_s3_tab(), "S3")
        self.protocol_tabs.addTab(self._build_http_tab(), "HTTPS")
        self.protocol_tabs.addTab(self._build_ssh_tab(), "SSH")
        layout.addWidget(self.protocol_tabs)

        layout.addWidget(self._build_cache_group())

        button_row = QWidget()
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.addStretch(1)

        cancel_button = QPushButton("Cancel")
        save_button = QPushButton("Save")
        open_button = QPushButton("Open")
        cancel_button.clicked.connect(self.reject)
        save_button.clicked.connect(self._save_and_close)
        open_button.clicked.connect(self.accept)

        button_layout.addWidget(cancel_button)
        button_layout.addWidget(save_button)
        button_layout.addWidget(open_button)
        layout.addWidget(button_row)

        self._update_s3_mode()
        self._update_s3_selected_details()
        self._update_s3_config_details()
        self._update_ssh_mode()
        self._update_ssh_selected_details()
        self._restore_state(state)

    @staticmethod
    def _load_s3_locations(state: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
        """Load S3 location definitions and merge persisted UI-only attributes."""
        options = get_locations()
        if options is None:
            locations: dict[str, dict[str, Any]] = {}
        else:
            loaded_locations, _ = options
            locations = {
                str(alias): dict(details)
                for alias, details in loaded_locations.items()
                if isinstance(alias, str) and isinstance(details, dict)
            }

        if isinstance(state, dict):
            reductionist_map = RemoteConfigurationDialog._normalize_s3_reductionist_locations(
                state.get("s3_reductionist_locations")
            )
            for alias, reductionist_url in reductionist_map.items():
                if alias in locations:
                    details = dict(locations.get(alias, {}))
                    details["reductionist_url"] = reductionist_url
                    locations[alias] = details

        return dict(sorted(locations.items()))

    @staticmethod
    def _normalize_s3_reductionist_locations(raw: object) -> dict[str, str]:
        """Normalize persisted alias->reductionist_url mapping for S3 hosts."""
        if not isinstance(raw, dict):
            return {}

        cleaned: dict[str, str] = {}
        for alias, value in raw.items():
            if not isinstance(alias, str):
                continue
            alias_text = alias.strip()
            if not alias_text:
                continue
            url_text = str(value).strip() if value is not None else ""
            if url_text:
                cleaned[alias_text] = url_text
        return dict(sorted(cleaned.items()))

    @staticmethod
    def _load_http_locations(state: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
        """Load persisted HTTPS alias mapping from dialog state."""
        if not isinstance(state, dict):
            return {}
        raw = state.get("https_locations")
        if not isinstance(raw, dict):
            raw = state.get("http_locations")
        return RemoteConfigurationDialog._normalize_https_locations(raw)

    @staticmethod
    def _normalize_https_locations(raw: object) -> dict[str, dict[str, Any]]:
        """Normalize raw HTTPS alias mapping into sorted {alias: {url}} form."""
        if not isinstance(raw, dict):
            return {}

        cleaned: dict[str, dict[str, Any]] = {}
        for alias, details in raw.items():
            if not isinstance(alias, str) or not isinstance(details, dict):
                continue
            url = details.get("url") or details.get("base_url")
            if isinstance(url, str) and url.strip():
                normalized = {"url": url.strip()}
                reductionist_url = details.get("reductionist_url")
                if isinstance(reductionist_url, str) and reductionist_url.strip():
                    normalized["reductionist_url"] = reductionist_url.strip()
                cleaned[alias] = normalized
        return dict(sorted(cleaned.items()))

    def _current_s3_reductionist_locations(self) -> dict[str, str]:
        """Collect non-empty S3 reductionist URLs keyed by alias."""
        cleaned: dict[str, str] = {}
        for alias, details in self._s3_locations.items():
            if not isinstance(alias, str) or not alias.strip() or not isinstance(details, dict):
                continue
            reductionist_url = details.get("reductionist_url")
            if isinstance(reductionist_url, str) and reductionist_url.strip():
                cleaned[alias.strip()] = reductionist_url.strip()
        return dict(sorted(cleaned.items()))

    @staticmethod
    def _default_s3_config_path() -> Path:
        """Return the preferred writable S3 config file path."""
        primary = Path.home() / ".mc/config.json"
        fallback = Path.home() / ".config/cfview/config.json"
        if primary.is_file() or primary.parent.is_dir():
            return primary
        return fallback

    @staticmethod
    def _s3_config_path_from_choice(choice: str) -> Path:
        """Map UI config-target choice to the corresponding config path."""
        normalized = choice.strip().lower()
        if normalized == "xconv":
            return Path.home() / ".config/cfview/config.json"
        return Path.home() / ".mc/config.json"

    @classmethod
    def _save_s3_location(
        cls,
        alias: str,
        url: str,
        access_key: str,
        secret_key: str,
        api: str,
        *,
        config_path: Path | None = None,
    ) -> Path:
        """Persist a MinIO-style S3 alias entry to config JSON."""
        target_path = config_path or cls._default_s3_config_path()
        payload: dict[str, Any] = {"version": "10", "aliases": {}}

        if target_path.is_file():
            payload = json.loads(target_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError(f"Malformed configuration file: {target_path}")

        aliases = payload.get("aliases")
        if not isinstance(aliases, dict):
            aliases = {}
            payload["aliases"] = aliases

        aliases[alias] = {
            "url": url,
            "accessKey": access_key,
            "secretKey": secret_key,
            "api": api,
            "path": "auto",
        }

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(json.dumps(payload, indent=4) + "\n", encoding="utf-8")
        return target_path

    @staticmethod
    def _parse_ssh_config(config_path: Path) -> dict[str, dict[str, str]]:
        """Parse a user's ssh config into simple host records."""
        if not config_path.is_file():
            return {}

        hosts: dict[str, dict[str, str]] = {}
        active_hosts: list[str] = []

        for raw_line in config_path.read_text(encoding="utf-8").splitlines():
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            lower = stripped.lower()
            if lower.startswith("match "):
                active_hosts = []
                continue

            parts = stripped.split(None, 1)
            if len(parts) != 2:
                continue

            key, value = parts[0].lower(), parts[1].strip()
            if key == "host":
                active_hosts = [
                    alias for alias in value.split()
                    if not any(char in alias for char in "*?!")
                ]
                for alias in active_hosts:
                    hosts.setdefault(alias, {})
                continue

            if not active_hosts:
                continue

            if key in {"hostname", "user", "identityfile", "proxyjump"}:
                for alias in active_hosts:
                    hosts.setdefault(alias, {})[key] = value

        return dict(sorted(hosts.items()))

    @classmethod
    def _load_ssh_hosts(cls) -> dict[str, dict[str, str]]:
        """Load existing SSH host abbreviations from the user's ssh config."""
        return cls._parse_ssh_config(Path.home() / ".ssh/config")

    @staticmethod
    def _extract_ssh_runtime_preferences(state: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
        """Extract persisted per-alias SSH runtime preferences from dialog state."""
        if not isinstance(state, dict):
            return {}
        raw = state.get("ssh_runtime_preferences")
        if not isinstance(raw, dict):
            return {}

        cleaned: dict[str, dict[str, Any]] = {}
        for alias, prefs in raw.items():
            if not isinstance(alias, str) or not alias.strip() or not isinstance(prefs, dict):
                continue
            entry: dict[str, Any] = {}
            remote_python = prefs.get("remote_python")
            if isinstance(remote_python, str) and remote_python.strip():
                entry["remote_python"] = remote_python.strip()
            options = prefs.get("remote_python_options")
            if isinstance(options, dict):
                option_map = {
                    str(key): str(value)
                    for key, value in options.items()
                    if str(key).strip() and str(value).strip()
                }
                if option_map:
                    entry["remote_python_options"] = option_map
            login_shell = prefs.get("login_shell")
            if isinstance(login_shell, bool):
                entry["login_shell"] = login_shell
            elif isinstance(login_shell, str):
                entry["login_shell"] = login_shell.strip().lower() in {"1", "true", "yes", "on"}
            if entry:
                cleaned[alias.strip()] = entry
        return cleaned

    @staticmethod
    def _apply_ssh_runtime_preferences(
        hosts: dict[str, dict[str, Any]],
        runtime_prefs: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Merge persisted runtime SSH preferences into loaded SSH host entries."""
        merged: dict[str, dict[str, Any]] = {
            alias: dict(details)
            for alias, details in hosts.items()
            if isinstance(details, dict)
        }
        for alias, prefs in runtime_prefs.items():
            if not isinstance(alias, str) or not alias.strip() or not isinstance(prefs, dict):
                continue
            details = dict(merged.get(alias, {}))
            details.update(prefs)
            merged[alias] = details
        return dict(sorted(merged.items()))

    @staticmethod
    def _render_ssh_host_block(
        alias: str,
        hostname: str,
        user: str,
        identity_file: str,
        proxy_jump: str,
    ) -> str:
        """Render an ssh config block for a single host alias."""
        lines = [f"Host {alias}", f"    HostName {hostname}", f"    User {user}"]
        if identity_file.strip():
            lines.append(f"    IdentityFile {identity_file.strip()}")
        if proxy_jump.strip():
            lines.append(f"    ProxyJump {proxy_jump.strip()}")
        return "\n".join(lines)

    @classmethod
    def _upsert_ssh_config_text(
        cls,
        existing_text: str,
        alias: str,
        hostname: str,
        user: str,
        identity_file: str,
        proxy_jump: str = "",
    ) -> str:
        """Insert or replace a named host block in ssh config text."""
        lines = existing_text.splitlines()
        block_lines = cls._render_ssh_host_block(
            alias,
            hostname,
            user,
            identity_file,
            proxy_jump,
        ).splitlines()

        start_idx: int | None = None
        end_idx: int | None = None
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if not stripped.lower().startswith("host "):
                continue

            aliases = stripped.split()[1:]
            if alias in aliases:
                start_idx = idx
                end_idx = len(lines)
                for probe in range(idx + 1, len(lines)):
                    probe_stripped = lines[probe].strip().lower()
                    if probe_stripped.startswith("host ") or probe_stripped.startswith("match "):
                        end_idx = probe
                        break
                break

        if start_idx is not None and end_idx is not None:
            new_lines = lines[:start_idx] + block_lines + lines[end_idx:]
        else:
            new_lines = list(lines)
            if new_lines and new_lines[-1].strip():
                new_lines.append("")
            new_lines.extend(block_lines)

        return "\n".join(new_lines).rstrip() + "\n"

    @classmethod
    def _save_ssh_host(
        cls,
        alias: str,
        hostname: str,
        user: str,
        identity_file: str,
        proxy_jump: str = "",
        *,
        config_path: Path | None = None,
    ) -> Path:
        """Persist an SSH host alias to the user's ssh config file."""
        target_path = config_path or (Path.home() / ".ssh/config")
        existing_text = target_path.read_text(encoding="utf-8") if target_path.is_file() else ""
        updated_text = cls._upsert_ssh_config_text(
            existing_text,
            alias,
            hostname,
            user,
            identity_file,
            proxy_jump,
        )
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(updated_text, encoding="utf-8")
        return target_path

    def _set_combo_items(self, combo: QComboBox, items: list[str], empty_label: str) -> None:
        """Populate a combo box with items or a disabled empty-state entry."""
        combo.clear()
        if items:
            combo.addItems(items)
            combo.setEnabled(True)
        else:
            combo.addItem(empty_label)
            combo.setEnabled(False)

    @classmethod
    def _set_line_edit_character_width(cls, widget: QLineEdit) -> None:
        """Give line edits a practical width in character units."""
        width_px = widget.fontMetrics().horizontalAdvance("M" * cls._WIDE_FIELD_CHARS)
        widget.setMinimumWidth(width_px)

    def _build_s3_tab(self) -> QWidget:
        """Build S3 configuration controls."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("S3 setup:"))
        self.s3_mode_combo = QComboBox()
        self.s3_mode_combo.addItems(self._S3_MODES)
        self.s3_mode_combo.currentTextChanged.connect(self._update_s3_mode)
        mode_row.addWidget(self.s3_mode_combo, 1)
        layout.addLayout(mode_row)

        aliases = list(self._s3_locations.keys())

        self.s3_existing_group = QGroupBox("Existing configuration")
        existing_layout = QFormLayout(self.s3_existing_group)
        self.s3_existing_combo = QComboBox()
        self._set_combo_items(self.s3_existing_combo, aliases, "No existing configurations")
        self.s3_existing_combo.currentTextChanged.connect(self._update_s3_selected_details)
        self.s3_existing_url = QLineEdit("")
        self.s3_existing_url.setReadOnly(True)
        self._set_line_edit_character_width(self.s3_existing_url)
        self.s3_existing_api = QLineEdit("")
        self.s3_existing_api.setReadOnly(True)
        self.s3_existing_reductionist_url = QLineEdit("")
        self.s3_existing_reductionist_url.setReadOnly(True)
        self._set_line_edit_character_width(self.s3_existing_reductionist_url)
        existing_layout.addRow("Host alias:", self.s3_existing_combo)
        existing_layout.addRow("URL:", self.s3_existing_url)
        existing_layout.addRow("API:", self.s3_existing_api)
        existing_layout.addRow("(Optional) Reductionist URL:", self.s3_existing_reductionist_url)
        layout.addWidget(self.s3_existing_group)

        self.s3_config_group = QGroupBox("Use config file")
        config_layout = QFormLayout(self.s3_config_group)
        self.s3_config_combo = QComboBox()
        self._set_combo_items(self.s3_config_combo, aliases, "No config file entries")
        self.s3_config_combo.currentTextChanged.connect(self._update_s3_config_details)
        self.s3_config_url = QLineEdit("")
        self.s3_config_url.setReadOnly(True)
        self._set_line_edit_character_width(self.s3_config_url)
        self.s3_config_api = QLineEdit("")
        self.s3_config_api.setReadOnly(True)
        self.s3_config_reductionist_url = QLineEdit("")
        self.s3_config_reductionist_url.setReadOnly(True)
        self._set_line_edit_character_width(self.s3_config_reductionist_url)
        config_layout.addRow("Config entry:", self.s3_config_combo)
        config_layout.addRow("URL:", self.s3_config_url)
        config_layout.addRow("API:", self.s3_config_api)
        config_layout.addRow("(Optional) Reductionist URL:", self.s3_config_reductionist_url)
        layout.addWidget(self.s3_config_group)

        self.s3_new_group = QGroupBox("Add new")
        new_layout = QFormLayout(self.s3_new_group)
        self.s3_alias_edit = QLineEdit("")
        self.s3_url_edit = QLineEdit("")
        self._set_line_edit_character_width(self.s3_url_edit)
        self.s3_access_key_edit = QLineEdit("")
        self.s3_secret_key_edit = QLineEdit("")
        self.s3_secret_key_edit.setEchoMode(QLineEdit.Password)
        self.s3_reductionist_url_edit = QLineEdit("")
        self._set_line_edit_character_width(self.s3_reductionist_url_edit)
        self.s3_api_combo = QComboBox()
        self.s3_api_combo.addItems(["S3v4"])
        self.s3_api_combo.setEnabled(False)
        self.s3_config_target_combo = QComboBox()
        self.s3_config_target_combo.addItems(["MinIO", "xconv"])
        self.s3_config_target_combo.setCurrentText("MinIO")
        new_layout.addRow("Host alias:", self.s3_alias_edit)
        new_layout.addRow("URL:", self.s3_url_edit)
        new_layout.addRow("Access Key:", self.s3_access_key_edit)
        new_layout.addRow("Secret Key:", self.s3_secret_key_edit)
        new_layout.addRow("(Optional) Reductionist URL:", self.s3_reductionist_url_edit)
        new_layout.addRow("API:", self.s3_api_combo)
        new_layout.addRow("Config target:", self.s3_config_target_combo)
        layout.addWidget(self.s3_new_group)

        layout.addStretch(1)
        return tab

    def _update_s3_selected_details(self) -> None:
        """Refresh preview fields for an existing S3 configuration."""
        alias = self.s3_existing_combo.currentText()
        details = self._s3_locations.get(alias, {})
        self.s3_existing_url.setText(str(details.get("url", "")))
        self.s3_existing_api.setText(str(details.get("api", "")))
        self.s3_existing_reductionist_url.setText(str(details.get("reductionist_url", "")))

    def _update_s3_config_details(self) -> None:
        """Refresh preview fields for a config-file-backed S3 configuration."""
        alias = self.s3_config_combo.currentText()
        details = self._s3_locations.get(alias, {})
        self.s3_config_url.setText(str(details.get("url", "")))
        self.s3_config_api.setText(str(details.get("api", "")))
        self.s3_config_reductionist_url.setText(str(details.get("reductionist_url", "")))

    def _update_s3_mode(self) -> None:
        """Show the relevant S3 subframe for the selected configuration mode."""
        mode = self.s3_mode_combo.currentText()
        self.s3_existing_group.setVisible(mode == "Select from existing")
        self.s3_new_group.setVisible(mode == "Add new")
        self.s3_config_group.setVisible(False)
        if mode == "Add new":
            self.s3_alias_edit.clear()
            self.s3_url_edit.clear()
            self.s3_access_key_edit.clear()
            self.s3_secret_key_edit.clear()
            self.s3_reductionist_url_edit.clear()

    def _build_http_tab(self) -> QWidget:
        """Build HTTPS configuration controls."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("HTTPS setup:"))
        self.http_mode_combo = QComboBox()
        self.http_mode_combo.addItems(self._HTTPS_MODES)
        self.http_mode_combo.currentTextChanged.connect(self._update_http_mode)
        mode_row.addWidget(self.http_mode_combo, 1)
        layout.addLayout(mode_row)

        aliases = list(self._http_locations.keys())

        self.http_existing_group = QGroupBox("Existing configuration")
        existing_layout = QFormLayout(self.http_existing_group)
        self.http_existing_combo = QComboBox()
        self._set_combo_items(self.http_existing_combo, aliases, "No HTTPS configurations found")
        self.http_existing_combo.currentTextChanged.connect(self._update_http_selected_details)
        self.http_existing_url = QLineEdit("")
        self.http_existing_url.setReadOnly(True)
        self._set_line_edit_character_width(self.http_existing_url)
        self.http_existing_reductionist_url = QLineEdit("")
        self.http_existing_reductionist_url.setReadOnly(True)
        self._set_line_edit_character_width(self.http_existing_reductionist_url)
        existing_layout.addRow("Host alias:", self.http_existing_combo)
        existing_layout.addRow("URL:", self.http_existing_url)
        existing_layout.addRow("(Optional) Reductionist URL:", self.http_existing_reductionist_url)
        layout.addWidget(self.http_existing_group)

        self.http_new_group = QGroupBox("Add new")
        new_layout = QFormLayout(self.http_new_group)
        self.http_alias_edit = QLineEdit("")
        self.http_url_edit = QLineEdit("")
        self._set_line_edit_character_width(self.http_url_edit)
        self.http_reductionist_url_edit = QLineEdit("")
        self._set_line_edit_character_width(self.http_reductionist_url_edit)
        new_layout.addRow("Host alias:", self.http_alias_edit)
        new_layout.addRow("Remote HTTPS URL:", self.http_url_edit)
        new_layout.addRow("(Optional) Reductionist URL:", self.http_reductionist_url_edit)
        layout.addWidget(self.http_new_group)

        layout.addStretch(1)
        return tab

    def _update_http_mode(self) -> None:
        """Show the relevant HTTPS subframe for the selected mode."""
        mode = self.http_mode_combo.currentText()
        self.http_existing_group.setVisible(mode == "Select from existing")
        self.http_new_group.setVisible(mode == "Add new")
        if mode == "Add new":
            self.http_alias_edit.clear()
            self.http_url_edit.clear()
            self.http_reductionist_url_edit.clear()

    def _update_http_selected_details(self) -> None:
        """Refresh preview fields for an existing HTTPS alias."""
        alias = self.http_existing_combo.currentText().strip()
        details = self._http_locations.get(alias, {})
        url = details.get("url") if isinstance(details, dict) else ""
        reductionist_url = details.get("reductionist_url") if isinstance(details, dict) else ""
        self.http_existing_url.setText(url if isinstance(url, str) else "")
        self.http_existing_reductionist_url.setText(reductionist_url if isinstance(reductionist_url, str) else "")

    def _select_saved_http_alias(self, alias: str, details: dict[str, str]) -> None:
        """Switch UI state to an existing HTTPS selection after saving a new alias."""
        self._http_locations[alias] = details
        aliases = list(self._http_locations.keys())
        self._set_combo_items(self.http_existing_combo, aliases, "No HTTPS configurations found")
        index = self.http_existing_combo.findText(alias)
        if index >= 0:
            self.http_existing_combo.setCurrentIndex(index)
        self.http_mode_combo.setCurrentText("Select from existing")
        self._update_http_selected_details()
        self._update_http_mode()

    def _build_ssh_tab(self) -> QWidget:
        """Build SSH configuration controls."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("SSH setup:"))
        self.ssh_mode_combo = QComboBox()
        self.ssh_mode_combo.addItems(self._SSH_MODES)
        self.ssh_mode_combo.currentTextChanged.connect(self._update_ssh_mode)
        mode_row.addWidget(self.ssh_mode_combo, 1)
        layout.addLayout(mode_row)

        hosts = list(self._ssh_hosts.keys())

        self.ssh_existing_group = QGroupBox("Existing host")
        existing_layout = QFormLayout(self.ssh_existing_group)
        self.ssh_existing_combo = QComboBox()
        self._set_combo_items(self.ssh_existing_combo, hosts, "No ssh hosts found")
        self.ssh_existing_combo.currentTextChanged.connect(self._update_ssh_selected_details)
        self.ssh_existing_hostname = QLineEdit("")
        self.ssh_existing_hostname.setReadOnly(True)
        self._set_line_edit_character_width(self.ssh_existing_hostname)
        self.ssh_existing_user = QLineEdit("")
        self.ssh_existing_user.setReadOnly(True)
        self.ssh_existing_identity = QLineEdit("")
        self.ssh_existing_identity.setReadOnly(True)
        self._set_line_edit_character_width(self.ssh_existing_identity)
        self.ssh_existing_proxyjump = QLineEdit("")
        self.ssh_existing_proxyjump.setReadOnly(True)
        existing_layout.addRow("Host alias:", self.ssh_existing_combo)
        existing_layout.addRow("Hostname:", self.ssh_existing_hostname)
        existing_layout.addRow("User:", self.ssh_existing_user)
        existing_layout.addRow("Identity File:", self.ssh_existing_identity)
        existing_layout.addRow("ProxyJump:", self.ssh_existing_proxyjump)
        layout.addWidget(self.ssh_existing_group)

        self.ssh_new_group = QGroupBox("Add new")
        new_layout = QFormLayout(self.ssh_new_group)
        self.ssh_alias_edit = QLineEdit("")
        self.ssh_hostname_edit = QLineEdit("")
        self._set_line_edit_character_width(self.ssh_hostname_edit)
        self.ssh_user_edit = QLineEdit("")
        self.ssh_proxy_jump_edit = QLineEdit("")
        identity_row = QHBoxLayout()
        self.ssh_identity_file_edit = QLineEdit("")
        self._set_line_edit_character_width(self.ssh_identity_file_edit)
        identity_browse = QPushButton("Browse...")
        identity_browse.clicked.connect(self._choose_ssh_identity_file)
        identity_row.addWidget(self.ssh_identity_file_edit, 1)
        identity_row.addWidget(identity_browse)
        identity_widget = QWidget()
        identity_widget.setLayout(identity_row)
        new_layout.addRow("Host alias:", self.ssh_alias_edit)
        new_layout.addRow("Hostname:", self.ssh_hostname_edit)
        new_layout.addRow("User:", self.ssh_user_edit)
        new_layout.addRow("Identity File:", identity_widget)
        new_layout.addRow("ProxyJump (optional):", self.ssh_proxy_jump_edit)
        layout.addWidget(self.ssh_new_group)

        # Runtime-only SSH execution options for p5rem bootstrap.
        self.ssh_runtime_group = QGroupBox("Remote Python (must include 'pyfive' and 'cbor2'; no special reductionist needed)")
        runtime_layout = QFormLayout(self.ssh_runtime_group)
        remote_python_row = QHBoxLayout()
        self.ssh_remote_python_combo = QComboBox()
        self.ssh_remote_python_combo.setEditable(False)
        self.ssh_remote_python_combo.addItem("python3", "python3")
        self.ssh_remote_python_combo.setMinimumWidth(
            self.ssh_remote_python_combo.fontMetrics().horizontalAdvance("M" * self._WIDE_FIELD_CHARS)
        )
        discover_button = QPushButton("Discover Envs...")
        discover_button.clicked.connect(self._discover_ssh_remote_python)
        remote_python_row.addWidget(self.ssh_remote_python_combo, 1)
        remote_python_row.addWidget(discover_button)
        remote_python_widget = QWidget()
        remote_python_widget.setLayout(remote_python_row)

        self.ssh_login_shell_check = QCheckBox("Use login shell (bash -lc)")
        self.ssh_login_shell_check.setChecked(False)

        runtime_layout.addRow("Remote Python:", remote_python_widget)
        runtime_layout.addRow("", self.ssh_login_shell_check)
        layout.addWidget(self.ssh_runtime_group)

        layout.addStretch(1)
        return tab

    @staticmethod
    def _coerce_bool(value: object, default: bool = False) -> bool:
        """Convert loose config values to a strict bool."""
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
        return default

    @staticmethod
    def _remote_python_options_from_envs(envs: dict[str, str]) -> dict[str, str]:
        """Build {label: bootstrap_command} options from discovered conda env paths."""
        options: dict[str, str] = {"python3": "python3"}
        for env_name, env_path in sorted(envs.items()):
            label = str(env_name).strip()
            path = str(env_path).strip()
            if not label or not path:
                continue
            options[label] = f"conda run -p {shlex.quote(path)} --no-capture-output python"
        return options

    def _current_ssh_remote_python_command(self) -> str:
        """Return the selected command payload for the remote python combo."""
        data = self.ssh_remote_python_combo.currentData()
        if isinstance(data, str) and data.strip():
            return data.strip()
        text = self.ssh_remote_python_combo.currentText().strip()
        return text or "python3"

    def _set_ssh_remote_python_options(self, options: dict[str, str], *, preferred_command: str | None = None) -> None:
        """Populate remote python combo from {label: command} mapping."""
        current_command = self._current_ssh_remote_python_command()

        cleaned: dict[str, str] = {}
        for label, command in options.items():
            if not isinstance(label, str) or not isinstance(command, str):
                continue
            label_text = label.strip()
            command_text = command.strip()
            if label_text and command_text:
                cleaned[label_text] = command_text

        if not cleaned:
            cleaned = {"python3": "python3"}

        selected_command = (preferred_command or "").strip() or current_command
        if selected_command and selected_command not in cleaned.values():
            cleaned[f"custom: {selected_command}"] = selected_command

        self.ssh_remote_python_combo.clear()
        for label, command in cleaned.items():
            self.ssh_remote_python_combo.addItem(label, command)

        target = selected_command if selected_command else "python3"
        matched_index = -1
        for index in range(self.ssh_remote_python_combo.count()):
            value = self.ssh_remote_python_combo.itemData(index)
            if isinstance(value, str) and value == target:
                matched_index = index
                break
        self.ssh_remote_python_combo.setCurrentIndex(matched_index if matched_index >= 0 else 0)

    def _current_ssh_discovery_params(self) -> tuple[str, str, str | None, str | None]:
        """Return host-or-alias/user/key_filename/password for SSH env discovery."""
        mode = self.ssh_mode_combo.currentText()
        if mode == "Add new":
            host = self.ssh_hostname_edit.text().strip()
            user = self.ssh_user_edit.text().strip()
            key = self.ssh_identity_file_edit.text().strip() or None
            if key:
                key = str(Path(key).expanduser())
            return host, user, key, None

        alias = self.ssh_existing_combo.currentText()
        details = self._ssh_hosts.get(alias, {})
        # Use alias to let SSH config resolution supply hostname/user/key defaults.
        host = alias.strip() or str(details.get("hostname", "")).strip()
        user = str(details.get("user", "")).strip()
        key = str(details.get("identityfile", "")).strip() or None
        if key:
            key = str(Path(key).expanduser())
        password = str(details.get("password", "")).strip() or None
        return host, user, key, password

    def _discover_ssh_remote_python(self) -> None:
        """Populate remote python commands by discovering remote conda environments."""
        if discover_remote_conda_envs is None:
            QMessageBox.warning(
                self,
                "Discovery unavailable",
                "p5rem with discover_remote_conda_envs is required for remote environment discovery.",
            )
            return

        host, user, key_filename, password = self._current_ssh_discovery_params()
        if not host or not user:
            QMessageBox.warning(
                self,
                "Missing SSH details",
                "Hostname and user are required to discover remote Python environments.",
            )
            return

        try:
            envs = discover_remote_conda_envs(
                host=host,
                username=user,
                password=password,
                key_filename=key_filename,
                ssh_config_path=str(Path.home() / ".ssh" / "config"),
                login_shell=True,
                timeout=10.0,
            )
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Discovery failed",
                f"Could not discover remote environments on {host}: {exc}",
            )
            return

        options_map = self._remote_python_options_from_envs(envs)
        previous = self._current_ssh_remote_python_command()

        preferred = None
        if "work26" in envs:
            preferred = f"conda run -p {shlex.quote(str(envs['work26']))} --no-capture-output python"

        mode = self.ssh_mode_combo.currentText()
        if mode == "Add new":
            self._ssh_add_new_remote_python_options = dict(options_map)
        else:
            alias = self.ssh_existing_combo.currentText()
            details = self._ssh_hosts.get(alias)
            if isinstance(details, dict):
                details["remote_python_options"] = dict(options_map)
            prefs = dict(self._ssh_runtime_preferences.get(alias, {}))
            prefs["remote_python_options"] = dict(options_map)
            self._ssh_runtime_preferences[alias] = prefs

        selected = preferred if preferred in options_map.values() else previous
        self._set_ssh_remote_python_options(options_map, preferred_command=selected)

        self.ssh_login_shell_check.setChecked(True)
        QMessageBox.information(
            self,
            "Discovery complete",
            f"Found {len(envs)} remote conda environment(s). Remote Python options have been updated.",
        )

    def _update_ssh_mode(self) -> None:
        """Show the relevant SSH subframe for the selected configuration mode."""
        mode = self.ssh_mode_combo.currentText()
        self.ssh_existing_group.setVisible(mode == "Select from existing")
        self.ssh_new_group.setVisible(mode == "Add new")
        if mode == "Add new":
            self.ssh_alias_edit.clear()
            self.ssh_hostname_edit.clear()
            self.ssh_user_edit.clear()
            self.ssh_identity_file_edit.clear()
            self.ssh_proxy_jump_edit.clear()
            self._set_ssh_remote_python_options(self._ssh_add_new_remote_python_options)
            self.ssh_login_shell_check.setChecked(False)

    def _update_ssh_selected_details(self) -> None:
        """Refresh preview fields for an existing SSH host alias."""
        alias = self.ssh_existing_combo.currentText()
        details = self._ssh_hosts.get(alias, {})
        self.ssh_existing_hostname.setText(str(details.get("hostname", "")))
        self.ssh_existing_user.setText(str(details.get("user", "")))
        self.ssh_existing_identity.setText(str(details.get("identityfile", "")))
        self.ssh_existing_proxyjump.setText(str(details.get("proxyjump", "")))
        options = details.get("remote_python_options")
        if isinstance(options, dict):
            option_map = {str(key): str(value) for key, value in options.items()}
        elif isinstance(options, list):
            # Backward compatibility for older state shape.
            option_map = {str(item): str(item) for item in options if str(item).strip()}
        else:
            option_map = {"python3": "python3"}
        preferred = str(details.get("remote_python", "python3"))
        self._set_ssh_remote_python_options(option_map, preferred_command=preferred)
        self.ssh_login_shell_check.setChecked(self._coerce_bool(details.get("login_shell"), default=False))

    def _select_saved_s3_alias(self, alias: str, details: dict[str, Any]) -> None:
        """Switch UI state to an existing S3 selection after saving a new alias."""
        self._s3_locations[alias] = details
        aliases = list(self._s3_locations.keys())
        self._set_combo_items(self.s3_existing_combo, aliases, "No existing configurations")
        index = self.s3_existing_combo.findText(alias)
        if index >= 0:
            self.s3_existing_combo.setCurrentIndex(index)
        self.s3_mode_combo.setCurrentText("Select from existing")
        self._update_s3_selected_details()
        self._update_s3_mode()

    def _select_saved_ssh_alias(self, alias: str, details: dict[str, Any]) -> None:
        """Switch UI state to an existing SSH selection after saving a new alias."""
        self._ssh_hosts[alias] = details
        self._ssh_runtime_preferences[alias] = {
            "remote_python": str(details.get("remote_python", "python3")),
            "remote_python_options": dict(details.get("remote_python_options", {"python3": "python3"})),
            "login_shell": bool(details.get("login_shell", False)),
        }
        aliases = list(self._ssh_hosts.keys())
        self._set_combo_items(self.ssh_existing_combo, aliases, "No ssh hosts found")
        index = self.ssh_existing_combo.findText(alias)
        if index >= 0:
            self.ssh_existing_combo.setCurrentIndex(index)
        self.ssh_mode_combo.setCurrentText("Select from existing")
        self._update_ssh_selected_details()
        self._update_ssh_mode()

    def _choose_ssh_identity_file(self) -> None:
        """Browse for an SSH identity file path."""
        start_dir = str(Path.home() / ".ssh")
        selected, _ = QFileDialog.getOpenFileName(self, "Select Identity File", start_dir)
        if selected:
            self.ssh_identity_file_edit.setText(selected)

    def _build_cache_group(self) -> QWidget:
        """Build disk cache configuration controls."""
        group = QGroupBox("Cache Configuration")
        layout = QHBoxLayout(group)

        disk_group = QGroupBox("Disk Cache")
        disk_layout = QFormLayout(disk_group)
        self.disk_mode_combo = QComboBox()
        self.disk_mode_combo.addItems(self._DISK_CACHE_MODES)
        self.disk_mode_combo.setCurrentText("Disabled")
        self.disk_location_edit = QLineEdit(str(Path.home() / ".cache/xconv2"))
        disk_width_px = self.disk_location_edit.fontMetrics().horizontalAdvance("M" * self._DISK_LOCATION_CHARS)
        self.disk_location_edit.setMinimumWidth(disk_width_px)
        self.disk_limit_spin = QSpinBox()
        self.disk_limit_spin.setRange(1, 4096)
        self.disk_limit_spin.setValue(10)
        self.disk_expiry_combo = QComboBox()
        self.disk_expiry_combo.addItems(self._EXPIRY_OPTIONS)
        self.disk_expiry_combo.setCurrentText("1 day")
        disk_layout.addRow("Mode:", self.disk_mode_combo)
        disk_layout.addRow("Location:", self.disk_location_edit)
        disk_layout.addRow("Limit (GB):", self.disk_limit_spin)
        disk_layout.addRow("Expiry:", self.disk_expiry_combo)

        layout.addWidget(disk_group, 1)
        return group

    def configuration(self) -> dict[str, Any]:
        """Return the currently selected remote and cache configuration."""
        protocol = self.protocol_tabs.tabText(self.protocol_tabs.currentIndex())
        config: dict[str, Any] = {
            "protocol": protocol,
            "cache": {
                "disk_mode": self.disk_mode_combo.currentText(),
                "disk_location": self.disk_location_edit.text().strip(),
                "disk_limit_gb": int(self.disk_limit_spin.value()),
                "disk_expiry": self.disk_expiry_combo.currentText(),
            },
        }

        if protocol == "S3":
            mode = self.s3_mode_combo.currentText()
            if mode == "Add new":
                config["remote"] = {
                    "mode": mode,
                    "alias": self.s3_alias_edit.text().strip(),
                    "url": self.s3_url_edit.text().strip(),
                    "access_key": self.s3_access_key_edit.text().strip(),
                    "secret_key": self.s3_secret_key_edit.text(),
                    "reductionist_url": self.s3_reductionist_url_edit.text().strip(),
                    "api": self.s3_api_combo.currentText(),
                    "config_target": self.s3_config_target_combo.currentText(),
                }
            else:
                alias = self.s3_existing_combo.currentText()
                config["remote"] = {
                    "mode": mode,
                    "alias": alias,
                    "details": self._s3_locations.get(alias, {}),
                }
        elif protocol == "SSH":
            mode = self.ssh_mode_combo.currentText()
            if mode == "Add new":
                config["remote"] = {
                    "mode": mode,
                    "alias": self.ssh_alias_edit.text().strip(),
                    "hostname": self.ssh_hostname_edit.text().strip(),
                    "user": self.ssh_user_edit.text().strip(),
                    "identity_file": self.ssh_identity_file_edit.text().strip(),
                    "proxyjump": self.ssh_proxy_jump_edit.text().strip(),
                    "remote_python": self._current_ssh_remote_python_command(),
                    "remote_python_options": dict(self._ssh_add_new_remote_python_options),
                    "login_shell": bool(self.ssh_login_shell_check.isChecked()),
                }
            else:
                alias = self.ssh_existing_combo.currentText()
                details = dict(self._ssh_hosts.get(alias, {}))
                details["remote_python"] = self._current_ssh_remote_python_command()
                options = details.get("remote_python_options")
                if not isinstance(options, dict):
                    if isinstance(options, list):
                        details["remote_python_options"] = {str(item): str(item) for item in options if str(item).strip()}
                    else:
                        details["remote_python_options"] = {"python3": "python3"}
                details["login_shell"] = bool(self.ssh_login_shell_check.isChecked())
                config["remote"] = {
                    "mode": mode,
                    "alias": alias,
                    "details": details,
                }
        elif protocol == "HTTPS":
            mode = self.http_mode_combo.currentText()
            if mode == "Add new":
                config["remote"] = {
                    "mode": mode,
                    "alias": self.http_alias_edit.text().strip(),
                    "url": self.http_url_edit.text().strip(),
                    "reductionist_url": self.http_reductionist_url_edit.text().strip(),
                }
            else:
                alias = self.http_existing_combo.currentText().strip()
                details = self._http_locations.get(alias, {})
                if not isinstance(details, dict):
                    details = {}
                if "url" not in details and self.http_existing_url.text().strip():
                    details = dict(details)
                    details["url"] = self.http_existing_url.text().strip()
                config["remote"] = {
                    "mode": mode,
                    "alias": alias,
                    "details": details,
                }
        else:
            config["remote"] = {"mode": "Not Implemented Yet"}

        return config

    def state(self) -> dict[str, Any]:
        """Return dialog state for session persistence."""
        selected_alias = self.ssh_existing_combo.currentText().strip()
        if selected_alias:
            self._ssh_runtime_preferences[selected_alias] = {
                "remote_python": self._current_ssh_remote_python_command(),
                "remote_python_options": dict(self._ssh_hosts.get(selected_alias, {}).get("remote_python_options", {"python3": "python3"})),
                "login_shell": bool(self.ssh_login_shell_check.isChecked()),
            }

        return {
            "protocol_index": self.protocol_tabs.currentIndex(),
            "s3_mode": self.s3_mode_combo.currentText(),
            "s3_existing_alias": self.s3_existing_combo.currentText(),
            "s3_alias": self.s3_alias_edit.text().strip(),
            "s3_url": self.s3_url_edit.text().strip(),
            "s3_access_key": self.s3_access_key_edit.text().strip(),
            "s3_secret_key": self.s3_secret_key_edit.text(),
            "s3_reductionist_url": self.s3_reductionist_url_edit.text().strip(),
            "s3_reductionist_locations": self._current_s3_reductionist_locations(),
            "s3_config_target": self.s3_config_target_combo.currentText(),
            "ssh_mode": self.ssh_mode_combo.currentText(),
            "ssh_existing_alias": self.ssh_existing_combo.currentText(),
            "ssh_alias": self.ssh_alias_edit.text().strip(),
            "ssh_hostname": self.ssh_hostname_edit.text().strip(),
            "ssh_user": self.ssh_user_edit.text().strip(),
            "ssh_identity_file": self.ssh_identity_file_edit.text().strip(),
            "ssh_proxy_jump": self.ssh_proxy_jump_edit.text().strip(),
            "ssh_remote_python": self._current_ssh_remote_python_command(),
            "ssh_remote_python_options": dict(self._ssh_add_new_remote_python_options),
            "ssh_login_shell": bool(self.ssh_login_shell_check.isChecked()),
            "ssh_runtime_preferences": dict(self._ssh_runtime_preferences),
            "https_mode": self.http_mode_combo.currentText(),
            "https_existing_alias": self.http_existing_combo.currentText().strip(),
            "https_alias": self.http_alias_edit.text().strip(),
            "https_url": self.http_url_edit.text().strip(),
            "https_reductionist_url": self.http_reductionist_url_edit.text().strip(),
            "https_locations": dict(self._http_locations),
            "disk_mode": self.disk_mode_combo.currentText(),
            "disk_location": self.disk_location_edit.text().strip(),
            "disk_limit_gb": int(self.disk_limit_spin.value()),
            "disk_expiry": self.disk_expiry_combo.currentText(),
        }

    def _restore_state(self, state: dict[str, Any] | None) -> None:
        """Restore the last-used dialog state from persisted settings."""
        if not isinstance(state, dict):
            return

        protocol_index = state.get("protocol_index")
        if isinstance(protocol_index, int) and 0 <= protocol_index < self.protocol_tabs.count():
            self.protocol_tabs.setCurrentIndex(protocol_index)

        s3_mode = state.get("s3_mode")
        if isinstance(s3_mode, str):
            index = self.s3_mode_combo.findText(s3_mode)
            if index >= 0:
                self.s3_mode_combo.setCurrentIndex(index)

        s3_existing_alias = state.get("s3_existing_alias")
        if isinstance(s3_existing_alias, str):
            index = self.s3_existing_combo.findText(s3_existing_alias)
            if index >= 0:
                self.s3_existing_combo.setCurrentIndex(index)

        if isinstance(state.get("s3_alias"), str):
            self.s3_alias_edit.setText(str(state["s3_alias"]))
        if isinstance(state.get("s3_url"), str):
            self.s3_url_edit.setText(str(state["s3_url"]))
        if isinstance(state.get("s3_access_key"), str):
            self.s3_access_key_edit.setText(str(state["s3_access_key"]))
        if isinstance(state.get("s3_secret_key"), str):
            self.s3_secret_key_edit.setText(str(state["s3_secret_key"]))
        if isinstance(state.get("s3_reductionist_url"), str):
            self.s3_reductionist_url_edit.setText(str(state["s3_reductionist_url"]))
        s3_config_target = state.get("s3_config_target")
        if isinstance(s3_config_target, str):
            index = self.s3_config_target_combo.findText(s3_config_target)
            if index >= 0:
                self.s3_config_target_combo.setCurrentIndex(index)

        ssh_mode = state.get("ssh_mode")
        if isinstance(ssh_mode, str):
            index = self.ssh_mode_combo.findText(ssh_mode)
            if index >= 0:
                self.ssh_mode_combo.setCurrentIndex(index)

        ssh_existing_alias = state.get("ssh_existing_alias")
        if isinstance(ssh_existing_alias, str):
            index = self.ssh_existing_combo.findText(ssh_existing_alias)
            if index >= 0:
                self.ssh_existing_combo.setCurrentIndex(index)

        if isinstance(state.get("ssh_alias"), str):
            self.ssh_alias_edit.setText(str(state["ssh_alias"]))
        if isinstance(state.get("ssh_hostname"), str):
            self.ssh_hostname_edit.setText(str(state["ssh_hostname"]))
        if isinstance(state.get("ssh_user"), str):
            self.ssh_user_edit.setText(str(state["ssh_user"]))
        if isinstance(state.get("ssh_identity_file"), str):
            self.ssh_identity_file_edit.setText(str(state["ssh_identity_file"]))
        if isinstance(state.get("ssh_proxy_jump"), str):
            self.ssh_proxy_jump_edit.setText(str(state["ssh_proxy_jump"]))
        options = state.get("ssh_remote_python_options")
        if isinstance(options, dict):
            self._ssh_add_new_remote_python_options = {
                str(key): str(value)
                for key, value in options.items()
                if str(key).strip() and str(value).strip()
            }
            if not self._ssh_add_new_remote_python_options:
                self._ssh_add_new_remote_python_options = {"python3": "python3"}
        elif isinstance(options, list):
            # Backward compatibility for older saved state shape.
            converted = {str(item): str(item) for item in options if str(item).strip()}
            self._ssh_add_new_remote_python_options = converted or {"python3": "python3"}
        if isinstance(state.get("ssh_remote_python"), str):
            self._set_ssh_remote_python_options(
                self._ssh_add_new_remote_python_options,
                preferred_command=str(state["ssh_remote_python"]),
            )
        if "ssh_login_shell" in state:
            self.ssh_login_shell_check.setChecked(self._coerce_bool(state.get("ssh_login_shell"), default=False))

        https_mode = state.get("https_mode")
        if isinstance(https_mode, str):
            index = self.http_mode_combo.findText(https_mode)
            if index >= 0:
                self.http_mode_combo.setCurrentIndex(index)

        https_existing_alias = state.get("https_existing_alias")
        if isinstance(https_existing_alias, str):
            index = self.http_existing_combo.findText(https_existing_alias)
            if index >= 0:
                self.http_existing_combo.setCurrentIndex(index)

        http_alias = state.get("https_alias")
        if not isinstance(http_alias, str):
            http_alias = state.get("http_alias")
        if isinstance(http_alias, str):
            self.http_alias_edit.setText(http_alias)

        https_url = state.get("https_url")
        if not isinstance(https_url, str):
            https_url = state.get("http_url")
        if isinstance(https_url, str):
            self.http_url_edit.setText(https_url)

        https_reductionist_url = state.get("https_reductionist_url")
        if isinstance(https_reductionist_url, str):
            self.http_reductionist_url_edit.setText(https_reductionist_url)

        disk_mode = state.get("disk_mode")
        if isinstance(disk_mode, str):
            index = self.disk_mode_combo.findText(disk_mode)
            if index >= 0:
                self.disk_mode_combo.setCurrentIndex(index)

        if isinstance(state.get("disk_location"), str):
            self.disk_location_edit.setText(str(state["disk_location"]))

        disk_limit = state.get("disk_limit_gb")
        if isinstance(disk_limit, int):
            self.disk_limit_spin.setValue(disk_limit)

        disk_expiry = state.get("disk_expiry")
        if isinstance(disk_expiry, str):
            index = self.disk_expiry_combo.findText(disk_expiry)
            if index >= 0:
                self.disk_expiry_combo.setCurrentIndex(index)

        self._update_s3_mode()
        self._update_s3_selected_details()
        self._update_ssh_mode()
        self._update_ssh_selected_details()
        self._update_http_mode()
        self._update_http_selected_details()

    def _validate_new_s3(self) -> bool:
        """Validate required fields for a new S3 configuration."""
        if not self.s3_alias_edit.text().strip() or not self.s3_url_edit.text().strip():
            QMessageBox.warning(self, "Missing S3 details", "S3 short name and URL are required.")
            return False
        return True

    def _validate_new_ssh(self) -> bool:
        """Validate required fields for a new SSH configuration."""
        if (
            not self.ssh_alias_edit.text().strip()
            or not self.ssh_hostname_edit.text().strip()
            or not self.ssh_user_edit.text().strip()
        ):
            QMessageBox.warning(self, "Missing SSH details", "SSH short name, hostname, and user are required.")
            return False
        return True

    def _validate_http(self) -> bool:
        """Validate required fields for HTTPS configuration."""
        mode = self.http_mode_combo.currentText()
        if mode == "Add new":
            if not self.http_alias_edit.text().strip() or not self.http_url_edit.text().strip():
                QMessageBox.warning(self, "Missing HTTPS details", "HTTPS short name and remote URL are required.")
                return False
        elif not self.http_existing_combo.isEnabled() or not self.http_existing_combo.currentText().strip():
            QMessageBox.warning(self, "Missing HTTPS details", "No existing HTTPS configuration is available.")
            return False
        else:
            alias = self.http_existing_combo.currentText().strip()
            details = self._http_locations.get(alias)
            if not isinstance(details, dict) or not str(details.get("url", "")).strip():
                QMessageBox.warning(self, "Missing HTTPS details", "Selected HTTPS alias has no URL configured.")
                return False
        return True

    def _persist_current_protocol_configuration(self) -> bool:
        """Persist any Add new entries for the currently selected protocol."""
        protocol = self.protocol_tabs.tabText(self.protocol_tabs.currentIndex())

        if protocol == "S3" and self.s3_mode_combo.currentText() == "Add new":
            if not self._validate_new_s3():
                return False
            alias = self.s3_alias_edit.text().strip()
            details = {
                "url": self.s3_url_edit.text().strip(),
                "accessKey": self.s3_access_key_edit.text().strip(),
                "secretKey": self.s3_secret_key_edit.text(),
                "api": self.s3_api_combo.currentText(),
                "path": "auto",
            }
            reductionist_url = self.s3_reductionist_url_edit.text().strip()
            if reductionist_url:
                details["reductionist_url"] = reductionist_url
            self._save_s3_location(
                alias,
                str(details["url"]),
                str(details["accessKey"]),
                str(details["secretKey"]),
                str(details["api"]),
                config_path=self._s3_config_path_from_choice(self.s3_config_target_combo.currentText()),
            )
            self._select_saved_s3_alias(alias, details)

        if protocol == "SSH" and self.ssh_mode_combo.currentText() == "Add new":
            if not self._validate_new_ssh():
                return False
            alias = self.ssh_alias_edit.text().strip()
            details = {
                "hostname": self.ssh_hostname_edit.text().strip(),
                "user": self.ssh_user_edit.text().strip(),
                "identityfile": self.ssh_identity_file_edit.text().strip(),
                "proxyjump": self.ssh_proxy_jump_edit.text().strip(),
                "remote_python": self._current_ssh_remote_python_command(),
                "remote_python_options": dict(self._ssh_add_new_remote_python_options),
                "login_shell": bool(self.ssh_login_shell_check.isChecked()),
            }
            self._save_ssh_host(
                alias,
                details["hostname"],
                details["user"],
                details["identityfile"],
                details["proxyjump"],
            )
            self._select_saved_ssh_alias(alias, details)

        if protocol == "HTTPS":
            if not self._validate_http():
                return False
            if self.http_mode_combo.currentText() == "Add new":
                alias = self.http_alias_edit.text().strip()
                url = self.http_url_edit.text().strip()
                details: dict[str, str] = {"url": url}
                reductionist_url = self.http_reductionist_url_edit.text().strip()
                if reductionist_url:
                    details["reductionist_url"] = reductionist_url
                self._select_saved_http_alias(alias, details)

        return True

    def _save_and_close(self) -> None:
        """Persist configuration changes and close without opening a remote file picker."""
        if not self._persist_current_protocol_configuration():
            return
        self.done(self._RESULT_SAVED_ONLY)

    def accept(self) -> None:  # type: ignore[override]
        """Persist new S3/SSH configurations before opening with this selection."""
        if not self._persist_current_protocol_configuration():
            return

        super().accept()

    @classmethod
    def get_configuration(
        cls,
        parent: QWidget | None,
        state: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, bool, dict[str, Any]]:
        """Show the remote configuration dialog and return collected values."""
        dialog = cls(parent, state=state)
        if dialog.exec() != QDialog.Accepted:
            return None, False, dialog.state()
        return dialog.configuration(), True, dialog.state()

    @classmethod
    def show_non_modal(
        cls,
        parent: QWidget | None,
        state: dict[str, Any] | None = None,
        on_finished: Callable[[dict[str, Any] | None, bool, dict[str, Any]], None] | None = None,
    ) -> "RemoteConfigurationDialog":
        """Show the dialog non-modally and call on_finished(config, ok, next_state) when done."""
        dialog = cls(parent, state=state)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.setWindowModality(Qt.NonModal)

        def _on_finished(result: int) -> None:
            if on_finished is None:
                return
            next_state = dialog.state()
            if result != QDialog.Accepted:
                on_finished(None, False, next_state)
                return
            config = dialog.configuration()
            on_finished(config, config is not None, next_state)

        dialog.finished.connect(_on_finished)
        dialog.show()
        return dialog


class RemoteOpenDialog(QDialog):
    """Open an existing remote configuration by short name."""

    _RESULT_CONFIGURE_NEW = 2

    def __init__(self, parent: QWidget | None, state: dict[str, Any] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Open Remote")
        self.resize(560, 260)

        self._s3_locations = RemoteConfigurationDialog._load_s3_locations(state)
        self._ssh_runtime_preferences = RemoteConfigurationDialog._extract_ssh_runtime_preferences(state)
        self._ssh_hosts = RemoteConfigurationDialog._apply_ssh_runtime_preferences(
            RemoteConfigurationDialog._load_ssh_hosts(),
            self._ssh_runtime_preferences,
        )
        self._http_locations = self._load_http_locations(state)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Choose a saved remote short name to open"))

        self.protocol_tabs = QTabWidget()
        self.protocol_tabs.addTab(self._build_s3_tab(), "S3")
        self.protocol_tabs.addTab(self._build_http_tab(), "HTTPS")
        self.protocol_tabs.addTab(self._build_ssh_tab(), "SSH")
        layout.addWidget(self.protocol_tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.configure_button = buttons.addButton("Config New Remote", QDialogButtonBox.ActionRole)
        self.open_button = buttons.addButton("Open", QDialogButtonBox.AcceptRole)
        buttons.rejected.connect(self.reject)
        self.configure_button.clicked.connect(self._configure_new_remote)
        self.open_button.clicked.connect(self.accept)
        layout.addWidget(buttons)

        self._restore_state(state)
        self._refresh_open_button()
        self.protocol_tabs.currentChanged.connect(self._refresh_open_button)

    @staticmethod
    def _load_http_locations(state: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
        """Load optional HTTPS aliases from persisted dialog state."""
        if not isinstance(state, dict):
            return {}
        raw = state.get("https_locations")
        if not isinstance(raw, dict):
            raw = state.get("http_locations")
        return RemoteConfigurationDialog._normalize_https_locations(raw)

    @staticmethod
    def _set_combo_items(combo: QComboBox, items: list[str], empty_label: str) -> None:
        """Populate a combo and disable it when no entries exist."""
        combo.clear()
        if items:
            combo.addItems(items)
            combo.setEnabled(True)
        else:
            combo.addItem(empty_label)
            combo.setEnabled(False)

    def _build_s3_tab(self) -> QWidget:
        """Build S3 open-by-alias tab."""
        tab = QWidget()
        layout = QFormLayout(tab)
        self.s3_open_combo = QComboBox()
        self._set_combo_items(self.s3_open_combo, list(self._s3_locations.keys()), "No S3 short names found")
        layout.addRow("Host alias:", self.s3_open_combo)
        return tab

    def _build_http_tab(self) -> QWidget:
        """Build HTTPS open-by-alias tab."""
        tab = QWidget()
        layout = QFormLayout(tab)
        self.http_open_combo = QComboBox()
        self._set_combo_items(self.http_open_combo, list(self._http_locations.keys()), "No HTTPS short names found")
        layout.addRow("Host alias:", self.http_open_combo)
        return tab

    def _build_ssh_tab(self) -> QWidget:
        """Build SSH open-by-alias tab."""
        tab = QWidget()
        layout = QFormLayout(tab)
        self.ssh_open_combo = QComboBox()
        self._set_combo_items(self.ssh_open_combo, list(self._ssh_hosts.keys()), "No SSH short names found")
        layout.addRow("Host alias:", self.ssh_open_combo)
        return tab

    def _active_alias_combo(self) -> QComboBox:
        """Return the alias combo for the currently selected protocol tab."""
        protocol = self.protocol_tabs.tabText(self.protocol_tabs.currentIndex())
        if protocol == "S3":
            return self.s3_open_combo
        if protocol == "HTTPS":
            return self.http_open_combo
        return self.ssh_open_combo

    def _refresh_open_button(self) -> None:
        """Enable open only when the active protocol has at least one saved alias."""
        self.open_button.setEnabled(self._active_alias_combo().isEnabled())

    def _configure_new_remote(self) -> None:
        """Close this dialog and request opening the full configuration dialog."""
        self.done(self._RESULT_CONFIGURE_NEW)

    def _restore_state(self, state: dict[str, Any] | None) -> None:
        """Restore previous open-dialog protocol and alias choices."""
        if not isinstance(state, dict):
            return

        protocol = state.get("protocol")
        if isinstance(protocol, str):
            for index in range(self.protocol_tabs.count()):
                if self.protocol_tabs.tabText(index) == protocol:
                    self.protocol_tabs.setCurrentIndex(index)
                    break

        s3_alias = state.get("s3_alias")
        if isinstance(s3_alias, str):
            index = self.s3_open_combo.findText(s3_alias)
            if index >= 0:
                self.s3_open_combo.setCurrentIndex(index)

        http_alias = state.get("https_alias")
        if not isinstance(http_alias, str):
            http_alias = state.get("http_alias")
        if isinstance(http_alias, str):
            index = self.http_open_combo.findText(http_alias)
            if index >= 0:
                self.http_open_combo.setCurrentIndex(index)

        ssh_alias = state.get("ssh_alias")
        if isinstance(ssh_alias, str):
            index = self.ssh_open_combo.findText(ssh_alias)
            if index >= 0:
                self.ssh_open_combo.setCurrentIndex(index)

    def state(self) -> dict[str, Any]:
        """Return open-dialog state for persistence."""
        return {
            "protocol": self.protocol_tabs.tabText(self.protocol_tabs.currentIndex()),
            "s3_alias": self.s3_open_combo.currentText(),
            "https_alias": self.http_open_combo.currentText(),
            "ssh_alias": self.ssh_open_combo.currentText(),
            "ssh_runtime_preferences": dict(self._ssh_runtime_preferences),
            "s3_reductionist_locations": RemoteConfigurationDialog._normalize_s3_reductionist_locations(
                {
                    alias: details.get("reductionist_url")
                    for alias, details in self._s3_locations.items()
                    if isinstance(details, dict)
                }
            ),
            "https_locations": dict(self._http_locations),
        }

    def configuration(self) -> dict[str, Any] | None:
        """Build a remote configuration payload from the selected short name."""
        protocol = self.protocol_tabs.tabText(self.protocol_tabs.currentIndex())

        if protocol == "S3":
            alias = self.s3_open_combo.currentText()
            details = self._s3_locations.get(alias)
            if not isinstance(details, dict):
                return None
            return {
                "protocol": "S3",
                "remote": {
                    "mode": "Select from existing",
                    "alias": alias,
                    "details": details,
                },
            }

        if protocol == "HTTPS":
            alias = self.http_open_combo.currentText()
            details = self._http_locations.get(alias)
            if not isinstance(details, dict):
                return None
            return {
                "protocol": "HTTPS",
                "remote": {
                    "mode": "Select from existing",
                    "alias": alias,
                    "details": details,
                },
            }

        alias = self.ssh_open_combo.currentText()
        details = self._ssh_hosts.get(alias)
        if not isinstance(details, dict):
            return None
        return {
            "protocol": "SSH",
            "remote": {
                "mode": "Select from existing",
                "alias": alias,
                "details": details,
            },
        }

    @classmethod
    def get_configuration(
        cls,
        parent: QWidget | None,
        state: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, bool, dict[str, Any]]:
        """Show the remote-open dialog and return selected configuration (blocking)."""
        dialog = cls(parent, state=state)
        result = dialog.exec()
        next_state = dialog.state()
        if result == cls._RESULT_CONFIGURE_NEW:
            next_state["configure_new_remote"] = True
            return None, False, next_state

        if result != QDialog.Accepted:
            return None, False, dialog.state()

        config = dialog.configuration()
        if config is None:
            QMessageBox.warning(parent, "No remote configured", "No saved short name is available for this protocol.")
            return None, False, next_state
        next_state["configure_new_remote"] = False
        return config, True, next_state
