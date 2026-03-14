from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class SettingsStore:
    """Persist and validate local application settings."""

    def __init__(
        self,
        settings_path: Path,
        recent_log_path: Path,
        *,
        settings_version: int,
        default_max_recent_files: int,
    ) -> None:
        self.settings_path = settings_path
        self.recent_log_path = recent_log_path
        self.settings_version = settings_version
        self.default_max_recent_files = default_max_recent_files
        self.data: dict[str, object] = {}

    def default_settings(self) -> dict[str, object]:
        """Return default persisted settings schema."""
        return {
            "version": self.settings_version,
            "recent_files": [],
            "max_recent_files": self.default_max_recent_files,
            "last_remote_configuration": {
                "protocol_index": 0,
                "s3_mode": "Select from existing",
                "s3_existing_alias": "",
                "s3_alias": "",
                "s3_url": "",
                "s3_access_key": "",
                "s3_secret_key": "",
                "s3_config_target": "MinIO",
                "ssh_mode": "Select from existing",
                "ssh_existing_alias": "",
                "ssh_alias": "",
                "ssh_hostname": "",
                "ssh_user": "",
                "ssh_identity_file": "",
                "cache_blocksize_mb": 2,
                "cache_ram_buffer_mb": 1024,
                "cache_strategy": "Block",
                "disk_mode": "Disabled",
                "disk_location": str(Path.home() / ".cache/xconv2"),
                "disk_limit_gb": 10,
                "disk_expiry": "1 day",
            },
            "field_list_rows": 12,
            "visible_coordinate_rows": 4,
            "contour_title_fontsize": 10.5,
            "page_title_fontsize": 10.0,
            "annotation_fontsize": 8.0,
            "default_plot_filename": "xconv_{timestamp}",
            "default_plot_format": "png",
            "last_save_code_dir": str(Path.home()),
            "last_save_plot_dir": str(Path.home()),
        }

    def max_recent_files(self, settings: dict[str, object] | None = None) -> int:
        """Return validated max recent-files value from settings."""
        source = settings if settings is not None else self.data
        raw = source.get("max_recent_files", self.default_max_recent_files)
        if isinstance(raw, int) and raw > 0:
            return raw
        return self.default_max_recent_files

    def load_recent_files_legacy(self, settings: dict[str, object] | None = None) -> list[str]:
        """Load old newline-based recent-file log for one-time settings migration."""
        if not self.recent_log_path.exists():
            return []

        try:
            lines = self.recent_log_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            logger.exception("Failed to read recent files log: %s", self.recent_log_path)
            return []

        recent_files: list[str] = []
        max_recent = self.max_recent_files(settings)
        for line in lines:
            path = line.strip()
            if not path or path in recent_files:
                continue
            recent_files.append(path)
            if len(recent_files) >= max_recent:
                break
        return recent_files

    def load(self) -> dict[str, object]:
        """Load JSON settings with sane defaults and legacy migration."""
        settings = self.default_settings()

        if self.settings_path.exists():
            try:
                payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    settings.update(payload)
            except (OSError, json.JSONDecodeError):
                logger.exception("Failed to read settings JSON: %s", self.settings_path)

        recent_files = settings.get("recent_files")
        if not isinstance(recent_files, list) or not recent_files:
            migrated = self.load_recent_files_legacy(settings)
            if migrated:
                settings["recent_files"] = migrated

        for key in ("last_save_code_dir", "last_save_plot_dir"):
            value = settings.get(key)
            if not isinstance(value, str) or not value.strip():
                settings[key] = str(Path.home())

        max_recent = settings.get("max_recent_files")
        if not isinstance(max_recent, int) or max_recent < 1:
            settings["max_recent_files"] = self.default_max_recent_files

        field_list_rows = settings.get("field_list_rows")
        if not isinstance(field_list_rows, int) or field_list_rows < 1:
            settings["field_list_rows"] = 12

        visible_coordinate_rows = settings.get("visible_coordinate_rows")
        if not isinstance(visible_coordinate_rows, int) or visible_coordinate_rows < 1:
            settings["visible_coordinate_rows"] = 4

        contour_title_fontsize = settings.get("contour_title_fontsize")
        if (
            not isinstance(contour_title_fontsize, (int, float))
            or float(contour_title_fontsize) <= 0
        ):
            settings["contour_title_fontsize"] = 10.5

        page_title_fontsize = settings.get("page_title_fontsize")
        if (
            not isinstance(page_title_fontsize, (int, float))
            or float(page_title_fontsize) <= 0
        ):
            settings["page_title_fontsize"] = 10.0

        annotation_fontsize = settings.get("annotation_fontsize")
        if (
            not isinstance(annotation_fontsize, (int, float))
            or float(annotation_fontsize) <= 0
        ):
            settings["annotation_fontsize"] = 8.0

        default_plot_filename = settings.get("default_plot_filename")
        if not isinstance(default_plot_filename, str) or not default_plot_filename.strip():
            settings["default_plot_filename"] = "xconv_{timestamp}"

        default_plot_format = settings.get("default_plot_format")
        if default_plot_format not in {"png", "svg", "pdf"}:
            settings["default_plot_format"] = "png"

        self.data = settings
        try:
            self.save()
        except OSError:
            logger.exception("Failed to persist settings JSON after load")

        return self.data

    def save(self) -> None:
        """Persist settings dictionary to disk as JSON."""
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(
            json.dumps(self.data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def load_recent_files(self) -> list[str]:
        """Load recent files from settings and return a sanitized list."""
        raw_recent = self.data.get("recent_files", [])
        if not isinstance(raw_recent, list):
            return []

        max_recent = self.max_recent_files()
        recent_files: list[str] = []
        for entry in raw_recent:
            if not isinstance(entry, str):
                continue
            path = entry.strip()
            if not path or path in recent_files:
                continue
            recent_files.append(path)
            if len(recent_files) >= max_recent:
                break
        return recent_files

    def save_recent_files(self, recent_files: list[str]) -> None:
        """Persist recent files list to settings."""
        self.data["recent_files"] = recent_files[: self.max_recent_files()]
        self.save()

    def record_recent_file(self, file_path: str) -> list[str]:
        """Record a file open event and return the updated recent list."""
        normalized_path = str(Path(file_path).expanduser())
        recent_files = [p for p in self.load_recent_files() if p != normalized_path]
        recent_files.insert(0, normalized_path)
        recent_files = recent_files[: self.max_recent_files()]
        self.save_recent_files(recent_files)
        return recent_files

    def default_save_path(self, settings_key: str, filename: str) -> str:
        """Build default save-file path from last-used directory setting."""
        candidate = self.data.get(settings_key, str(Path.home()))
        if isinstance(candidate, str) and candidate.strip():
            base_dir = Path(candidate).expanduser()
        else:
            base_dir = Path.home()

        if not base_dir.is_dir():
            base_dir = Path.home()

        return str(base_dir / filename)

    def remember_last_save_dir(self, settings_key: str, file_path: str) -> None:
        """Persist the parent folder of a just-saved file for future defaults."""
        parent = Path(file_path).expanduser().parent
        self.data[settings_key] = str(parent)
        self.save()
