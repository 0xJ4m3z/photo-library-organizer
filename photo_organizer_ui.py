import csv
import os
import re
import shutil
import sys
import time
from pathlib import Path

from PySide6.QtCore import QProcess, Qt, QUrl
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QFont, QImage, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "bulk_image_rename.py"
SAMPLE_ROOT = ROOT / "sample_images"
SAMPLE_IMPORT = SAMPLE_ROOT / "raw_import"
TARGET_EXTS = {".jpg", ".jpeg", ".png", ".cr2", ".dng", ".mov", ".avi", ".3gp", ".gif", ".mp4"}
PROGRESS_RE = re.compile(
    r"\[PROC\]\s+([\d,]+)/([\d,]+)\s+\(\s*([\d.]+)%\).*?"
    r"moved\s+([\d,]+).*?renamed\s+([\d,]+).*?dups\s+([\d,]+).*?skipped\s+([\d,]+)",
    re.IGNORECASE,
)
ACTION_RE = re.compile(r"^(?:\[DRY\])?\[([^\]]+)\]\s+(.+?)(?:\s+->\s+(.+))?$")
SAMPLE_FILES = [
    ("beach-sunset.jpg", "Beach Sunset", "#f6b75a", "#305f8f", 0),
    ("mountain-trail.jpg", "Mountain Trail", "#8ecae6", "#2d6a4f", 37),
    ("birthday-table.jpg", "Birthday Table", "#f7cad0", "#9d4edd", 82),
    ("city-night.jpg", "City Night", "#1b263b", "#fca311", 126),
    ("dog-park.jpg", "Dog Park", "#95d5b2", "#6c584c", 173),
    ("concert-lights.jpg", "Concert Lights", "#3a0ca3", "#f72585", 218),
    ("snowy-cabin.jpg", "Snowy Cabin", "#dbeafe", "#31572c", 264),
    ("pasta-dinner.jpg", "Pasta Dinner", "#ffd166", "#bc4749", 301),
    ("lake-canoe.jpg", "Lake Canoe", "#74c0fc", "#184e77", 349),
    ("garden-flowers.jpg", "Garden Flowers", "#b7e4c7", "#d63384", 393),
    ("family-scan.jpg", "Family Scan", "#e9dcc9", "#6c584c", 438),
    ("water-dog.jpg", "Water Dog", "#90e0ef", "#0077b6", 482),
    ("desert-road.jpg", "Desert Road", "#f4a261", "#264653", 527),
    ("forest-path.jpg", "Forest Path", "#40916c", "#081c15", 571),
    ("museum-day.jpg", "Museum Day", "#dee2e6", "#495057", 616),
    ("coffee-window.jpg", "Coffee Window", "#c9ada7", "#4a4e69", 660),
    ("harbor-boats.jpg", "Harbor Boats", "#a8dadc", "#1d3557", 704),
    ("autumn-leaves.jpg", "Autumn Leaves", "#e76f51", "#6a994e", 749),
    ("market-stall.jpg", "Market Stall", "#ffbe0b", "#fb5607", 793),
    ("rainy-street.jpg", "Rainy Street", "#4a5568", "#90cdf4", 838),
]


def make_item(value: str, align_center: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem(value)
    item.setForeground(QBrush(QColor("#172026")))
    if align_center:
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    return item


def format_size(raw_size: str) -> str:
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


class PhotoOrganizerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.process: QProcess | None = None
        self.latest_csv_path: Path | None = None
        self.dest_root: Path | None = None

        self._ensure_sample_library()

        self.setWindowTitle("Photo Library Organizer")
        self.resize(1200, 760)
        self.setMinimumSize(980, 680)

        shell = QWidget()
        self.setCentralWidget(shell)
        root_layout = QHBoxLayout(shell)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        root_layout.addWidget(self._build_sidebar())
        root_layout.addWidget(self._build_pages(), 1)

        self._apply_styles()
        self._select_page(0)
        self._update_sample_count()
        self._update_folder_labels()
        self._update_command_preview()

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(240)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(14)

        brand_row = QHBoxLayout()
        mark = QLabel("PL")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setObjectName("brandMark")
        title = QLabel("Photo Library\nOrganizer")
        title.setObjectName("brandTitle")
        brand_row.addWidget(mark)
        brand_row.addWidget(title, 1)
        layout.addLayout(brand_row)

        self.nav_buttons: list[QPushButton] = []
        for idx, label in enumerate(("Run", "Settings")):
            button = QPushButton(label)
            button.setObjectName("navButton")
            button.clicked.connect(lambda _checked=False, page=idx: self._select_page(page))
            self.nav_buttons.append(button)
            layout.addWidget(button)

        layout.addStretch(1)

        sample_box = QFrame()
        sample_box.setObjectName("sideCard")
        sample_layout = QVBoxLayout(sample_box)
        sample_layout.setContentsMargins(14, 14, 14, 14)
        sample_layout.setSpacing(10)
        self.sample_count = QLabel("Sample files: -")
        self.sample_count.setObjectName("sideLabel")
        reset = QPushButton("Reset Sample")
        reset.setObjectName("ghostButton")
        reset.clicked.connect(self.reset_sample_library)
        sample_note = QLabel("Use the generated sample folder for a safe demo, or browse to your own library.")
        sample_note.setWordWrap(True)
        sample_note.setObjectName("muted")
        sample_layout.addWidget(self.sample_count)
        sample_layout.addWidget(reset)
        sample_layout.addWidget(sample_note)
        layout.addWidget(sample_box)

        return sidebar

    def _build_pages(self) -> QWidget:
        self.pages = QStackedWidget()
        self.pages.setObjectName("pages")
        self.pages.addWidget(self._build_run_page())
        self.pages.addWidget(self._build_options_page())
        return self.pages

    def _build_run_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(14)

        header = QHBoxLayout()
        product_label = QLabel("PYTHON MEDIA ORGANIZER")
        product_label.setObjectName("eyebrow")
        header.addWidget(product_label)
        header.addStretch(1)
        self.open_csv_button = QPushButton("Open CSV")
        self.open_csv_button.setObjectName("ghostButton")
        self.open_csv_button.clicked.connect(self.open_csv)
        self.open_csv_button.setEnabled(False)
        self.open_dest_button = QPushButton("Open Output")
        self.open_dest_button.setObjectName("ghostButton")
        self.open_dest_button.clicked.connect(self.open_output_folder)
        self.open_dest_button.setEnabled(False)
        self.run_button = QPushButton("Run Dry Scan")
        self.run_button.setObjectName("primaryButton")
        self.run_button.clicked.connect(self.run_organizer)
        header.addWidget(self.open_csv_button)
        header.addWidget(self.open_dest_button)
        header.addWidget(self.run_button)
        layout.addLayout(header)

        top_row = QHBoxLayout()
        top_row.setSpacing(18)

        folder_card = QFrame()
        folder_card.setObjectName("panel")
        folder_layout = QGridLayout(folder_card)
        folder_layout.setContentsMargins(18, 18, 18, 18)
        folder_layout.setHorizontalSpacing(10)
        folder_layout.setVerticalSpacing(10)

        folder_layout.addWidget(QLabel("Source folder"), 0, 0)
        self.root_path = QLineEdit(str(SAMPLE_ROOT))
        self.root_path.textChanged.connect(self._update_command_preview)
        self.root_path.textChanged.connect(self._update_folder_labels)
        browse = QPushButton("Browse")
        browse.setObjectName("ghostButton")
        browse.clicked.connect(self.choose_root)
        folder_layout.addWidget(self.root_path, 1, 0)
        folder_layout.addWidget(browse, 1, 1)

        folder_layout.addWidget(QLabel("Destination folder"), 2, 0)
        self.destination_label = QLabel()
        self.destination_label.setObjectName("pathLabel")
        folder_layout.addWidget(self.destination_label, 3, 0, 1, 2)

        self.dry_run = QCheckBox("Dry run first")
        self.dry_run.setChecked(True)
        self.dry_run.stateChanged.connect(lambda _state: self._update_run_label())
        self.dry_run.stateChanged.connect(lambda _state: self._update_command_preview())
        self.csv_log = QCheckBox("Write CSV log")
        self.csv_log.setChecked(True)
        self.csv_log.stateChanged.connect(lambda _state: self._update_command_preview())
        folder_layout.addWidget(self.dry_run, 4, 0)
        folder_layout.addWidget(self.csv_log, 4, 1)
        top_row.addWidget(folder_card, 3)

        preview_card = QFrame()
        preview_card.setObjectName("panel")
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(16, 14, 16, 14)
        preview_layout.setSpacing(8)
        preview_title = QLabel("Current image")
        preview_title.setObjectName("sectionTitle")
        self.preview = QLabel("Waiting for run")
        self.preview.setObjectName("preview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(220, 108)
        self.preview.setMaximumHeight(126)
        self.current_file_label = QLabel("No file yet.")
        self.current_file_label.setObjectName("muted")
        self.current_file_label.setWordWrap(True)
        preview_layout.addWidget(preview_title)
        preview_layout.addWidget(self.preview)
        preview_layout.addWidget(self.current_file_label)
        top_row.addWidget(preview_card, 2)
        layout.addLayout(top_row)

        progress_card = QFrame()
        progress_card.setObjectName("panel")
        progress_layout = QVBoxLayout(progress_card)
        progress_layout.setContentsMargins(18, 14, 18, 14)
        progress_layout.setSpacing(8)
        self.status_label = QLabel("Ready.")
        self.status_label.setObjectName("statusLabel")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.stats_label = QLabel("Moved 0 | Renamed 0 | Duplicates 0 | Skipped 0")
        self.stats_label.setObjectName("muted")
        progress_layout.addWidget(self.status_label)
        progress_layout.addWidget(self.progress)
        progress_layout.addWidget(self.stats_label)
        layout.addWidget(progress_card)

        actions_card = QFrame()
        actions_card.setObjectName("panel")
        actions_card.setMinimumHeight(280)
        actions_layout = QVBoxLayout(actions_card)
        actions_layout.setContentsMargins(18, 14, 18, 14)
        actions_layout.setSpacing(8)
        actions_label = QLabel("Actions")
        actions_label.setObjectName("sectionTitle")
        self.results_table = QTableWidget(0, 5)
        self.results_table.setObjectName("resultsTable")
        self.results_table.setHorizontalHeaderLabels(["Action", "From", "To", "Source / Note", "Size"])
        self.results_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setAlternatingRowColors(True)
        self.results_table.setMinimumHeight(210)
        self.results_table.verticalHeader().setDefaultSectionSize(28)
        self.results_status = QLabel("No run loaded yet.")
        self.results_status.setObjectName("muted")
        actions_layout.addWidget(actions_label)
        actions_layout.addWidget(self.results_table, 1)
        actions_layout.addWidget(self.results_status)
        layout.addWidget(actions_card, 1)

        return page

    def _build_options_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 28, 30, 28)
        layout.setSpacing(18)

        title = QLabel("Settings")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        options = QFrame()
        options.setObjectName("panel")
        grid = QGridLayout(options)
        grid.setContentsMargins(18, 18, 18, 18)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(14)

        self.dest_name = QLineEdit("all_photos")
        self.dest_name.textChanged.connect(self._update_command_preview)
        self.dest_name.textChanged.connect(self._update_folder_labels)
        self.dup_action = QComboBox()
        self.dup_action.addItems(["move", "skip", "delete"])
        self.dup_action.currentTextChanged.connect(self._update_command_preview)
        self.organize_year = QCheckBox("Organize destination into year folders")
        self.organize_year.setChecked(True)
        self.organize_year.stateChanged.connect(lambda _state: self._update_command_preview())
        self.prefer_newest = QCheckBox("Prefer newest timestamp")
        self.prefer_newest.stateChanged.connect(lambda _state: self._update_command_preview())
        self.no_exiftool = QCheckBox("Skip ExifTool and use file modified time")
        self.no_exiftool.stateChanged.connect(lambda _state: self._update_command_preview())
        self.exiftool = QLineEdit("exiftool")
        self.exiftool.textChanged.connect(self._update_command_preview)
        self.exif_timeout = QSpinBox()
        self.exif_timeout.setRange(1, 120)
        self.exif_timeout.setValue(10)
        self.exif_timeout.valueChanged.connect(self._update_command_preview)
        self.hash_max = QSpinBox()
        self.hash_max.setRange(1, 100000)
        self.hash_max.setValue(512)
        self.hash_max.setSuffix(" MB")
        self.hash_max.valueChanged.connect(self._update_command_preview)
        self.exclude_paths = QPlainTextEdit()
        self.exclude_paths.setPlaceholderText("One folder per line, relative to root or absolute path")
        self.exclude_paths.setMaximumHeight(90)
        self.exclude_paths.textChanged.connect(self._update_command_preview)

        grid.addWidget(QLabel("Destination folder name"), 0, 0)
        grid.addWidget(self.dest_name, 0, 1)
        grid.addWidget(QLabel("Duplicate action"), 1, 0)
        grid.addWidget(self.dup_action, 1, 1)
        grid.addWidget(QLabel("ExifTool command/path"), 2, 0)
        grid.addWidget(self.exiftool, 2, 1)
        grid.addWidget(QLabel("ExifTool timeout"), 3, 0)
        grid.addWidget(self.exif_timeout, 3, 1)
        grid.addWidget(QLabel("Hash duplicates up to"), 4, 0)
        grid.addWidget(self.hash_max, 4, 1)
        grid.addWidget(self.organize_year, 5, 0, 1, 2)
        grid.addWidget(self.prefer_newest, 6, 0, 1, 2)
        grid.addWidget(self.no_exiftool, 7, 0, 1, 2)
        grid.addWidget(QLabel("Exclude folders"), 8, 0)
        grid.addWidget(self.exclude_paths, 8, 1)
        layout.addWidget(options)

        note = QLabel(
            "The UI calls bulk_image_rename.py with these options. Dry runs do not move files; real runs move "
            "media into the destination folder and write duplicates according to the selected action."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        return page

    def _select_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        for button_index, button in enumerate(self.nav_buttons):
            button.setObjectName("navActive" if button_index == index else "navButton")
            button.style().unpolish(button)
            button.style().polish(button)

    def choose_root(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose media root", self.root_path.text())
        if folder:
            self.root_path.setText(folder)
            self._update_sample_count()
            self._update_folder_labels()

    def reset_sample_library(self) -> None:
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.information(self, "Run in progress", "Stop the current run before resetting the sample library.")
            return
        self._ensure_sample_library(force=True)
        self.root_path.setText(str(SAMPLE_ROOT))
        self.latest_csv_path = None
        self.dest_root = None
        self.open_csv_button.setEnabled(False)
        self.open_dest_button.setEnabled(False)
        self.results_table.setRowCount(0)
        self.results_status.setText("Sample library reset.")
        self.status_label.setText("Sample library reset. Ready for a dry scan.")
        self.preview.setText("Waiting for run")
        self.preview.setPixmap(QPixmap())
        self.current_file_label.setText("No file yet.")
        self.progress.setValue(0)
        self.stats_label.setText("Moved 0 | Renamed 0 | Duplicates 0 | Skipped 0")
        self._update_sample_count()
        self._update_command_preview()

    def run_organizer(self) -> None:
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
            self.status_label.setText("Stopping...")
            return

        root = Path(self.root_path.text()).expanduser()
        if not root.exists():
            QMessageBox.warning(self, "Folder not found", f"The selected root folder does not exist:\n{root}")
            return
        if not SCRIPT.exists():
            QMessageBox.critical(self, "Organizer missing", f"Could not find:\n{SCRIPT}")
            return

        self.latest_csv_path = root / "photo_organizer_run.csv" if self.csv_log.isChecked() else None
        self.dest_root = root / self.dest_name.text().strip()

        self.process = QProcess(self)
        self.process.setProgram(sys.executable)
        self.process.setArguments(self._build_args(root))
        self.process.setWorkingDirectory(str(ROOT))
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self._process_error)

        self.results_table.setRowCount(0)
        self.open_csv_button.setEnabled(False)
        self.open_dest_button.setEnabled(False)
        self.preview.setText("Waiting for first file")
        self.preview.setPixmap(QPixmap())
        self.current_file_label.setText("No file yet.")
        self.progress.setValue(0)
        self.status_label.setText("Starting...")
        self.stats_label.setText("Moved 0 | Renamed 0 | Duplicates 0 | Skipped 0")
        self._update_run_label()
        self.process.start()

    def _build_args(self, root: Path) -> list[str]:
        args = [str(SCRIPT), str(root)]
        if self.dest_name.text().strip():
            args.extend(["--dest-name", self.dest_name.text().strip()])
        if self.exiftool.text().strip():
            args.extend(["--exiftool", self.exiftool.text().strip()])
        if self.no_exiftool.isChecked():
            args.append("--no-exiftool")
        args.extend(["--exiftool-timeout", str(self.exif_timeout.value())])
        if self.dry_run.isChecked():
            args.append("--dry-run")
        if self.latest_csv_path:
            args.extend(["--log-csv", str(self.latest_csv_path)])
        if self.prefer_newest.isChecked():
            args.append("--prefer-newest")
        args.extend(["--dup-action", self.dup_action.currentText()])
        args.extend(["--hash-max-mb", str(self.hash_max.value())])
        for line in self.exclude_paths.toPlainText().splitlines():
            exclude = line.strip()
            if exclude:
                args.extend(["--exclude", exclude])
        if self.organize_year.isChecked():
            args.append("--organize-by-year")
        return args

    def _update_command_preview(self) -> None:
        if not hasattr(self, "command_preview"):
            return
        root = Path(self.root_path.text()).expanduser() if self.root_path.text() else SAMPLE_ROOT
        parts = [sys.executable] + self._build_args(root)
        self.command_preview.setPlainText(" ".join(f'"{part}"' if " " in part else part for part in parts))

    def _update_folder_labels(self) -> None:
        if not hasattr(self, "destination_label"):
            return
        root = Path(self.root_path.text()).expanduser() if self.root_path.text() else SAMPLE_ROOT
        destination = root / self.dest_name.text().strip()
        self.destination_label.setText(str(destination))

    def _update_run_label(self) -> None:
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            self.run_button.setText("Stop")
        else:
            self.run_button.setText("Run Dry Scan" if self.dry_run.isChecked() else "Run Organizer")

    def _read_stdout(self) -> None:
        if not self.process:
            return
        text = bytes(self.process.readAllStandardOutput()).decode(errors="replace")
        self._update_progress(text)
        self._update_actions(text)

    def _read_stderr(self) -> None:
        if not self.process:
            return
        text = bytes(self.process.readAllStandardError()).decode(errors="replace")
        if text.strip():
            self.results_status.setText(text.strip().splitlines()[-1])

    def _update_progress(self, text: str) -> None:
        normalized = text.replace("\r", "\n")
        for match in PROGRESS_RE.finditer(normalized):
            percent = float(match.group(3))
            moved, renamed, dups, skipped = (match.group(i).replace(",", "") for i in range(4, 8))
            self.progress.setValue(int(percent))
            self.status_label.setText(f"Processing {match.group(1)} of {match.group(2)} files")
            self.stats_label.setText(f"Moved {moved} | Renamed {renamed} | Duplicates {dups} | Skipped {skipped}")
        if "Phase A" in text:
            self.status_label.setText("Phase A: counting media files")
        elif "Phase B" in text:
            self.status_label.setText("Phase B: building file list")
        elif "Phase C" in text:
            self.status_label.setText("Phase C: processing files")

    def _update_actions(self, text: str) -> None:
        for line in text.replace("\r", "\n").splitlines():
            match = ACTION_RE.match(line.strip())
            if not match:
                continue
            action, source, destination = match.group(1), match.group(2), match.group(3) or ""
            source_path = Path(source)
            destination_path = Path(destination) if destination else None
            preview_path = source_path if source_path.exists() else destination_path
            if preview_path:
                self._show_current_media(preview_path)
            self._append_action_row([action, source, destination, "", ""])

    def _append_action_row(self, row: list[str]) -> None:
        row_idx = self.results_table.rowCount()
        self.results_table.insertRow(row_idx)
        for col_idx, value in enumerate(row):
            self.results_table.setItem(row_idx, col_idx, make_item(value, align_center=col_idx == 0))
        self.results_table.scrollToBottom()

    def _show_current_media(self, path: Path) -> None:
        self.current_file_label.setText(str(path))
        if not path.exists() or path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".gif"}:
            self.preview.setPixmap(QPixmap())
            self.preview.setText(path.name)
            return
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.preview.setPixmap(QPixmap())
            self.preview.setText(path.name)
            return
        self.preview.setText("")
        self.preview.setPixmap(
            pixmap.scaled(
                self.preview.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _process_finished(self, exit_code: int, _exit_status) -> None:
        self._update_run_label()
        if exit_code == 0:
            self.progress.setValue(100)
            self.status_label.setText("Complete.")
        else:
            self.status_label.setText(f"Stopped with exit code {exit_code}. See output for details.")
        self._load_csv_results()
        root = Path(self.root_path.text()).expanduser()
        if (self.dest_root and self.dest_root.exists()) or root.exists():
            self.open_dest_button.setEnabled(True)

    def _process_error(self, error) -> None:
        self._update_run_label()
        self.status_label.setText(f"Process error: {error}")

    def _load_csv_results(self) -> None:
        if not self.latest_csv_path or not self.latest_csv_path.exists():
            self.results_status.setText("No CSV log was written.")
            return

        rows: list[list[str]] = []
        with self.latest_csv_path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                rows.append([
                    row.get("action", ""),
                    row.get("old_path", ""),
                    row.get("new_path", ""),
                    row.get("source", "") or row.get("note", ""),
                    format_size(row.get("size_bytes", "")),
                ])

        self.results_table.setRowCount(len(rows))
        for row_idx, row in enumerate(rows):
            for col_idx, value in enumerate(row):
                self.results_table.setItem(row_idx, col_idx, make_item(value, align_center=col_idx == 0))
        self.results_status.setText(f"Loaded {len(rows)} CSV rows from {self.latest_csv_path}")
        self.open_csv_button.setEnabled(True)

    def open_csv(self) -> None:
        if not self.latest_csv_path or not self.latest_csv_path.exists():
            QMessageBox.information(self, "CSV not ready", "Run with CSV logging enabled first.")
            return
        self._open_path(self.latest_csv_path)

    def open_output_folder(self) -> None:
        if self.dest_root and self.dest_root.exists():
            self._open_path(self.dest_root)
            return

        root = Path(self.root_path.text()).expanduser()
        if root.exists():
            QMessageBox.information(
                self,
                "Output not created yet",
                "This was likely a dry run, so the output folder was not created. Opening the source folder instead.",
            )
            self._open_path(root)
            return

        QMessageBox.information(self, "Output not ready", "Run the organizer first.")

    def _open_path(self, path: Path) -> None:
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(path))  # type: ignore[attr-defined]
                return
        except OSError as exc:
            QMessageBox.warning(self, "Could not open path", str(exc))
            return

        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            QMessageBox.warning(self, "Could not open path", str(path))

    def _sample_media_count(self) -> int:
        if not SAMPLE_ROOT.exists():
            return 0
        return sum(
            1
            for path in SAMPLE_ROOT.rglob("*")
            if "all_photos" not in path.parts and path.is_file() and path.suffix.lower() in TARGET_EXTS
        )

    def _update_sample_count(self) -> None:
        self.sample_count.setText(f"Sample files: {self._sample_media_count()}")

    def _has_sample_source_media(self) -> bool:
        return self._sample_media_count() > 0

    def _ensure_sample_library(self, force: bool = False) -> None:
        if force and SAMPLE_ROOT.exists():
            shutil.rmtree(SAMPLE_ROOT)
        if self._has_sample_source_media():
            return

        SAMPLE_IMPORT.mkdir(parents=True, exist_ok=True)
        base_time = int(time.time()) - (86400 * 120)

        for idx, (filename, title, color_a, color_b, minute_offset) in enumerate(SAMPLE_FILES):
            target = SAMPLE_IMPORT / filename
            self._write_sample_image(target, title, color_a, color_b, idx)
            ts = base_time + (minute_offset * 60)
            os.utime(target, (ts, ts))

    def _write_sample_image(self, target: Path, title: str, color_a: str, color_b: str, idx: int) -> None:
        width = 960 + (idx % 5) * 41
        height = 640 + (idx % 4) * 37
        image = QImage(width, height, QImage.Format.Format_RGB32)

        painter = QPainter(image)
        gradient = QLinearGradient(0, 0, width, height)
        gradient.setColorAt(0, QColor(color_a))
        gradient.setColorAt(1, QColor(color_b))
        painter.fillRect(0, 0, width, height, gradient)

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for shape_idx in range(7 + idx):
            x = (shape_idx * 97 + idx * 31) % width
            y = (shape_idx * 61 + idx * 47) % height
            size = 42 + ((shape_idx + idx) % 6) * 18
            color = QColor(255, 255, 255, 34 + (shape_idx % 5) * 18)
            painter.setBrush(QBrush(color))
            painter.setPen(Qt.PenStyle.NoPen)
            if shape_idx % 2:
                painter.drawEllipse(x, y, size, size)
            else:
                painter.drawRoundedRect(x, y, size + 38, size, 16, 16)

        painter.setPen(QColor("#ffffff"))
        font = QFont("Arial", 42)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(38, height - 86, title)

        painter.setPen(QColor(255, 255, 255, 190))
        painter.setFont(QFont("Arial", 18))
        painter.drawText(42, height - 48, f"Sample media {idx + 1:02d}")
        painter.end()

        quality = 72 + (idx % 9) * 3
        image.save(str(target), "JPEG", quality)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow {
                background: #edf2f5;
            }
            #sidebar {
                background: #ffffff;
                border-right: 1px solid #d9e0e5;
            }
            #pages {
                background: #edf2f5;
            }
            #brandMark {
                min-width: 44px;
                min-height: 44px;
                max-width: 44px;
                max-height: 44px;
                border-radius: 8px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #2f80ed, stop:1 #1f9d68);
                color: #ffffff;
                font-weight: 900;
            }
            #brandTitle {
                color: #172026;
                font-size: 18px;
                font-weight: 900;
            }
            #navButton, #navActive, #primaryButton, #ghostButton {
                min-height: 38px;
                border-radius: 8px;
                padding: 0 14px;
                font-weight: 800;
            }
            #navButton {
                text-align: left;
                border: 0;
                background: transparent;
                color: #65717a;
            }
            #navActive {
                text-align: left;
                border: 1px solid #2185d0;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2f80ed, stop:1 #1f9d68);
                color: #ffffff;
            }
            #primaryButton {
                text-align: center;
                border: 1px solid #2185d0;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2f80ed, stop:1 #1f9d68);
                color: #ffffff;
            }
            #ghostButton {
                border: 1px solid #d9e0e5;
                background: #ffffff;
                color: #172026;
            }
            #sideCard, #panel {
                background: #ffffff;
                border: 1px solid #d9e0e5;
                border-radius: 8px;
            }
            #sideLabel, #sectionTitle, #statusLabel {
                color: #172026;
                font-weight: 900;
            }
            #pathLabel {
                background: #f8fafb;
                border: 1px solid #d9e0e5;
                border-radius: 8px;
                color: #172026;
                font-weight: 800;
                padding: 9px;
            }
            #preview {
                background: #f8fafb;
                border: 1px solid #d9e0e5;
                border-radius: 8px;
                color: #65717a;
                font-weight: 800;
            }
            #eyebrow {
                color: #65717a;
                font-size: 11px;
                font-weight: 800;
                text-transform: uppercase;
            }
            #pageTitle {
                color: #172026;
                font-size: 28px;
                font-weight: 900;
            }
            QLabel {
                color: #65717a;
                font-weight: 700;
            }
            QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {
                border: 1px solid #d9e0e5;
                border-radius: 8px;
                background: #f8fafb;
                color: #172026;
                padding: 8px;
                font-weight: 700;
            }
            QCheckBox {
                color: #172026;
                font-weight: 800;
            }
            QProgressBar {
                min-height: 14px;
                border: 0;
                border-radius: 7px;
                background: #dce5eb;
                color: transparent;
            }
            QProgressBar::chunk {
                border-radius: 7px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2f80ed, stop:1 #1f9d68);
            }
            #console, #commandPreview {
                background: #111820;
                color: #dbe7ee;
                border: 0;
                font-family: Consolas, monospace;
                font-weight: 500;
            }
            #resultsTable {
                background: #ffffff;
                color: #172026;
                alternate-background-color: #f8fafb;
                border: 1px solid #d9e0e5;
                border-radius: 8px;
                gridline-color: #d9e0e5;
                selection-background-color: #dcecff;
                selection-color: #172026;
            }
            #resultsTable::item {
                color: #172026;
                padding: 6px;
            }
            QHeaderView::section {
                background: #f8fafb;
                color: #65717a;
                border: 0;
                border-bottom: 1px solid #d9e0e5;
                padding: 8px;
                font-weight: 900;
            }
            #muted {
                color: #65717a;
                font-weight: 600;
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
