from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
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
    ) -> None:
        super().__init__(parent, flags)
        self.setWindowTitle(title)

        layout = QVBoxLayout(self)

        prompt = QLabel(label)
        layout.addWidget(prompt)

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
    ) -> tuple[str, bool]:
        """Mirror QInputDialog.getItem with extra ``doc_text`` rich-text content."""
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
    """Dialog for collecting a URI and placeholder remote access options."""

    _PROTOCOLS = ["S3", "HTTPS", "SSH"]

    def __init__(self, parent: QWidget | None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Open URI")

        layout = QVBoxLayout(self)

        uri_label = QLabel("URI:")
        layout.addWidget(uri_label)

        self.uri_edit = QLineEdit("")
        self.uri_edit.setPlaceholderText("Examples: s3://bucket/path, https://host/path, ssh://user@host/path")
        layout.addWidget(self.uri_edit)

        options_group = QGroupBox("Access options")
        options_layout = QVBoxLayout(options_group)
        self.protocol_tabs = QTabWidget()
        for protocol in self._PROTOCOLS:
            tab = QWidget()
            tab_layout = QVBoxLayout(tab)
            tab_layout.addWidget(QLabel(f"{protocol} access options are not implemented yet."))
            tab_layout.addStretch(1)
            self.protocol_tabs.addTab(tab, protocol)
        options_layout.addWidget(self.protocol_tabs)
        layout.addWidget(options_group)

        buttons = QDialogButtonBox()
        cancel_button = buttons.addButton(QDialogButtonBox.Cancel)
        quit_button = buttons.addButton("Quit", QDialogButtonBox.AcceptRole)
        cancel_button.clicked.connect(self.reject)
        quit_button.clicked.connect(self.accept)
        layout.addWidget(buttons)

    @classmethod
    def get_uri(cls, parent: QWidget | None) -> tuple[str, str, bool]:
        """Return the entered URI, selected protocol, and acceptance state."""
        dialog = cls(parent)
        if dialog.exec() != QDialog.Accepted:
            return "", "", False

        uri = dialog.uri_edit.text().strip()
        protocol = cls._PROTOCOLS[dialog.protocol_tabs.currentIndex()]
        return uri, protocol, True


class RemoteConfigurationDialog(QDialog):
    """Collect remote configuration details before opening a remote navigator."""

    _S3_MODES = ["Select from existing", "Add new"]
    _SSH_MODES = ["Select from existing", "Add new"]
    _CACHE_STRATEGIES = ["None", "Block", "Readahead", "Whole-File"]
    _DISK_CACHE_MODES = ["Disabled", "Blocks", "Files"]
    _EXPIRY_OPTIONS = ["Never", "1 day", "7 days", "30 days"]

    def __init__(self, parent: QWidget | None, state: dict[str, Any] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Remote Configuration")
        self.resize(760, 620)

        self._s3_locations = self._load_s3_locations()
        self._ssh_hosts = self._load_ssh_hosts()

        layout = QVBoxLayout(self)

        intro = QLabel("Select remote configuration type")
        layout.addWidget(intro)

        self.protocol_tabs = QTabWidget()
        self.protocol_tabs.addTab(self._build_s3_tab(), "S3")
        self.protocol_tabs.addTab(self._build_http_tab(), "HTTP")
        self.protocol_tabs.addTab(self._build_ssh_tab(), "SSH")
        layout.addWidget(self.protocol_tabs)

        layout.addWidget(self._build_cache_group())

        buttons = QDialogButtonBox()
        cancel_button = buttons.addButton(QDialogButtonBox.Cancel)
        save_button = buttons.addButton("Save", QDialogButtonBox.AcceptRole)
        cancel_button.clicked.connect(self.reject)
        save_button.clicked.connect(self.accept)
        layout.addWidget(buttons)

        self._update_s3_mode()
        self._update_s3_selected_details()
        self._update_s3_config_details()
        self._update_ssh_mode()
        self._update_ssh_selected_details()
        self._update_memory_cache_summary()
        self._restore_state(state)

    @staticmethod
    def _load_s3_locations() -> dict[str, dict[str, Any]]:
        """Load S3 location definitions from AAA config when available."""
        options = get_locations()
        if options is None:
            return {}
        locations, _ = options
        return dict(sorted(locations.items()))

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

            if key in {"hostname", "user", "identityfile"}:
                for alias in active_hosts:
                    hosts.setdefault(alias, {})[key] = value

        return dict(sorted(hosts.items()))

    @classmethod
    def _load_ssh_hosts(cls) -> dict[str, dict[str, str]]:
        """Load existing SSH host abbreviations from the user's ssh config."""
        return cls._parse_ssh_config(Path.home() / ".ssh/config")

    @staticmethod
    def _render_ssh_host_block(
        alias: str,
        hostname: str,
        user: str,
        identity_file: str,
    ) -> str:
        """Render an ssh config block for a single host alias."""
        lines = [f"Host {alias}", f"    HostName {hostname}", f"    User {user}"]
        if identity_file.strip():
            lines.append(f"    IdentityFile {identity_file.strip()}")
        return "\n".join(lines)

    @classmethod
    def _upsert_ssh_config_text(
        cls,
        existing_text: str,
        alias: str,
        hostname: str,
        user: str,
        identity_file: str,
    ) -> str:
        """Insert or replace a named host block in ssh config text."""
        lines = existing_text.splitlines()
        block_lines = cls._render_ssh_host_block(alias, hostname, user, identity_file).splitlines()

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
        *,
        config_path: Path | None = None,
    ) -> Path:
        """Persist an SSH host alias to the user's ssh config file."""
        target_path = config_path or (Path.home() / ".ssh/config")
        existing_text = target_path.read_text(encoding="utf-8") if target_path.is_file() else ""
        updated_text = cls._upsert_ssh_config_text(existing_text, alias, hostname, user, identity_file)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(updated_text, encoding="utf-8")
        return target_path

    @staticmethod
    def _calculate_max_blocks(blocksize_mb: int, ram_buffer_mb: int) -> int:
        """Derive the maximum number of cached memory blocks from limits."""
        if blocksize_mb <= 0 or ram_buffer_mb <= 0:
            return 0
        return max(1, ram_buffer_mb // blocksize_mb)

    def _set_combo_items(self, combo: QComboBox, items: list[str], empty_label: str) -> None:
        """Populate a combo box with items or a disabled empty-state entry."""
        combo.clear()
        if items:
            combo.addItems(items)
            combo.setEnabled(True)
        else:
            combo.addItem(empty_label)
            combo.setEnabled(False)

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
        self.s3_existing_api = QLineEdit("")
        self.s3_existing_api.setReadOnly(True)
        existing_layout.addRow("Configuration:", self.s3_existing_combo)
        existing_layout.addRow("URL:", self.s3_existing_url)
        existing_layout.addRow("API:", self.s3_existing_api)
        layout.addWidget(self.s3_existing_group)

        self.s3_config_group = QGroupBox("Use config file")
        config_layout = QFormLayout(self.s3_config_group)
        self.s3_config_combo = QComboBox()
        self._set_combo_items(self.s3_config_combo, aliases, "No config file entries")
        self.s3_config_combo.currentTextChanged.connect(self._update_s3_config_details)
        self.s3_config_url = QLineEdit("")
        self.s3_config_url.setReadOnly(True)
        self.s3_config_api = QLineEdit("")
        self.s3_config_api.setReadOnly(True)
        config_layout.addRow("Config entry:", self.s3_config_combo)
        config_layout.addRow("URL:", self.s3_config_url)
        config_layout.addRow("API:", self.s3_config_api)
        layout.addWidget(self.s3_config_group)

        self.s3_new_group = QGroupBox("Add new")
        new_layout = QFormLayout(self.s3_new_group)
        self.s3_alias_edit = QLineEdit("")
        self.s3_url_edit = QLineEdit("")
        self.s3_access_key_edit = QLineEdit("")
        self.s3_secret_key_edit = QLineEdit("")
        self.s3_secret_key_edit.setEchoMode(QLineEdit.Password)
        self.s3_api_combo = QComboBox()
        self.s3_api_combo.addItems(["S3v4"])
        self.s3_api_combo.setEnabled(False)
        self.s3_config_target_combo = QComboBox()
        self.s3_config_target_combo.addItems(["MinIO", "xconv"])
        self.s3_config_target_combo.setCurrentText("MinIO")
        new_layout.addRow("Short name:", self.s3_alias_edit)
        new_layout.addRow("URL:", self.s3_url_edit)
        new_layout.addRow("Access Key:", self.s3_access_key_edit)
        new_layout.addRow("Secret Key:", self.s3_secret_key_edit)
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

    def _update_s3_config_details(self) -> None:
        """Refresh preview fields for a config-file-backed S3 configuration."""
        alias = self.s3_config_combo.currentText()
        details = self._s3_locations.get(alias, {})
        self.s3_config_url.setText(str(details.get("url", "")))
        self.s3_config_api.setText(str(details.get("api", "")))

    def _update_s3_mode(self) -> None:
        """Show the relevant S3 subframe for the selected configuration mode."""
        mode = self.s3_mode_combo.currentText()
        self.s3_existing_group.setVisible(mode == "Select from existing")
        self.s3_new_group.setVisible(mode == "Add new")
        self.s3_config_group.setVisible(False)

    def _build_http_tab(self) -> QWidget:
        """Build placeholder HTTP tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addWidget(QLabel("Not Implemented Yet"))
        layout.addStretch(1)
        return tab

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
        self.ssh_existing_user = QLineEdit("")
        self.ssh_existing_user.setReadOnly(True)
        self.ssh_existing_identity = QLineEdit("")
        self.ssh_existing_identity.setReadOnly(True)
        existing_layout.addRow("Host alias:", self.ssh_existing_combo)
        existing_layout.addRow("Hostname:", self.ssh_existing_hostname)
        existing_layout.addRow("User:", self.ssh_existing_user)
        existing_layout.addRow("Identity File:", self.ssh_existing_identity)
        layout.addWidget(self.ssh_existing_group)

        self.ssh_new_group = QGroupBox("Add new")
        new_layout = QFormLayout(self.ssh_new_group)
        self.ssh_alias_edit = QLineEdit("")
        self.ssh_hostname_edit = QLineEdit("")
        self.ssh_user_edit = QLineEdit("")
        identity_row = QHBoxLayout()
        self.ssh_identity_file_edit = QLineEdit("")
        identity_browse = QPushButton("Browse...")
        identity_browse.clicked.connect(self._choose_ssh_identity_file)
        identity_row.addWidget(self.ssh_identity_file_edit, 1)
        identity_row.addWidget(identity_browse)
        identity_widget = QWidget()
        identity_widget.setLayout(identity_row)
        new_layout.addRow("Short name:", self.ssh_alias_edit)
        new_layout.addRow("Hostname:", self.ssh_hostname_edit)
        new_layout.addRow("User:", self.ssh_user_edit)
        new_layout.addRow("Identity File:", identity_widget)
        layout.addWidget(self.ssh_new_group)

        layout.addStretch(1)
        return tab

    def _update_ssh_mode(self) -> None:
        """Show the relevant SSH subframe for the selected configuration mode."""
        mode = self.ssh_mode_combo.currentText()
        self.ssh_existing_group.setVisible(mode == "Select from existing")
        self.ssh_new_group.setVisible(mode == "Add new")

    def _update_ssh_selected_details(self) -> None:
        """Refresh preview fields for an existing SSH host alias."""
        alias = self.ssh_existing_combo.currentText()
        details = self._ssh_hosts.get(alias, {})
        self.ssh_existing_hostname.setText(str(details.get("hostname", "")))
        self.ssh_existing_user.setText(str(details.get("user", "")))
        self.ssh_existing_identity.setText(str(details.get("identityfile", "")))

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

    def _select_saved_ssh_alias(self, alias: str, details: dict[str, str]) -> None:
        """Switch UI state to an existing SSH selection after saving a new alias."""
        self._ssh_hosts[alias] = details
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
        """Build shared cache configuration controls."""
        group = QGroupBox("Cache Configuration")
        layout = QHBoxLayout(group)

        memory_group = QGroupBox("Memory Cache")
        memory_layout = QFormLayout(memory_group)
        self.cache_blocksize_spin = QSpinBox()
        self.cache_blocksize_spin.setRange(1, 1024)
        self.cache_blocksize_spin.setValue(2)
        self.cache_blocksize_spin.valueChanged.connect(self._update_memory_cache_summary)
        self.cache_ram_buffer_spin = QSpinBox()
        self.cache_ram_buffer_spin.setRange(1, 262144)
        self.cache_ram_buffer_spin.setValue(1024)
        self.cache_ram_buffer_spin.valueChanged.connect(self._update_memory_cache_summary)
        self.cache_strategy_combo = QComboBox()
        self.cache_strategy_combo.addItems(self._CACHE_STRATEGIES)
        self.cache_strategy_combo.setCurrentText("Block")
        self.cache_max_blocks_label = QLabel("")
        memory_layout.addRow("Blocksize (MB):", self.cache_blocksize_spin)
        memory_layout.addRow("RAM Buffer (MB):", self.cache_ram_buffer_spin)
        memory_layout.addRow("Cache Strategy:", self.cache_strategy_combo)
        memory_layout.addRow("Max blocks:", self.cache_max_blocks_label)

        disk_group = QGroupBox("Disk Cache")
        disk_layout = QFormLayout(disk_group)
        self.disk_mode_combo = QComboBox()
        self.disk_mode_combo.addItems(self._DISK_CACHE_MODES)
        self.disk_mode_combo.setCurrentText("Disabled")
        self.disk_location_edit = QLineEdit(str(Path.home() / ".cache/xconv2"))
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

        layout.addWidget(memory_group, 1)
        layout.addWidget(disk_group, 1)
        return group

    def _update_memory_cache_summary(self) -> None:
        """Update the derived max-blocks label from current cache limits."""
        max_blocks = self._calculate_max_blocks(
            int(self.cache_blocksize_spin.value()),
            int(self.cache_ram_buffer_spin.value()),
        )
        self.cache_max_blocks_label.setText(str(max_blocks))

    def configuration(self) -> dict[str, Any]:
        """Return the currently selected remote and cache configuration."""
        protocol = self.protocol_tabs.tabText(self.protocol_tabs.currentIndex())
        config: dict[str, Any] = {
            "protocol": protocol,
            "cache": {
                "blocksize_mb": int(self.cache_blocksize_spin.value()),
                "ram_buffer_mb": int(self.cache_ram_buffer_spin.value()),
                "cache_strategy": self.cache_strategy_combo.currentText(),
                "max_blocks": self._calculate_max_blocks(
                    int(self.cache_blocksize_spin.value()),
                    int(self.cache_ram_buffer_spin.value()),
                ),
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
                }
            else:
                alias = self.ssh_existing_combo.currentText()
                config["remote"] = {
                    "mode": mode,
                    "alias": alias,
                    "details": self._ssh_hosts.get(alias, {}),
                }
        else:
            config["remote"] = {"mode": "Not Implemented Yet"}

        return config

    def state(self) -> dict[str, Any]:
        """Return dialog state for session persistence."""
        return {
            "protocol_index": self.protocol_tabs.currentIndex(),
            "s3_mode": self.s3_mode_combo.currentText(),
            "s3_existing_alias": self.s3_existing_combo.currentText(),
            "s3_alias": self.s3_alias_edit.text().strip(),
            "s3_url": self.s3_url_edit.text().strip(),
            "s3_access_key": self.s3_access_key_edit.text().strip(),
            "s3_secret_key": self.s3_secret_key_edit.text(),
            "s3_config_target": self.s3_config_target_combo.currentText(),
            "ssh_mode": self.ssh_mode_combo.currentText(),
            "ssh_existing_alias": self.ssh_existing_combo.currentText(),
            "ssh_alias": self.ssh_alias_edit.text().strip(),
            "ssh_hostname": self.ssh_hostname_edit.text().strip(),
            "ssh_user": self.ssh_user_edit.text().strip(),
            "ssh_identity_file": self.ssh_identity_file_edit.text().strip(),
            "cache_blocksize_mb": int(self.cache_blocksize_spin.value()),
            "cache_ram_buffer_mb": int(self.cache_ram_buffer_spin.value()),
            "cache_strategy": self.cache_strategy_combo.currentText(),
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

        blocksize = state.get("cache_blocksize_mb")
        if isinstance(blocksize, int):
            self.cache_blocksize_spin.setValue(blocksize)
        ram_buffer = state.get("cache_ram_buffer_mb")
        if isinstance(ram_buffer, int):
            self.cache_ram_buffer_spin.setValue(ram_buffer)

        cache_strategy = state.get("cache_strategy")
        if isinstance(cache_strategy, str):
            index = self.cache_strategy_combo.findText(cache_strategy)
            if index >= 0:
                self.cache_strategy_combo.setCurrentIndex(index)

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
        self._update_memory_cache_summary()

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

    def accept(self) -> None:  # type: ignore[override]
        """Persist new S3/SSH configurations before closing the dialog."""
        protocol = self.protocol_tabs.tabText(self.protocol_tabs.currentIndex())

        if protocol == "S3" and self.s3_mode_combo.currentText() == "Add new":
            if not self._validate_new_s3():
                return
            alias = self.s3_alias_edit.text().strip()
            details = {
                "url": self.s3_url_edit.text().strip(),
                "accessKey": self.s3_access_key_edit.text().strip(),
                "secretKey": self.s3_secret_key_edit.text(),
                "api": self.s3_api_combo.currentText(),
                "path": "auto",
            }
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
                return
            alias = self.ssh_alias_edit.text().strip()
            details = {
                "hostname": self.ssh_hostname_edit.text().strip(),
                "user": self.ssh_user_edit.text().strip(),
                "identityfile": self.ssh_identity_file_edit.text().strip(),
            }
            self._save_ssh_host(
                alias,
                details["hostname"],
                details["user"],
                details["identityfile"],
            )
            self._select_saved_ssh_alias(alias, details)

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
