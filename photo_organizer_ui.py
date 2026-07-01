import csv
import re
import shutil
import sys
import time
from pathlib import Path

from PySide6.QtCore import QProcess, Qt, QUrl
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QHeaderView,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "bulk_image_rename.py"
SAMPLE_IMAGE = ROOT / "assets" / "gallery-contact-sheet.png"
SAMPLE_ROOT = ROOT / "sample_images"
SAMPLE_IMPORT = SAMPLE_ROOT / "raw_import"
PROGRESS_RE = re.compile(r"\[PROC\]\s+([\d,]+)/([\d,]+)\s+\(\s*([\d.]+)%\)")
TARGET_EXTS = {".jpg", ".jpeg", ".png", ".cr2", ".dng", ".mov", ".avi", ".3gp", ".gif", ".mp4"}
SAMPLE_FILES = [
    ("IMG_4382.png", 0),
    ("beach-sunset.png", 48),
    ("birthday-table.png", 96),
    ("city-night.png", 144),
    ("dog-park.png", 192),
    ("concert-lights.png", 240),
    ("snowy-cabin.png", 288),
    ("family-scan.png", 336),
]


class StatCard(QFrame):
    def __init__(self, value: str, label: str) -> None:
        super().__init__()
        self.setObjectName("statCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(0)

        value_label = QLabel(value)
        value_label.setObjectName("statValue")
        label_label = QLabel(label)
        label_label.setObjectName("statLabel")

        layout.addWidget(value_label)
        layout.addWidget(label_label)


class OptionRow(QFrame):
    def __init__(self, checkbox: QCheckBox, description: str) -> None:
        super().__init__()
        self.setObjectName("optionRow")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)

        checkbox.setObjectName("optionCheck")
        desc_label = QLabel(description)
        desc_label.setObjectName("optionDescription")
        desc_label.setWordWrap(True)

        layout.addWidget(checkbox)
        layout.addWidget(desc_label)


def make_table_item(value: str, centered: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem(value)
    item.setForeground(QBrush(QColor("#172026")))
    if centered:
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    return item


class PhotoOrganizerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.process: QProcess | None = None
        self.latest_csv_path: Path | None = None
        self.nav_buttons: dict[str, QPushButton] = {}
        self.sections: dict[str, QWidget] = {}
        self._ensure_sample_library()
        self.setWindowTitle("Photo Library Organizer")
        self.resize(1280, 720)
        self.setMinimumSize(1100, 680)

        root = QWidget()
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        shell.addWidget(self._build_sidebar())
        shell.addWidget(self._build_workspace(), 1)

        self._apply_styles()
        self._load_sample_image()
        self._populate_demo_rows()
        self._connect_nav()

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(260)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(24, 24, 24, 18)
        layout.setSpacing(16)

        brand = QHBoxLayout()
        brand.setSpacing(12)
        mark = QLabel("PL")
        mark.setAlignment(Qt.AlignCenter)
        mark.setObjectName("brandMark")
        title_wrap = QVBoxLayout()
        title_wrap.setSpacing(0)
        eyebrow = QLabel("Photo Library")
        eyebrow.setObjectName("eyebrow")
        title = QLabel("Organizer")
        title.setObjectName("brandTitle")
        title_wrap.addWidget(eyebrow)
        title_wrap.addWidget(title)
        brand.addWidget(mark)
        brand.addLayout(title_wrap)
        layout.addLayout(brand)

        for name, active in (
            ("Scan", True),
            ("Rules", False),
            ("Duplicates", False),
            ("Logs", False),
        ):
            button = QPushButton(name)
            button.setObjectName("navActive" if active else "navButton")
            self.nav_buttons[name.lower()] = button
            layout.addWidget(button)

        layout.addStretch(1)

        safety = QFrame()
        safety.setObjectName("safetyPanel")
        safety_layout = QVBoxLayout(safety)
        safety_layout.setContentsMargins(16, 16, 16, 16)
        safety_layout.setSpacing(10)
        safety_label = QLabel("Safety Mode")
        safety_label.setObjectName("panelLabel")
        self.dry_run = QCheckBox("Dry run first")
        self.dry_run.setChecked(True)
        reset_sample = QPushButton("Reset sample")
        reset_sample.setObjectName("ghostButton")
        reset_sample.clicked.connect(self.reset_sample_library)
        note = QLabel("Preview moves, renames, duplicate actions, and CSV output before touching the library.")
        note.setWordWrap(True)
        note.setObjectName("mutedText")
        safety_layout.addWidget(safety_label)
        safety_layout.addWidget(self.dry_run)
        safety_layout.addWidget(reset_sample)
        safety_layout.addWidget(note)
        layout.addWidget(safety)

        return sidebar

    def _build_workspace(self) -> QWidget:
        scroll_area = QScrollArea()
        scroll_area.setObjectName("workspaceScroll")
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        workspace = QFrame()
        workspace.setObjectName("workspace")
        workspace.setMinimumWidth(900)
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(28, 24, 24, 24)
        layout.setSpacing(18)

        header = QHBoxLayout()
        header.setSpacing(20)
        headline_wrap = QVBoxLayout()
        headline_wrap.setSpacing(4)
        eyebrow = QLabel("Windows friendly Python utility")
        eyebrow.setObjectName("eyebrow")
        headline = QLabel("Consolidate, rename, dedupe, and organize a messy media drive.")
        headline.setObjectName("headline")
        headline.setWordWrap(True)
        headline_wrap.addWidget(eyebrow)
        headline_wrap.addWidget(headline)
        header.addLayout(headline_wrap, 1)

        self.open_csv_button = QPushButton("Open CSV")
        self.open_csv_button.setObjectName("ghostButton")
        self.open_csv_button.clicked.connect(self.open_csv)
        self.open_csv_button.setEnabled(False)
        self.run_button = QPushButton("Run dry scan")
        self.run_button.setObjectName("primaryButton")
        self.run_button.clicked.connect(self.run_organizer)
        self.dry_run.stateChanged.connect(lambda _state: self._update_run_button_label())
        header.addWidget(self.open_csv_button)
        header.addWidget(self.run_button)
        layout.addLayout(header)

        top_grid = QGridLayout()
        top_grid.setSpacing(18)
        self.scan_panel = self._build_scan_panel()
        self.preview_panel = self._build_preview_panel()
        top_grid.addWidget(self.scan_panel, 0, 0)
        top_grid.addWidget(self.preview_panel, 0, 1)
        top_grid.setColumnStretch(0, 10)
        top_grid.setColumnStretch(1, 10)
        layout.addLayout(top_grid, 0)

        lower_grid = QGridLayout()
        lower_grid.setSpacing(18)
        self.options_panel = self._build_options_panel()
        self.summary_panel = self._build_summary_panel()
        lower_grid.addWidget(self.options_panel, 0, 0)
        lower_grid.addWidget(self.summary_panel, 0, 1)
        lower_grid.setColumnStretch(0, 10)
        lower_grid.setColumnStretch(1, 8)
        layout.addLayout(lower_grid, 0)

        self.log_panel = self._build_log_panel()
        layout.addWidget(self.log_panel, 1)
        scroll_area.setWidget(workspace)
        self.scroll_area = scroll_area
        self.sections = {
            "scan": self.scan_panel,
            "rules": self.options_panel,
            "duplicates": self.summary_panel,
            "logs": self.log_panel,
        }
        return scroll_area

    def _build_scan_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setMinimumHeight(178)
        panel.setMaximumHeight(210)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        layout.addLayout(self._section_heading("Source", "Library Scan", "ExifTool ready"))

        path_label = QLabel("Root folder")
        path_label.setObjectName("fieldLabel")
        layout.addWidget(path_label)

        path_row = QHBoxLayout()
        self.root_path = QLineEdit(str(SAMPLE_ROOT))
        self.root_path.setObjectName("pathInput")
        browse = QPushButton("Browse")
        browse.setObjectName("ghostButton")
        browse.clicked.connect(self.choose_root)
        path_row.addWidget(self.root_path, 1)
        path_row.addWidget(browse)
        layout.addLayout(path_row)

        info_row = QHBoxLayout()
        info_row.setSpacing(10)
        self.sample_count_label = QLabel(f"{self._sample_media_count()} sample files")
        self.sample_count_label.setObjectName("scanInfo")
        mode_label = QLabel("Safe dry-run demo")
        mode_label.setObjectName("scanInfoMuted")
        info_row.addWidget(self.sample_count_label)
        info_row.addWidget(mode_label)
        info_row.addStretch(1)
        layout.addLayout(info_row)

        self.phase_label = QLabel("Ready")
        self.phase_label.setObjectName("fieldLabel")
        self.progress_label = QLabel("0%")
        self.progress_label.setAlignment(Qt.AlignRight)
        self.progress_label.setObjectName("fieldLabel")
        progress_meta = QHBoxLayout()
        progress_meta.addWidget(self.phase_label)
        progress_meta.addWidget(self.progress_label)
        layout.addLayout(progress_meta)

        self.progress = QFrame()
        self.progress.setObjectName("progressTrack")
        self.progress_fill = QFrame(self.progress)
        self.progress_fill.setObjectName("progressFill")
        self.progress_fill.setGeometry(0, 0, 0, 12)
        layout.addWidget(self.progress)

        return panel

    def _connect_nav(self) -> None:
        for key, button in self.nav_buttons.items():
            button.clicked.connect(lambda _checked=False, section=key: self.scroll_to_section(section))

    def scroll_to_section(self, section: str) -> None:
        target = self.sections.get(section)
        if not target:
            return
        for key, button in self.nav_buttons.items():
            button.setObjectName("navActive" if key == section else "navButton")
            button.style().unpolish(button)
            button.style().polish(button)
        self.scroll_area.ensureWidgetVisible(target, 16, 16)

    def _has_sample_source_media(self) -> bool:
        if not SAMPLE_ROOT.exists():
            return False
        for path in SAMPLE_ROOT.rglob("*"):
            if "all_photos" in path.parts:
                continue
            if path.is_file() and path.suffix.lower() in TARGET_EXTS:
                return True
        return False

    def _sample_media_count(self) -> int:
        if not SAMPLE_ROOT.exists():
            return 0
        return sum(
            1
            for path in SAMPLE_ROOT.rglob("*")
            if "all_photos" not in path.parts
            and path.is_file()
            and path.suffix.lower() in TARGET_EXTS
        )

    def _ensure_sample_library(self, force: bool = False) -> None:
        if force and SAMPLE_ROOT.exists():
            shutil.rmtree(SAMPLE_ROOT)
        if self._has_sample_source_media():
            return

        SAMPLE_IMPORT.mkdir(parents=True, exist_ok=True)
        source = SAMPLE_IMAGE if SAMPLE_IMAGE.exists() else None
        base_time = int(time.time()) - (86400 * 90)
        for idx, (filename, minute_offset) in enumerate(SAMPLE_FILES):
            target = SAMPLE_IMPORT / filename
            if source:
                shutil.copyfile(source, target)
                with target.open("ab") as handle:
                    handle.write(f"\n# sample-file-{idx}\n".encode("ascii"))
            else:
                target.write_text(f"sample media placeholder {idx}\n", encoding="utf-8")
            ts = base_time + (minute_offset * 60)
            target.touch()
            try:
                import os

                os.utime(target, (ts, ts))
            except OSError:
                pass

    def reset_sample_library(self) -> None:
        if self.process and self.process.state() != QProcess.NotRunning:
            QMessageBox.information(self, "Run in progress", "Stop the current run before resetting the sample library.")
            return
        self._ensure_sample_library(force=True)
        self.root_path.setText(str(SAMPLE_ROOT))
        self.latest_csv_path = None
        self.open_csv_button.setEnabled(False)
        self._populate_demo_rows()
        self.sample_count_label.setText(f"{self._sample_media_count()} sample files")
        self.phase_label.setText("Ready")
        self.status_line.setText("Sample library reset. Run a dry scan to preview the organizer.")
        self._set_progress(0)

    def _build_preview_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("previewPanel")
        panel.setMinimumHeight(178)
        panel.setMaximumHeight(210)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.preview_image = QLabel()
        self.preview_image.setAlignment(Qt.AlignCenter)
        self.preview_image.setScaledContents(False)
        self.preview_image.setObjectName("previewImage")
        self.preview_image.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.preview_image, 1)

        overlay = QFrame()
        overlay.setObjectName("previewOverlay")
        overlay_layout = QHBoxLayout(overlay)
        overlay_layout.setContentsMargins(18, 14, 18, 14)
        overlay_layout.setSpacing(18)
        dest = QVBoxLayout()
        dest.setSpacing(2)
        small = QLabel("Destination")
        small.setObjectName("overlayEyebrow")
        big = QLabel("all_photos / 2024")
        big.setObjectName("overlayTitle")
        dest.addWidget(small)
        dest.addWidget(big)
        naming = QLabel("YYYYMMDD_HHMMSS.ext")
        naming.setObjectName("overlayMeta")
        overlay_layout.addLayout(dest, 1)
        overlay_layout.addWidget(naming)
        layout.addWidget(overlay)

        return panel

    def _build_options_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setMinimumHeight(172)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 16, 20, 14)
        layout.setSpacing(10)
        layout.addLayout(self._section_heading("Rules", "Organizer Options", "Save preset"))

        self.organize_year = QCheckBox("Organize into year folders")
        self.organize_year.setChecked(True)
        self.csv_log = QCheckBox("CSV log")
        self.csv_log.setChecked(True)
        self.prefer_newest = QCheckBox("Prefer newest")

        check_row = QHBoxLayout()
        check_row.setSpacing(10)
        for checkbox in (self.organize_year, self.csv_log, self.prefer_newest):
            checkbox.setObjectName("optionCheck")
            check_row.addWidget(checkbox)
        layout.addLayout(check_row)

        dup_row = QHBoxLayout()
        dup_label = QLabel("Duplicate action")
        dup_label.setObjectName("fieldLabel")
        self.dup_action = QComboBox()
        self.dup_action.addItems(["move", "skip", "delete"])
        self.dup_action.setObjectName("combo")
        dup_row.addWidget(dup_label)
        dup_row.addWidget(self.dup_action, 1)
        layout.addLayout(dup_row)
        return panel

    def _build_summary_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setMinimumHeight(172)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 16, 20, 14)
        layout.setSpacing(8)
        layout.addLayout(self._section_heading("Live Preview", "Run Summary", "ETA 00:08:12"))

        summary_grid = QGridLayout()
        summary_grid.setSpacing(8)
        for idx, (name, value) in enumerate((
            ("Moved", "15,104"),
            ("Renamed", "11,238"),
            ("Duplicates", "642"),
            ("Skipped", "119"),
        )):
            row = QFrame()
            row.setObjectName("summaryRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(12, 6, 12, 6)
            label = QLabel(name)
            label.setObjectName("summaryLabel")
            number = QLabel(value)
            number.setObjectName("summaryValue")
            row_layout.addWidget(label)
            row_layout.addStretch(1)
            row_layout.addWidget(number)
            summary_grid.addWidget(row, idx // 2, idx % 2)
        layout.addLayout(summary_grid)
        return panel

    def _build_log_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setMinimumHeight(300)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 16, 20, 14)
        layout.setSpacing(8)
        layout.addLayout(self._section_heading("CSV Log", "Recent Actions", "run.csv"))

        self.log_table = QTableWidget(0, 5)
        self.log_table.setObjectName("logTable")
        self.log_table.setHorizontalHeaderLabels(["Action", "Old path", "New path", "Source", "Size"])
        self.log_table.verticalHeader().setVisible(False)
        self.log_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.log_table.setAlternatingRowColors(True)
        self.log_table.setMinimumHeight(190)
        layout.addWidget(self.log_table)

        self.status_line = QLabel("Ready. Configure options and run a dry scan.")
        self.status_line.setObjectName("statusLine")
        layout.addWidget(self.status_line)
        return panel

    def _section_heading(self, eyebrow_text: str, title_text: str, pill_text: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)
        title_wrap = QVBoxLayout()
        title_wrap.setSpacing(2)
        eyebrow = QLabel(eyebrow_text)
        eyebrow.setObjectName("eyebrow")
        title = QLabel(title_text)
        title.setObjectName("sectionTitle")
        title_wrap.addWidget(eyebrow)
        title_wrap.addWidget(title)
        pill = QLabel(pill_text)
        pill.setObjectName("pill")
        row.addLayout(title_wrap, 1)
        row.addWidget(pill)
        return row

    def _populate_demo_rows(self) -> None:
        rows = [
            ("MOVE+RENAME", r"E:\DCIM\IMG_4382.JPG", r"all_photos\2024\20240518_143522.jpg", "exif:DateTimeOriginal", "6.8 MB"),
            ("DUP_MOVE", r"E:\Backup\IMG_4382 copy.JPG", r"all_photos\_DUPLICATES\IMG_4382 copy.jpg", "hash_match", "6.8 MB"),
            ("MOVE", r"E:\Videos\VID_1029.MOV", r"all_photos\2023\20230902_181744.mov", "QuickTime:CreateDate", "184 MB"),
            ("SKIP", r"E:\Private\scan.png", "-", "excluded folder", "2.1 MB"),
        ]
        self.log_table.setRowCount(len(rows))
        for row_idx, row in enumerate(rows):
            for col_idx, value in enumerate(row):
                item = make_table_item(value, centered=col_idx == 0)
                self.log_table.setItem(row_idx, col_idx, item)
        self.log_table.resizeColumnsToContents()

    def _load_sample_image(self) -> None:
        if not SAMPLE_IMAGE.exists():
            self.preview_image.setText("Sample gallery asset missing")
            return
        pixmap = QPixmap(str(SAMPLE_IMAGE))
        if pixmap.isNull():
            self.preview_image.setText("Unable to load sample gallery")
            return
        self._sample_pixmap = pixmap
        self._rescale_preview()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._rescale_preview()
        self._set_progress(self._current_progress if hasattr(self, "_current_progress") else 0)

    def _rescale_preview(self) -> None:
        pixmap = getattr(self, "_sample_pixmap", None)
        if not pixmap:
            return
        target = self.preview_image.size()
        if target.width() <= 0 or target.height() <= 0:
            return
        self.preview_image.setPixmap(pixmap.scaled(target, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))

    def choose_root(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose media root", self.root_path.text())
        if folder:
            self.root_path.setText(folder)

    def open_csv(self) -> None:
        if not self.latest_csv_path or not self.latest_csv_path.exists():
            QMessageBox.information(self, "CSV not ready", "Run the organizer with CSV log enabled first.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.latest_csv_path)))

    def _update_run_button_label(self) -> None:
        if self.process and self.process.state() != QProcess.NotRunning:
            self.run_button.setText("Stop run")
        else:
            self.run_button.setText("Run dry scan" if self.dry_run.isChecked() else "Run organizer")

    def run_organizer(self) -> None:
        if self.process and self.process.state() != QProcess.NotRunning:
            self.process.kill()
            return

        root = Path(self.root_path.text()).expanduser()
        if not root.exists():
            QMessageBox.warning(self, "Folder not found", f"The selected root folder does not exist:\n{root}")
            return
        if not SCRIPT.exists():
            QMessageBox.critical(self, "Organizer script missing", f"Could not find:\n{SCRIPT}")
            return

        args = [str(SCRIPT), str(root), "--dup-action", self.dup_action.currentText()]
        if self.dry_run.isChecked():
            args.append("--dry-run")
        if self.csv_log.isChecked():
            self.latest_csv_path = root / "photo_organizer_run.csv"
            args.extend(["--log-csv", str(self.latest_csv_path)])
        else:
            self.latest_csv_path = None
        if self.prefer_newest.isChecked():
            args.append("--prefer-newest")
        if self.organize_year.isChecked():
            args.append("--organize-by-year")

        self.process = QProcess(self)
        self.process.setProgram(sys.executable)
        self.process.setArguments(args)
        self.process.setWorkingDirectory(str(ROOT))
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self._process_error)

        self.log_table.setRowCount(0)
        self.open_csv_button.setEnabled(False)
        self.status_line.setText("Starting organizer...")
        self.phase_label.setText("Starting organizer")
        self._set_progress(0)
        self.run_button.setText("Stop run")
        self.process.start()

    def _read_stdout(self) -> None:
        if not self.process:
            return
        text = bytes(self.process.readAllStandardOutput()).decode(errors="replace")
        self._append_console(text)
        self._update_progress_from_text(text)

    def _read_stderr(self) -> None:
        if not self.process:
            return
        text = bytes(self.process.readAllStandardError()).decode(errors="replace")
        self._append_console(text)

    def _append_console(self, text: str) -> None:
        lines = [line.strip() for line in text.replace("\r", "\n").splitlines() if line.strip()]
        if lines:
            self.status_line.setText(lines[-1][-150:])

    def _update_progress_from_text(self, text: str) -> None:
        for match in PROGRESS_RE.finditer(text.replace("\r", "\n")):
            percent = float(match.group(3))
            self.phase_label.setText(f"Processing {match.group(1)} of {match.group(2)}")
            self._set_progress(percent)
        if "Phase A" in text:
            self.phase_label.setText("Phase A: pre-scan")
        elif "Phase B" in text:
            self.phase_label.setText("Phase B: snapshot list")
        elif "Phase C" in text:
            self.phase_label.setText("Phase C: processing")
        elif "Organize-by-year" in text:
            self.phase_label.setText("Organize-by-year")

    def _set_progress(self, percent: float) -> None:
        self._current_progress = max(0, min(100, percent))
        self.progress_label.setText(f"{self._current_progress:.0f}%")
        width = int(self.progress.width() * (self._current_progress / 100))
        self.progress_fill.setGeometry(0, 0, width, self.progress.height())

    def _process_finished(self, exit_code: int, _exit_status) -> None:
        self._update_run_button_label()
        self._load_csv_rows()
        if exit_code == 0:
            self.phase_label.setText("Complete")
            self._set_progress(100)
            self.status_line.setText("Complete. CSV log is ready." if self.latest_csv_path else "Complete.")
        else:
            self.phase_label.setText(f"Stopped with exit code {exit_code}")
            self.status_line.setText(f"Stopped with exit code {exit_code}.")

    def _process_error(self, error) -> None:
        self._update_run_button_label()
        self.status_line.setText(f"Process error: {error}")

    def _load_csv_rows(self) -> None:
        if not self.latest_csv_path or not self.latest_csv_path.exists():
            return

        self.open_csv_button.setEnabled(True)
        rows: list[list[str]] = []
        try:
            with self.latest_csv_path.open("r", newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    rows.append([
                        row.get("action", ""),
                        row.get("old_path", ""),
                        row.get("new_path", ""),
                        row.get("source", "") or row.get("note", ""),
                        self._format_size(row.get("size_bytes", "")),
                    ])
        except OSError as exc:
            self.status_line.setText(f"Could not read CSV: {exc}")
            return

        self._set_table_rows(rows[-200:] if rows else [])

    def _set_table_rows(self, rows: list[list[str]]) -> None:
        self.log_table.setRowCount(len(rows))
        for row_idx, row in enumerate(rows):
            for col_idx, value in enumerate(row):
                item = make_table_item(value, centered=col_idx == 0)
                self.log_table.setItem(row_idx, col_idx, item)

    def _format_size(self, raw_size: str) -> str:
        try:
            size = int(raw_size)
        except (TypeError, ValueError):
            return raw_size
        if size >= 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024 * 1024):.1f} GB"
        if size >= 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        if size >= 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size} B"

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow {
                background: #edf2f5;
            }
            #workspaceScroll {
                border: 0;
                background: #edf2f5;
            }
            #sidebar {
                background: rgba(255, 255, 255, 218);
                border-right: 1px solid #d9e0e5;
            }
            #workspace {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #f5f8fa, stop:1 #e8eef2);
            }
            #brandMark {
                min-width: 44px;
                min-height: 44px;
                max-width: 44px;
                max-height: 44px;
                border-radius: 8px;
                background: #172026;
                color: white;
                font-weight: 900;
            }
            #brandTitle {
                color: #172026;
                font-size: 20px;
                font-weight: 900;
            }
            #eyebrow, QLabel#eyebrow {
                color: #65717a;
                font-size: 11px;
                font-weight: 800;
                text-transform: uppercase;
            }
            #headline {
                color: #172026;
                font-size: 27px;
                font-weight: 900;
            }
            #panel, #safetyPanel {
                background: #ffffff;
                border: 1px solid #d9e0e5;
                border-radius: 8px;
            }
            #previewPanel {
                background: #111820;
                border-radius: 8px;
            }
            #previewImage {
                background: #111820;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                min-height: 132px;
                max-height: 150px;
            }
            #previewOverlay {
                background: rgba(18, 26, 34, 205);
                border-bottom-left-radius: 8px;
                border-bottom-right-radius: 8px;
            }
            #overlayEyebrow {
                color: rgba(255, 255, 255, 170);
                font-size: 11px;
                font-weight: 800;
                text-transform: uppercase;
            }
            #overlayTitle {
                color: #ffffff;
                font-size: 17px;
                font-weight: 900;
            }
            #overlayMeta {
                color: rgba(255, 255, 255, 205);
                font-size: 13px;
                font-weight: 800;
            }
            QPushButton {
                min-height: 34px;
                padding: 0 14px;
                border-radius: 8px;
                font-weight: 800;
            }
            #primaryButton, #navActive {
                background: #172026;
                color: #ffffff;
                border: 1px solid #172026;
            }
            #ghostButton, #navButton {
                background: #ffffff;
                color: #172026;
                border: 1px solid #d9e0e5;
            }
            #navButton {
                text-align: left;
                color: #65717a;
                border: 0;
                background: transparent;
            }
            #navActive {
                text-align: left;
            }
            #pill {
                background: rgba(31, 157, 104, 31);
                color: #10764b;
                border-radius: 14px;
                padding: 5px 10px;
                font-size: 12px;
                font-weight: 900;
            }
            #sectionTitle {
                color: #172026;
                font-size: 17px;
                font-weight: 900;
            }
            #fieldLabel, #panelLabel {
                color: #65717a;
                font-size: 12px;
                font-weight: 900;
            }
            #mutedText, #optionDescription, #summaryLabel {
                color: #65717a;
                font-size: 13px;
            }
            #pathInput, #combo {
                min-height: 34px;
                border: 1px solid #d9e0e5;
                border-radius: 8px;
                background: #f8fafb;
                color: #172026;
                padding: 0 12px;
                font-weight: 700;
            }
            #statCard, #optionRow, #summaryRow {
                background: #f8fafb;
                border: 1px solid #d9e0e5;
                border-radius: 8px;
            }
            #statCard {
                min-height: 46px;
                max-height: 50px;
            }
            #statValue {
                color: #172026;
                font-size: 17px;
                font-weight: 900;
            }
            #statLabel {
                color: #65717a;
                font-size: 11px;
                font-weight: 700;
            }
            #scanInfo {
                background: #f8fafb;
                border: 1px solid #d9e0e5;
                border-radius: 8px;
                color: #172026;
                font-size: 13px;
                font-weight: 900;
                padding: 7px 10px;
            }
            #scanInfoMuted {
                background: rgba(31, 157, 104, 24);
                border-radius: 8px;
                color: #10764b;
                font-size: 13px;
                font-weight: 900;
                padding: 7px 10px;
            }
            #optionCheck {
                color: #172026;
                font-size: 13px;
                font-weight: 900;
            }
            #summaryValue {
                color: #172026;
                font-size: 15px;
                font-weight: 900;
            }
            #progressTrack {
                min-height: 10px;
                max-height: 10px;
                background: #dde5ea;
                border-radius: 6px;
            }
            #progressFill {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2f80ed, stop:1 #1f9d68);
                border-radius: 6px;
            }
            #logTable {
                background: #ffffff;
                color: #172026;
                alternate-background-color: #f8fafb;
                border: 1px solid #d9e0e5;
                border-radius: 8px;
                gridline-color: #d9e0e5;
                selection-background-color: #dcecff;
                selection-color: #172026;
            }
            #logTable::item {
                color: #172026;
                padding: 6px;
            }
            #logTable::item:disabled {
                color: #172026;
            }
            QHeaderView::section {
                background: #f8fafb;
                color: #65717a;
                border: 0;
                border-bottom: 1px solid #d9e0e5;
                padding: 8px;
                font-weight: 900;
            }
            #statusLine {
                background: #111820;
                color: #dbe7ee;
                border: 0;
                border-radius: 8px;
                padding: 7px 10px;
                font-family: Consolas, monospace;
            }
            """
        )


def main() -> int:
    app = QApplication(sys.argv)
    window = PhotoOrganizerWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
