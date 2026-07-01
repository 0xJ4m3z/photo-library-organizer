import re
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, Qt
from PySide6.QtGui import QPixmap, QTextCursor
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
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "bulk_image_rename.py"
SAMPLE_IMAGE = ROOT / "assets" / "gallery-contact-sheet.png"
PROGRESS_RE = re.compile(r"\[PROC\]\s+([\d,]+)/([\d,]+)\s+\(\s*([\d.]+)%\)")


class StatCard(QFrame):
    def __init__(self, value: str, label: str) -> None:
        super().__init__()
        self.setObjectName("statCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)

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


class PhotoOrganizerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.process: QProcess | None = None
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
        note = QLabel("Preview moves, renames, duplicate actions, and CSV output before touching the library.")
        note.setWordWrap(True)
        note.setObjectName("mutedText")
        safety_layout.addWidget(safety_label)
        safety_layout.addWidget(self.dry_run)
        safety_layout.addWidget(note)
        layout.addWidget(safety)

        return sidebar

    def _build_workspace(self) -> QWidget:
        workspace = QFrame()
        workspace.setObjectName("workspace")
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(28, 24, 18, 18)
        layout.setSpacing(12)

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
        self.run_button = QPushButton("Run dry scan")
        self.run_button.setObjectName("primaryButton")
        self.run_button.clicked.connect(self.run_organizer)
        header.addWidget(self.open_csv_button)
        header.addWidget(self.run_button)
        layout.addLayout(header)

        top_grid = QGridLayout()
        top_grid.setSpacing(18)
        top_grid.addWidget(self._build_scan_panel(), 0, 0)
        top_grid.addWidget(self._build_preview_panel(), 0, 1)
        top_grid.setColumnStretch(0, 9)
        top_grid.setColumnStretch(1, 11)
        layout.addLayout(top_grid, 0)

        lower_grid = QGridLayout()
        lower_grid.setSpacing(18)
        lower_grid.addWidget(self._build_options_panel(), 0, 0)
        lower_grid.addWidget(self._build_summary_panel(), 0, 1)
        lower_grid.setColumnStretch(0, 10)
        lower_grid.setColumnStretch(1, 8)
        layout.addLayout(lower_grid, 0)

        layout.addWidget(self._build_log_panel(), 1)
        return workspace

    def _build_scan_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setMinimumHeight(160)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        layout.addLayout(self._section_heading("Source", "Library Scan", "ExifTool ready"))

        path_label = QLabel("Root folder")
        path_label.setObjectName("fieldLabel")
        layout.addWidget(path_label)

        path_row = QHBoxLayout()
        self.root_path = QLineEdit(str(Path.home() / "Pictures"))
        self.root_path.setObjectName("pathInput")
        browse = QPushButton("Browse")
        browse.setObjectName("ghostButton")
        browse.clicked.connect(self.choose_root)
        path_row.addWidget(self.root_path, 1)
        path_row.addWidget(browse)
        layout.addLayout(path_row)

        stats = QHBoxLayout()
        stats.setSpacing(8)
        for value, label in (
            ("18,426", "files"),
            ("12", "formats"),
            ("384 GB", "queued"),
            ("94.8%", "timestamps"),
        ):
            stats.addWidget(StatCard(value, label))
        layout.addLayout(stats)

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

    def _build_preview_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("previewPanel")
        panel.setMinimumHeight(160)
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
        panel.setMinimumHeight(152)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 16, 20, 14)
        layout.setSpacing(10)
        layout.addLayout(self._section_heading("Rules", "Organizer Options", "Save preset"))

        self.organize_year = QCheckBox("Organize into year folders")
        self.organize_year.setChecked(True)
        self.csv_log = QCheckBox("CSV log")
        self.csv_log.setChecked(True)
        self.prefer_newest = QCheckBox("Prefer newest timestamp")

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
        panel.setMinimumHeight(152)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 16, 20, 14)
        layout.setSpacing(8)
        layout.addLayout(self._section_heading("Live Preview", "Run Summary", "ETA 00:08:12"))

        for name, value in (
            ("Moved", "15,104"),
            ("Renamed", "11,238"),
            ("Duplicates", "642"),
            ("Skipped", "119"),
        ):
            row = QFrame()
            row.setObjectName("summaryRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(14, 7, 14, 7)
            label = QLabel(name)
            label.setObjectName("summaryLabel")
            number = QLabel(value)
            number.setObjectName("summaryValue")
            row_layout.addWidget(label)
            row_layout.addStretch(1)
            row_layout.addWidget(number)
            layout.addWidget(row)
        return panel

    def _build_log_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setMinimumHeight(174)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 16, 20, 14)
        layout.setSpacing(8)
        layout.addLayout(self._section_heading("CSV Log", "Recent Actions", "run.csv"))

        self.log_table = QTableWidget(0, 5)
        self.log_table.setObjectName("logTable")
        self.log_table.setHorizontalHeaderLabels(["Action", "Old path", "New path", "Source", "Size"])
        self.log_table.verticalHeader().setVisible(False)
        self.log_table.horizontalHeader().setStretchLastSection(True)
        self.log_table.setAlternatingRowColors(True)
        layout.addWidget(self.log_table)

        self.console = QPlainTextEdit()
        self.console.setObjectName("console")
        self.console.setReadOnly(True)
        self.console.setMaximumHeight(54)
        self.console.setPlainText("Ready. Configure options and run a dry scan.")
        layout.addWidget(self.console)
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
                item = QTableWidgetItem(value)
                if col_idx == 0:
                    item.setTextAlignment(Qt.AlignCenter)
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

    def run_organizer(self) -> None:
        if self.process and self.process.state() != QProcess.NotRunning:
            self.process.kill()
            return

        root = Path(self.root_path.text()).expanduser()
        if not root.exists():
            QMessageBox.warning(self, "Folder not found", f"The selected root folder does not exist:\n{root}")
            return

        args = [str(SCRIPT), str(root), "--dup-action", self.dup_action.currentText()]
        if self.dry_run.isChecked():
            args.append("--dry-run")
        if self.csv_log.isChecked():
            args.extend(["--log-csv", str(root / "photo_organizer_run.csv")])
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

        self.console.clear()
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
        self.console.moveCursor(QTextCursor.MoveOperation.End)
        self.console.insertPlainText(text)
        self.console.ensureCursorVisible()

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
        self.run_button.setText("Run dry scan" if self.dry_run.isChecked() else "Run organizer")
        if exit_code == 0:
            self.phase_label.setText("Complete")
            self._set_progress(100)
        else:
            self.phase_label.setText(f"Stopped with exit code {exit_code}")

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow {
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
                font-size: 28px;
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
                alternate-background-color: #f8fafb;
                border: 1px solid #d9e0e5;
                border-radius: 8px;
                gridline-color: #d9e0e5;
                selection-background-color: #dcecff;
            }
            QHeaderView::section {
                background: #f8fafb;
                color: #65717a;
                border: 0;
                border-bottom: 1px solid #d9e0e5;
                padding: 8px;
                font-weight: 900;
            }
            #console {
                background: #111820;
                color: #dbe7ee;
                border: 0;
                border-radius: 8px;
                padding: 10px;
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
