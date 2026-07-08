import csv
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QPointF, QProcess, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDesktopServices,
    QIcon,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
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
    QScrollArea,
    QSizePolicy,
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
SAMPLE_SOURCE = SAMPLE_ROOT / "sample-pngs"
SAMPLE_DISPLAY = str(Path("sample_images") / "sample-pngs")
TARGET_EXTS = {".jpg", ".jpeg", ".png", ".cr2", ".dng", ".mov", ".avi", ".3gp", ".gif", ".mp4"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif"}
APP_VERSION = "1.0.0"

PROGRESS_RE = re.compile(
    r"\[PROC\]\s+([\d,]+)/([\d,]+)\s+\(\s*([\d.]+)%\).*?"
    r"moved\s+([\d,]+).*?renamed\s+([\d,]+).*?dups\s+([\d,]+).*?skipped\s+([\d,]+)",
    re.IGNORECASE,
)
FOUND_RE = re.compile(r"Phase A complete\.\s*Found\s+([\d,]+)\s+media files", re.IGNORECASE)
ACTION_RE = re.compile(r"^(?:\[DRY\])?\[([^\]]+)\]\s+(.+?)(?:\s+->\s+(.+))?$")
ACTION_NAMES = {"MOVE", "MOVE+RENAME", "DUP-MOVE", "DUP_MOVE", "DUP-SKIP", "DUP-DEL", "DUP_DELETE", "SKIP"}

# Dark neon-gradient palette shared by every widget builder / stylesheet block below.
BG = "#0b111c"
CARD = "#141b2a"
CARD_ALT = "#111827"
BORDER = "#293241"
BORDER_SOFT = "#30384a"
TEXT_PRIMARY = "#f4f7fb"
TEXT_MUTED = "#9aa8ba"
ACCENT_PINK = "#f725d9"
ACCENT_PURPLE = "#8b5cf6"
ACCENT_ORANGE = "#ff6b35"
ACCENT_GREEN = "#39d98a"
GRADIENT_CSS = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #d946ef, stop:0.5 #ec4899, stop:1 #f97316)"

# Sidebar-specific palette (kept separate so sidebar polish never touches dashboard styling).
SIDEBAR_GRADIENT_CSS = "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #08111f, stop:1 #050b14)"
SIDEBAR_BORDER = "rgba(31, 41, 55, 0.7)"
NAV_ACTIVE_GRADIENT_CSS = (
    "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #c026d3, stop:0.55 #ec4899, stop:1 #ff6b35)"
)
LOGO_GRADIENT_CSS = f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {ACCENT_PINK}, stop:1 {ACCENT_ORANGE})"
NAV_TEXT_MUTED = "#a6b0c0"
NAV_ICON_MUTED = "#8793a5"
NAV_GLOW_COLOR = "#ec4899"
SAMPLE_BORDER_SOFT = "rgba(247, 37, 217, 0.35)"


def make_item(value: str, align_center: bool = False, color: str = TEXT_PRIMARY) -> QTableWidgetItem:
    item = QTableWidgetItem(value)
    item.setForeground(QBrush(QColor(color)))
    if align_center:
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    return item


def format_size(raw_size: str) -> str:
    try:
        size = int(raw_size)
    except (TypeError, ValueError):
        return raw_size or "—"
    if size >= 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024 * 1024):.1f} GB"
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def format_action(action: str) -> str:
    return action.replace("+", " + ").replace("_", " ")


def format_timestamp(raw: str) -> str:
    if not raw:
        return "—"
    try:
        stamp = datetime.fromisoformat(raw.strip())
    except ValueError:
        return raw
    return stamp.strftime("%b %d, %Y %I:%M %p")


def rounded_pixmap(pixmap: QPixmap, radius: int) -> QPixmap:
    if pixmap.isNull():
        return pixmap
    result = QPixmap(pixmap.size())
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, pixmap.width(), pixmap.height(), radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    return result


def draw_nav_icon(kind: str, color: str, size: int = 20) -> QPixmap:
    """Paint a small line-style icon so sidebar glyphs never depend on font/emoji fallback."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(max(1.4, size * 0.09))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    m = size * 0.18
    w = size - 2 * m

    if kind == "run":
        painter.setBrush(QColor(color))
        painter.setPen(Qt.PenStyle.NoPen)
        triangle = QPolygonF(
            [
                QPointF(m + w * 0.12, m * 0.6),
                QPointF(m + w * 0.12, size - m * 0.6),
                QPointF(size - m * 0.4, size / 2),
            ]
        )
        painter.drawPolygon(triangle)
    elif kind == "output":
        path = QPainterPath()
        tab_w = w * 0.45
        tab_h = w * 0.18
        path.moveTo(m, m + tab_h)
        path.lineTo(m + tab_w, m + tab_h)
        path.lineTo(m + tab_w + tab_h, m)
        path.lineTo(size - m, m)
        path.lineTo(size - m, size - m)
        path.lineTo(m, size - m)
        path.closeSubpath()
        painter.drawPath(path)
    elif kind == "report":
        painter.drawRoundedRect(QRectF(m, m, w, size - 2 * m), 2.5, 2.5)
        inner_left = m + w * 0.22
        inner_right = size - m - w * 0.22
        for fraction in (0.38, 0.58, 0.78):
            y = m + (size - 2 * m) * fraction
            painter.drawLine(QPointF(inner_left, y), QPointF(inner_right, y))
    elif kind == "settings":
        center = QPointF(size / 2, size / 2)
        radius = w * 0.28
        painter.drawEllipse(center, radius, radius)
        tooth_len = w * 0.16
        for i in range(8):
            painter.save()
            painter.translate(center)
            painter.rotate(i * 45)
            painter.drawLine(QPointF(0, -radius - 1), QPointF(0, -radius - 1 - tooth_len))
            painter.restore()
    elif kind == "sample":
        band_h = (size - 2 * m - 4) / 3
        y = m
        for _ in range(3):
            painter.drawRoundedRect(QRectF(m, y, w, band_h), 1.5, 1.5)
            y += band_h + 2
    elif kind == "found":
        painter.drawRoundedRect(QRectF(m, m, w, size - 2 * m), 2.5, 2.5)
        painter.setBrush(QColor(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(m + w * 0.28, m + w * 0.28), w * 0.11, w * 0.11)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        mountain = QPolygonF(
            [
                QPointF(m + w * 0.08, size - m - w * 0.1),
                QPointF(m + w * 0.36, size - m - w * 0.42),
                QPointF(m + w * 0.56, size - m - w * 0.22),
                QPointF(m + w * 0.76, size - m - w * 0.5),
                QPointF(size - m - w * 0.08, size - m - w * 0.1),
            ]
        )
        painter.drawPolyline(mountain)
    elif kind == "renamed":
        painter.setBrush(QColor(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.save()
        painter.translate(size / 2, size / 2)
        painter.rotate(-45)
        body_len = w * 0.7
        body_w = w * 0.2
        painter.drawRoundedRect(QRectF(-body_len / 2, -body_w / 2, body_len * 0.7, body_w), 1.5, 1.5)
        tip = QPolygonF(
            [
                QPointF(body_len * 0.2, -body_w / 2),
                QPointF(body_len * 0.2, body_w / 2),
                QPointF(body_len / 2, 0),
            ]
        )
        painter.drawPolygon(tip)
        painter.restore()
    elif kind == "duplicates":
        back = w * 0.66
        offset = w * 0.2
        painter.drawRoundedRect(QRectF(m, m, back, back), 2.5, 2.5)
        painter.drawRoundedRect(QRectF(m + offset, m + offset, back, back), 2.5, 2.5)
    elif kind == "skipped":
        inset = w * 0.16
        painter.drawLine(QPointF(m + inset, m + inset), QPointF(size - m - inset, size - m - inset))
        painter.drawLine(QPointF(size - m - inset, m + inset), QPointF(m + inset, size - m - inset))

    painter.end()
    return pixmap


def checkmark_asset_path() -> str:
    """Render a white checkmark to a temp PNG for use as a QCheckBox::indicator image.

    Qt Style Sheets can't reference an in-memory QPixmap directly, so the
    checkmark is drawn once and saved to disk; QSS url() accepts a plain
    filesystem path as long as it uses forward slashes.
    """
    pixmap = QPixmap(20, 20)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#ffffff"))
    pen.setWidthF(2.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    path = QPainterPath()
    path.moveTo(4.5, 10.5)
    path.lineTo(8.3, 14.5)
    path.lineTo(15.5, 5.5)
    painter.drawPath(path)
    painter.end()

    tmp_path = Path(tempfile.gettempdir()) / "photo_organizer_checkmark.png"
    pixmap.save(str(tmp_path), "PNG")
    return str(tmp_path).replace("\\", "/")


class HeroGlowWidget(QWidget):
    """Soft atmospheric pink/purple/orange glow painted behind the Run page.

    Sized to the whole page (not just the header/stat row) so every gradient
    has room to fade fully to transparent before it reaches an edge - a QSS
    background on a tightly-sized widget reads as a hard rectangle, this
    doesn't.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        w = max(self.width(), 1)
        h = max(self.height(), 1)
        span = max(w, h)

        glows = [
            (w * 0.20, h * 0.02, span * 0.60, QColor(236, 72, 153, 50)),
            (w * 0.42, h * -0.06, span * 0.46, QColor(139, 92, 246, 42)),
            (w * 0.58, h * 0.10, span * 0.28, QColor(255, 107, 53, 32)),
        ]
        for cx, cy, radius, inner in glows:
            gradient = QRadialGradient(cx, cy, radius)
            gradient.setColorAt(0.0, inner)
            outer = QColor(inner)
            outer.setAlpha(0)
            gradient.setColorAt(1.0, outer)
            painter.setBrush(QBrush(gradient))
            painter.drawRect(self.rect())

        sparkles = [
            (0.10, 0.03, 2.0, 120), (0.24, 0.08, 1.4, 85), (0.34, 0.02, 1.7, 105),
            (0.46, 0.10, 1.3, 75), (0.15, 0.14, 1.2, 65), (0.52, 0.03, 1.6, 90),
        ]
        for fx, fy, radius, alpha in sparkles:
            painter.setBrush(QColor(255, 255, 255, alpha))
            painter.drawEllipse(QPointF(w * fx, h * fy), radius, radius)
        painter.end()


class ImagePreviewLabel(QLabel):
    """QLabel that keeps its source pixmap and re-scales on every resize.

    A one-shot scale-at-set-time approach breaks if the label's size isn't
    final yet when the image is first shown (e.g. populated before the
    window has been through its first show/layout pass) - the pixmap just
    sits at whatever wrong size it was scaled to. Re-deriving the scaled,
    rounded pixmap from the stored source on every resizeEvent makes the
    displayed image correct regardless of when or how the label got its
    current size.
    """

    def __init__(self) -> None:
        super().__init__()
        self._source_pixmap: QPixmap | None = None

    def set_source_pixmap(self, pixmap: QPixmap) -> None:
        self._source_pixmap = pixmap
        self._apply_scaled_pixmap()

    def show_placeholder(self, pixmap: QPixmap) -> None:
        self._source_pixmap = None
        self.setPixmap(pixmap)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_scaled_pixmap()

    def _apply_scaled_pixmap(self) -> None:
        if self._source_pixmap is None or self._source_pixmap.isNull():
            return
        scaled = self._source_pixmap.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(rounded_pixmap(scaled, 10))


class NavItem(QFrame):
    """Clickable sidebar row: painted left icon, label, and a chevron shown only when active."""

    clicked = Signal()
    ICON_SIZE = 19

    def __init__(self, kind: str, label: str) -> None:
        super().__init__()
        self.kind = kind
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(11)

        self.icon_label = QLabel()
        self.icon_label.setObjectName("navIcon")
        self.icon_label.setFixedSize(24, 24)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.text_label = QLabel(label)
        self.text_label.setObjectName("navText")

        self.chevron_label = QLabel("")
        self.chevron_label.setObjectName("navChevron")
        self.chevron_label.setFixedWidth(14)
        self.chevron_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(self.icon_label)
        layout.addWidget(self.text_label, 1)
        layout.addWidget(self.chevron_label)

        self.set_active(False)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def set_active(self, active: bool) -> None:
        self.setObjectName("navItemActive" if active else "navItemInactive")
        self.setFixedHeight(50 if active else 44)
        self.chevron_label.setText("›" if active else "")
        icon_color = "#ffffff" if active else NAV_ICON_MUTED
        self.icon_label.setPixmap(draw_nav_icon(self.kind, icon_color, self.ICON_SIZE))
        if active:
            glow = QGraphicsDropShadowEffect(self)
            glow.setColor(QColor(NAV_GLOW_COLOR))
            glow.setBlurRadius(28)
            glow.setOffset(0, 0)
            self.setGraphicsEffect(glow)
        else:
            self.setGraphicsEffect(None)
        for widget in (self, self.icon_label, self.text_label, self.chevron_label):
            widget.style().unpolish(widget)
            widget.style().polish(widget)


class PhotoOrganizerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.process: QProcess | None = None
        self.latest_csv_path: Path | None = None
        self.dest_root: Path | None = None
        self.report_ready = False

        self._ensure_sample_library()

        self.setWindowTitle("Photo Library Organizer")
        self.resize(1320, 880)
        self.setMinimumSize(1100, 680)

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
        self._reset_stat_cards()
        self._refresh_output_tab()
        self._refresh_report_tab()
        # Deferred: at construction time the preview label hasn't been through a
        # real layout pass yet (window isn't shown), so its reported size is
        # stale and scaling against it now would produce a wrongly-proportioned
        # image. Run once the event loop is idle, after the first show/layout.
        QTimer.singleShot(0, self._show_default_sample_preview)

    # ------------------------------------------------------------------
    # Sidebar / navigation
    # ------------------------------------------------------------------
    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(208)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(14, 18, 14, 18)
        layout.setSpacing(0)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(7)
        mark = QLabel("PL")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setObjectName("brandMark")
        title = QLabel("Photo Library\nOrganizer")
        title.setObjectName("brandTitle")
        brand_row.addWidget(mark)
        brand_row.addWidget(title, 1, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(brand_row)
        layout.addSpacing(20)

        self.nav_buttons: list[NavItem] = []
        nav_specs = [("run", "Run"), ("output", "Output"), ("report", "Report"), ("settings", "Settings")]
        for idx, (kind, label) in enumerate(nav_specs):
            item = NavItem(kind, label)
            item.clicked.connect(lambda page=idx: self._select_page(page))
            self.nav_buttons.append(item)
            layout.addWidget(item)
            layout.addSpacing(12 if idx == 0 else 3)

        layout.addStretch(1)

        sample_box = QFrame()
        sample_box.setObjectName("sideCard")
        sample_layout = QVBoxLayout(sample_box)
        sample_layout.setContentsMargins(11, 11, 11, 11)
        sample_layout.setSpacing(7)

        sample_header = QHBoxLayout()
        sample_header.setSpacing(9)
        sample_icon = QLabel()
        sample_icon.setObjectName("sampleIcon")
        sample_icon.setFixedSize(24, 24)
        sample_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sample_icon.setPixmap(draw_nav_icon("sample", "#ffffff", 14))
        self.sample_count = QLabel("Sample files: —")
        self.sample_count.setObjectName("sideLabel")
        sample_header.addWidget(sample_icon)
        sample_header.addWidget(self.sample_count, 1)
        sample_layout.addLayout(sample_header)

        reset = QPushButton("Reset Sample")
        reset.setObjectName("resetSampleButton")
        reset.clicked.connect(self.reset_sample_library)
        sample_layout.addWidget(reset)
        layout.addWidget(sample_box)

        return sidebar

    def _build_pages(self) -> QWidget:
        self.pages = QStackedWidget()
        self.pages.setObjectName("pages")
        self.pages.addWidget(self._build_run_page())
        self.pages.addWidget(self._scroll_wrap(self._build_output_page()))
        self.pages.addWidget(self._scroll_wrap(self._build_report_page()))
        self.pages.addWidget(self._build_settings_page())
        return self.pages

    def _select_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        for button_index, item in enumerate(self.nav_buttons):
            item.set_active(button_index == index)
        if index == 1:
            self._refresh_output_tab()
        elif index == 2:
            self._refresh_report_tab()

    # ------------------------------------------------------------------
    # Shared builder helpers
    # ------------------------------------------------------------------
    def _page_header(self, title: str, subtitle: str = "") -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName("pageHeaderTitle")
        column.addWidget(title_label)
        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setObjectName("pageSubtitle")
            column.addWidget(subtitle_label)
        return column

    def _make_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        return panel

    def _make_stat_card(self, kind: str, color: str, title: str, subtitle: str) -> tuple[QFrame, QLabel]:
        card = QFrame()
        card.setObjectName("statCard")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)

        tile = QLabel()
        tile.setObjectName("statIcon")
        tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tile.setFixedSize(38, 38)
        tile.setStyleSheet(f"background: {color}; border-radius: 10px;")
        tile.setPixmap(draw_nav_icon(kind, "#ffffff", 18))

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        title_label = QLabel(title)
        title_label.setObjectName("statTitle")
        value_label = QLabel("0")
        value_label.setObjectName("statValue")
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("statSubtitle")
        text_col.addWidget(title_label)
        text_col.addWidget(value_label)
        text_col.addWidget(subtitle_label)

        layout.addWidget(tile)
        layout.addLayout(text_col, 1)
        return card, value_label

    def _make_meta_row(self, dot_color: str, field: str, initial: str) -> tuple[QWidget, QLabel]:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        dot = QLabel("●")
        dot.setFixedWidth(12)
        dot.setStyleSheet(f"color: {dot_color}; font-size: 10px;")
        field_label = QLabel(field)
        field_label.setObjectName("metaField")
        field_label.setFixedWidth(88)
        value_label = QLabel(initial)
        value_label.setObjectName("metaValue")
        value_label.setWordWrap(True)
        layout.addWidget(dot)
        layout.addWidget(field_label)
        layout.addWidget(value_label, 1)
        return row, value_label

    def _make_table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setObjectName("resultsTable")
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setFixedHeight(38)
        table.verticalHeader().setVisible(False)
        table.setShowGrid(False)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setDefaultSectionSize(32)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        return table

    def _scroll_wrap(self, inner: QWidget) -> QWidget:
        scroll = QScrollArea()
        scroll.setObjectName("scrollArea")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(inner)
        return scroll

    # ------------------------------------------------------------------
    # Run page
    # ------------------------------------------------------------------
    def _field_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    def _build_run_page(self) -> QWidget:
        content = QWidget()
        content.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(22, 14, 22, 10)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.addLayout(self._page_header("Organize, rename, and sort your photos"))
        header.addStretch(1)
        self.run_button = QPushButton("Run Organizer")
        self.run_button.setObjectName("primaryButton")
        self.run_button.setIcon(QIcon(draw_nav_icon("run", "#ffffff", 14)))
        self.run_button.setIconSize(QSize(14, 14))
        self.run_button.clicked.connect(self.run_organizer)
        header.addWidget(self.run_button, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)

        stats_row = QHBoxLayout()
        stats_row.setSpacing(14)
        found_card, self.stat_found_label = self._make_stat_card("found", ACCENT_PURPLE, "Photos Found", "Total photos detected")
        renamed_card, self.stat_renamed_label = self._make_stat_card("renamed", ACCENT_PINK, "Renamed", "Files renamed")
        dups_card, self.stat_dups_label = self._make_stat_card("duplicates", ACCENT_ORANGE, "Duplicates", "Duplicate files found")
        report_card, self.stat_report_label = self._make_stat_card("report", ACCENT_GREEN, "Report Ready", "CSV report generated")
        for card in (found_card, renamed_card, dups_card, report_card):
            stats_row.addWidget(card, 1)
        layout.addLayout(stats_row)

        top_row = QHBoxLayout()
        top_row.setSpacing(14)

        # ---------------- Workflow card ----------------
        folder_card = self._make_panel()
        folder_layout = QVBoxLayout(folder_card)
        folder_layout.setContentsMargins(18, 14, 18, 14)
        folder_layout.setSpacing(0)

        workflow_title = QLabel("Workflow")
        workflow_title.setObjectName("sectionTitle")
        folder_layout.addWidget(workflow_title)
        folder_layout.addSpacing(10)

        folder_layout.addWidget(self._field_label("Source folder"))
        folder_layout.addSpacing(4)
        self.root_path = QLineEdit(SAMPLE_DISPLAY)
        self.root_path.setFixedHeight(32)
        self.root_path.setToolTip(str(SAMPLE_SOURCE))
        self.root_path.textChanged.connect(self._update_folder_labels)
        self.root_path.textChanged.connect(lambda _text: self._sync_source_tooltip())
        browse = QPushButton("Browse")
        browse.setObjectName("ghostButton")
        browse.setFixedWidth(96)
        browse.setFixedHeight(32)
        browse.clicked.connect(self.choose_root)
        source_row = QHBoxLayout()
        source_row.setSpacing(10)
        source_row.addWidget(self.root_path, 1)
        source_row.addWidget(browse)
        folder_layout.addLayout(source_row)
        folder_layout.addSpacing(10)

        folder_layout.addWidget(self._field_label("Destination folder"))
        folder_layout.addSpacing(4)
        self.destination_label = QLabel()
        self.destination_label.setObjectName("pathLabel")
        self.destination_label.setFixedHeight(32)
        self.destination_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.destination_label.setToolTip("Auto-generated from the source folder + destination name (Settings).")
        dest_browse = QPushButton("Browse")
        dest_browse.setObjectName("ghostButton")
        dest_browse.setFixedWidth(96)
        dest_browse.setFixedHeight(32)
        dest_browse.setToolTip("Choose a destination folder name (created under the source folder).")
        dest_browse.clicked.connect(self.choose_destination)
        dest_row = QHBoxLayout()
        dest_row.setSpacing(10)
        dest_row.addWidget(self.destination_label, 1)
        dest_row.addWidget(dest_browse)
        folder_layout.addLayout(dest_row)
        folder_layout.addSpacing(10)

        self.dry_run = QCheckBox("Dry run")
        self.dry_run.setChecked(True)
        self.csv_log = QCheckBox("Write report log")
        self.csv_log.setObjectName("accentCheck")
        self.csv_log.setChecked(True)
        options_row = QHBoxLayout()
        options_row.addWidget(self.dry_run)
        options_row.addStretch(1)
        options_row.addWidget(self.csv_log)
        folder_layout.addLayout(options_row)
        folder_layout.addSpacing(10)

        self.status_label = QLabel("Ready.")
        self.status_label.setObjectName("statusLabel")
        folder_layout.addWidget(self.status_label)
        folder_layout.addSpacing(8)

        progress_row = QHBoxLayout()
        progress_row.setSpacing(10)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress_percent_label = QLabel("0%")
        self.progress_percent_label.setObjectName("percentLabel")
        self.progress_percent_label.setFixedWidth(38)
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.progress_percent_label)
        folder_layout.addLayout(progress_row)
        folder_layout.addSpacing(8)

        self.stats_label = QLabel()
        self.stats_label.setObjectName("summaryRow")
        self.stats_label.setWordWrap(True)
        folder_layout.addWidget(self.stats_label)
        self._set_summary_row(0, 0, 0, 0)
        folder_layout.addStretch(1)
        top_row.addWidget(folder_card, 5)

        # ---------------- Current image card ----------------
        preview_card = self._make_panel()
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(16, 14, 16, 14)
        preview_layout.setSpacing(0)
        preview_title = QLabel("Current image")
        preview_title.setObjectName("sectionTitle")
        preview_layout.addWidget(preview_title)
        preview_layout.addSpacing(10)

        self.preview = ImagePreviewLabel()
        self.preview.setObjectName("preview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(150)
        self.preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        preview_layout.addWidget(self.preview, 1)
        preview_layout.addSpacing(10)

        self.preview_heading = QLabel("Waiting for preview")
        self.preview_heading.setObjectName("previewHeading")
        preview_layout.addWidget(self.preview_heading)
        preview_layout.addSpacing(2)
        self.preview_hint = QLabel("Run the organizer or select a sample image to preview details.")
        self.preview_hint.setObjectName("captionMuted")
        self.preview_hint.setWordWrap(True)
        preview_layout.addWidget(self.preview_hint)
        preview_layout.addSpacing(12)

        meta_column = QVBoxLayout()
        meta_column.setSpacing(10)
        name_row, self.meta_name_label = self._make_meta_row(ACCENT_PINK, "File", "—")
        size_row, self.meta_size_label = self._make_meta_row(ACCENT_PURPLE, "Size", "—")
        date_row, self.meta_date_label = self._make_meta_row(ACCENT_ORANGE, "Date", "—")
        dims_row, self.meta_dims_label = self._make_meta_row(ACCENT_GREEN, "Dimensions", "—")
        for row in (name_row, size_row, date_row, dims_row):
            meta_column.addWidget(row)
        preview_layout.addLayout(meta_column)
        top_row.addWidget(preview_card, 2)
        layout.addLayout(top_row)

        self._set_preview_empty()

        # ---------------- Actions table (fixed panel, scrolls internally) ----------------
        actions_card = self._make_panel()
        actions_layout = QVBoxLayout(actions_card)
        actions_layout.setContentsMargins(18, 13, 18, 12)
        actions_layout.setSpacing(0)
        actions_label = QLabel("Actions")
        actions_label.setObjectName("sectionTitle")
        actions_layout.addWidget(actions_label)
        actions_layout.addSpacing(8)
        self.results_table = self._make_table(["Action", "From", "To", "Source / Note", "Size", "Date Modified"])
        self.results_table.setMinimumHeight(120)
        self.results_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        actions_layout.addWidget(self.results_table, 1)
        actions_layout.addSpacing(8)
        self.results_status = QLabel("No run loaded yet.")
        self.results_status.setObjectName("muted")
        actions_layout.addWidget(self.results_status)
        layout.addWidget(actions_card, 1)

        # Full-page decorative glow behind everything, so its gradients have
        # room to fade to nothing before hitting an edge (see HeroGlowWidget).
        glow = HeroGlowWidget()
        page = QWidget()
        page_grid = QGridLayout(page)
        page_grid.setContentsMargins(0, 0, 0, 0)
        page_grid.addWidget(glow, 0, 0)
        page_grid.addWidget(content, 0, 0)
        glow.lower()
        return page

    # ------------------------------------------------------------------
    # Output page
    # ------------------------------------------------------------------
    def _build_output_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 26, 30, 26)
        layout.setSpacing(18)
        layout.addLayout(self._page_header("Output", "Browse files written to your destination folder. Read-only."))

        path_card = self._make_panel()
        path_layout = QHBoxLayout(path_card)
        path_layout.setContentsMargins(18, 16, 18, 16)
        path_layout.setSpacing(12)
        self.output_path_field = QLineEdit()
        self.output_path_field.setReadOnly(True)
        self.output_path_field.setMinimumHeight(38)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setObjectName("ghostButton")
        refresh_btn.clicked.connect(self._refresh_output_tab)
        open_btn = QPushButton("Open in File Explorer")
        open_btn.setObjectName("ghostButton")
        open_btn.clicked.connect(self.open_output_folder)
        path_layout.addWidget(self.output_path_field, 1)
        path_layout.addWidget(refresh_btn)
        path_layout.addWidget(open_btn)
        layout.addWidget(path_card)

        content_row = QHBoxLayout()
        content_row.setSpacing(16)

        table_card = self._make_panel()
        table_layout = QVBoxLayout(table_card)
        table_layout.setContentsMargins(20, 16, 20, 16)
        table_layout.setSpacing(8)
        self.output_table = self._make_table(["Name", "Type", "Size", "Modified"])
        self.output_table.itemSelectionChanged.connect(self._output_selection_changed)
        self.output_empty_label = QLabel(
            "No output yet. Run the organizer with Dry run unchecked to create files here."
        )
        self.output_empty_label.setObjectName("emptyState")
        self.output_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.output_empty_label.setWordWrap(True)
        table_layout.addWidget(self.output_table, 1)
        table_layout.addWidget(self.output_empty_label)
        content_row.addWidget(table_card, 3)

        preview_card = self._make_panel()
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(18, 16, 18, 16)
        preview_layout.setSpacing(10)
        preview_title = QLabel("Preview")
        preview_title.setObjectName("sectionTitle")
        self.output_preview = QLabel("Select a file")
        self.output_preview.setObjectName("preview")
        self.output_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.output_preview.setMinimumSize(200, 170)
        self.output_preview_meta = QLabel("No selection.")
        self.output_preview_meta.setObjectName("muted")
        self.output_preview_meta.setWordWrap(True)
        preview_layout.addWidget(preview_title)
        preview_layout.addWidget(self.output_preview)
        preview_layout.addWidget(self.output_preview_meta)
        preview_layout.addStretch(1)
        content_row.addWidget(preview_card, 2)

        layout.addLayout(content_row, 1)
        return page

    def _refresh_output_tab(self) -> None:
        if not hasattr(self, "output_path_field"):
            return
        root = self.dest_root or (self._root_from_field() / self.dest_name.text().strip())
        self.output_path_field.setText(self._display_path(root))
        self.output_table.setRowCount(0)
        self.output_preview.setPixmap(QPixmap())
        self.output_preview.setText("Select a file")
        self.output_preview_meta.setText("No selection.")

        if not root.exists():
            self.output_table.setVisible(False)
            self.output_empty_label.setVisible(True)
            return

        entries = sorted(
            (path for path in root.rglob("*") if path.is_file()),
            key=lambda path: str(path.relative_to(root)).lower(),
        )
        if not entries:
            self.output_table.setVisible(False)
            self.output_empty_label.setVisible(True)
            return

        self.output_empty_label.setVisible(False)
        self.output_table.setVisible(True)
        self.output_table.setRowCount(len(entries))
        for row_idx, path in enumerate(entries):
            stat = path.stat()
            modified = datetime.fromtimestamp(stat.st_mtime).strftime("%b %d, %Y %I:%M %p")
            name_item = make_item(str(path.relative_to(root)))
            name_item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.output_table.setItem(row_idx, 0, name_item)
            self.output_table.setItem(row_idx, 1, make_item(path.suffix.lstrip(".").upper() or "FILE", align_center=True))
            self.output_table.setItem(row_idx, 2, make_item(format_size(str(stat.st_size))))
            self.output_table.setItem(row_idx, 3, make_item(modified))

    def _output_selection_changed(self) -> None:
        items = self.output_table.selectedItems()
        if not items:
            return
        path_str = self.output_table.item(items[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        if not path_str:
            return
        path = Path(path_str)
        if path.suffix.lower() in IMAGE_EXTS and path.exists():
            image = QImage(str(path))
            if not image.isNull():
                pixmap = rounded_pixmap(
                    QPixmap.fromImage(image).scaled(
                        self.output_preview.size(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    ),
                    10,
                )
                self.output_preview.setText("")
                self.output_preview.setPixmap(pixmap)
            else:
                self.output_preview.setPixmap(QPixmap())
                self.output_preview.setText(path.name)
        else:
            self.output_preview.setPixmap(QPixmap())
            self.output_preview.setText(path.name)
        stat = path.stat()
        modified = datetime.fromtimestamp(stat.st_mtime).strftime("%b %d, %Y %I:%M %p")
        self.output_preview_meta.setText(f"{path.name}\n{format_size(str(stat.st_size))} • {modified}")

    # ------------------------------------------------------------------
    # Report page
    # ------------------------------------------------------------------
    def _build_report_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 26, 30, 26)
        layout.setSpacing(18)
        layout.addLayout(self._page_header("Report", "Review the CSV log generated by your last run."))

        path_card = self._make_panel()
        path_layout = QHBoxLayout(path_card)
        path_layout.setContentsMargins(18, 16, 18, 16)
        path_layout.setSpacing(12)
        self.report_path_field = QLineEdit()
        self.report_path_field.setReadOnly(True)
        self.report_path_field.setMinimumHeight(38)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setObjectName("ghostButton")
        refresh_btn.clicked.connect(self._refresh_report_tab)
        open_btn = QPushButton("Open in File Explorer")
        open_btn.setObjectName("ghostButton")
        open_btn.clicked.connect(self.open_csv)
        path_layout.addWidget(self.report_path_field, 1)
        path_layout.addWidget(refresh_btn)
        path_layout.addWidget(open_btn)
        layout.addWidget(path_card)

        summary_row = QHBoxLayout()
        summary_row.setSpacing(16)
        rows_card, self.report_rows_label = self._make_stat_card("report", ACCENT_PURPLE, "Report Rows", "Total logged actions")
        moved_card, self.report_moved_label = self._make_stat_card("output", ACCENT_PINK, "Moved", "Files moved")
        dups_card, self.report_dups_label = self._make_stat_card("duplicates", ACCENT_ORANGE, "Duplicates", "Duplicate files found")
        skipped_card, self.report_skipped_label = self._make_stat_card("skipped", ACCENT_GREEN, "Skipped", "Files skipped")
        for card in (rows_card, moved_card, dups_card, skipped_card):
            summary_row.addWidget(card, 1)
        layout.addLayout(summary_row)

        table_card = self._make_panel()
        table_layout = QVBoxLayout(table_card)
        table_layout.setContentsMargins(20, 16, 20, 16)
        table_layout.setSpacing(8)
        self.report_table = self._make_table(["Action", "From", "To", "Source / Note", "Size", "Date Modified"])
        self.report_empty_label = QLabel(
            "No report generated yet. Run the organizer with Write report log enabled to create a report."
        )
        self.report_empty_label.setObjectName("emptyState")
        self.report_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.report_empty_label.setWordWrap(True)
        table_layout.addWidget(self.report_table, 1)
        table_layout.addWidget(self.report_empty_label)
        layout.addWidget(table_card, 1)
        return page

    def _refresh_report_tab(self) -> None:
        if not hasattr(self, "report_path_field"):
            return
        path = self.latest_csv_path or (self._root_from_field() / "photo_organizer_run.csv")
        self.report_path_field.setText(str(path))

        if not path.exists():
            self.report_table.setVisible(False)
            self.report_empty_label.setVisible(True)
            for label in (self.report_rows_label, self.report_moved_label, self.report_dups_label, self.report_skipped_label):
                label.setText("0")
            return

        raw_actions, rows = self._read_csv_rows(path)
        self.report_empty_label.setVisible(False)
        self.report_table.setVisible(True)
        self._populate_action_table(self.report_table, rows)
        moved, _renamed, dups, skipped = self._summarize_actions(raw_actions)
        self.report_rows_label.setText(str(len(rows)))
        self.report_moved_label.setText(str(moved))
        self.report_dups_label.setText(str(dups))
        self.report_skipped_label.setText(str(skipped))

    # ------------------------------------------------------------------
    # Settings page
    # ------------------------------------------------------------------
    def _build_settings_page(self) -> QWidget:
        outer = QWidget()
        layout = QVBoxLayout(outer)
        layout.setContentsMargins(30, 26, 30, 26)
        layout.setSpacing(18)
        layout.addLayout(self._page_header("Settings", "Configure defaults and advanced organizer options."))

        general = self._make_panel()
        general_layout = QVBoxLayout(general)
        general_layout.setContentsMargins(22, 20, 22, 20)
        general_layout.setSpacing(12)
        general_title = QLabel("General")
        general_title.setObjectName("sectionTitle")
        general_layout.addWidget(general_title)

        self.default_dry_run_check = QCheckBox("Dry run by default")
        self.default_csv_log_check = QCheckBox("Write report log by default")
        self.default_csv_log_check.setObjectName("accentCheck")
        general_layout.addWidget(self.default_dry_run_check)
        general_layout.addWidget(self.default_csv_log_check)

        reset_row = QHBoxLayout()
        reset_row.addWidget(QLabel("Sample files"))
        reset_row.addStretch(1)
        reset_button = QPushButton("Reset Sample")
        reset_button.setObjectName("ghostButton")
        reset_button.clicked.connect(self.reset_sample_library)
        reset_row.addWidget(reset_button)
        general_layout.addLayout(reset_row)

        version_row = QHBoxLayout()
        version_row.addWidget(QLabel("App version"))
        version_row.addStretch(1)
        version_value = QLabel(APP_VERSION)
        version_value.setObjectName("metaValue")
        version_row.addWidget(version_value)
        general_layout.addLayout(version_row)

        workdir_row = QHBoxLayout()
        workdir_row.addWidget(QLabel("Working directory"))
        workdir_row.addStretch(1)
        workdir_value = QLineEdit(str(ROOT))
        workdir_value.setReadOnly(True)
        workdir_value.setObjectName("pathLabel")
        workdir_value.setMinimumWidth(320)
        workdir_row.addWidget(workdir_value)
        general_layout.addLayout(workdir_row)
        layout.addWidget(general)

        advanced = self._make_panel()
        grid = QGridLayout(advanced)
        grid.setContentsMargins(22, 20, 22, 20)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(14)
        advanced_title = QLabel("Advanced")
        advanced_title.setObjectName("sectionTitle")
        grid.addWidget(advanced_title, 0, 0, 1, 2)

        self.dest_name = QLineEdit("all_photos")
        self.dest_name.textChanged.connect(self._update_folder_labels)
        self.dup_action = QComboBox()
        self.dup_action.addItems(["move", "skip", "delete"])
        self.organize_year = QCheckBox("Organize destination into year folders")
        self.organize_year.setChecked(True)
        self.prefer_newest = QCheckBox("Prefer newest timestamp")
        self.no_exiftool = QCheckBox("Skip ExifTool and use file modified time")
        self.exiftool = QLineEdit("exiftool")
        self.exif_timeout = QSpinBox()
        self.exif_timeout.setRange(1, 120)
        self.exif_timeout.setValue(10)
        self.hash_max = QSpinBox()
        self.hash_max.setRange(1, 100000)
        self.hash_max.setValue(512)
        self.hash_max.setSuffix(" MB")
        self.exclude_paths = QPlainTextEdit()
        self.exclude_paths.setPlaceholderText("One folder per line, relative to root or absolute path")
        self.exclude_paths.setMaximumHeight(90)

        grid.addWidget(QLabel("Destination folder name"), 1, 0)
        grid.addWidget(self.dest_name, 1, 1)
        grid.addWidget(QLabel("Duplicate action"), 2, 0)
        grid.addWidget(self.dup_action, 2, 1)
        grid.addWidget(QLabel("ExifTool command/path"), 3, 0)
        grid.addWidget(self.exiftool, 3, 1)
        grid.addWidget(QLabel("ExifTool timeout"), 4, 0)
        grid.addWidget(self.exif_timeout, 4, 1)
        grid.addWidget(QLabel("Hash duplicates up to"), 5, 0)
        grid.addWidget(self.hash_max, 5, 1)
        grid.addWidget(self.organize_year, 6, 0, 1, 2)
        grid.addWidget(self.prefer_newest, 7, 0, 1, 2)
        grid.addWidget(self.no_exiftool, 8, 0, 1, 2)
        grid.addWidget(QLabel("Exclude folders"), 9, 0)
        grid.addWidget(self.exclude_paths, 9, 1)
        layout.addWidget(advanced)

        note = QLabel(
            "The UI calls bulk_image_rename.py with these options. Dry runs do not move files; real runs move "
            "media into the destination folder and write duplicates according to the selected action."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)

        # Defaults are linked to the Run tab's checkboxes once both exist.
        self._link_checkboxes(self.dry_run, self.default_dry_run_check)
        self._link_checkboxes(self.csv_log, self.default_csv_log_check)

        return self._scroll_wrap(outer)

    def _link_checkboxes(self, a: QCheckBox, b: QCheckBox) -> None:
        b.setChecked(a.isChecked())

        def sync(checked: bool, source: QCheckBox, target: QCheckBox) -> None:
            if target.isChecked() != checked:
                target.blockSignals(True)
                target.setChecked(checked)
                target.blockSignals(False)

        a.toggled.connect(lambda checked: sync(checked, a, b))
        b.toggled.connect(lambda checked: sync(checked, b, a))

    # ------------------------------------------------------------------
    # Folder selection / sample library
    # ------------------------------------------------------------------
    def choose_root(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose media root", str(self._root_from_field()))
        if folder:
            self.root_path.setText(folder)
            self._show_source_path_start()
            self._update_sample_count()
            self._update_folder_labels()

    def choose_destination(self) -> None:
        root = self._root_from_field()
        current_name = self.dest_name.text().strip()
        start_dir = root / current_name if current_name else root
        folder = QFileDialog.getExistingDirectory(
            self, "Choose destination folder", str(start_dir if start_dir.exists() else root)
        )
        if not folder:
            return
        selected = Path(folder)
        try:
            name = str(selected.resolve().relative_to(root.resolve()))
        except ValueError:
            name = selected.name
        self.dest_name.setText(name or "all_photos")
        self._update_folder_labels()

    def reset_sample_library(self) -> None:
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.information(self, "Run in progress", "Stop the current run before resetting the sample library.")
            return
        self._ensure_sample_library(force=True)
        self.root_path.setText(SAMPLE_DISPLAY)
        self._show_source_path_start()
        self.latest_csv_path = None
        self.dest_root = None
        self.results_table.setRowCount(0)
        self.results_status.setText("Sample library reset.")
        self.status_label.setText("Sample library reset. Ready.")
        self._set_preview_empty()
        self._show_default_sample_preview()
        self.progress.setValue(0)
        self.progress_percent_label.setText("0%")
        self._set_summary_row(0, 0, 0, 0)
        self._update_sample_count()
        self._reset_stat_cards()
        self._refresh_output_tab()
        self._refresh_report_tab()

    def _reset_stat_cards(self) -> None:
        self.stat_found_label.setText(str(self._sample_media_count()))
        self.stat_renamed_label.setText("0")
        self.stat_dups_label.setText("0")
        self.report_ready = False
        self.stat_report_label.setText("No")

    def _set_summary_row(self, moved: int, renamed: int, dups: int, skipped: int) -> None:
        self.stats_label.setText(
            f'Moved <span style="color:{ACCENT_PINK}; font-weight:800;">{moved}</span>'
            f' &nbsp;|&nbsp; Renamed <span style="color:{ACCENT_PURPLE}; font-weight:800;">{renamed}</span>'
            f' &nbsp;|&nbsp; Duplicates <span style="color:{ACCENT_ORANGE}; font-weight:800;">{dups}</span>'
            f' &nbsp;|&nbsp; Skipped <span style="color:{TEXT_MUTED}; font-weight:800;">{skipped}</span>'
        )

    # ------------------------------------------------------------------
    # Organizer process
    # ------------------------------------------------------------------
    def run_organizer(self) -> None:
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
            self.status_label.setText("Stopping...")
            return

        root = self._root_from_field()
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
        self._set_preview_empty()
        self.preview_heading.setText("Waiting for first file")
        self.progress.setValue(0)
        self.progress_percent_label.setText("0%")
        self.status_label.setText("Starting...")
        self._set_summary_row(0, 0, 0, 0)
        self._reset_stat_cards()
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

    def _update_folder_labels(self) -> None:
        if not hasattr(self, "destination_label"):
            return
        root = self._root_from_field()
        destination = root / self.dest_name.text().strip()
        self.destination_label.setText(self._display_path(destination))
        self.destination_label.setToolTip(str(destination))

    def _root_from_field(self) -> Path:
        raw_path = self.root_path.text().strip() if hasattr(self, "root_path") else ""
        if not raw_path:
            return SAMPLE_SOURCE
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = ROOT / path
        return path

    def _display_path(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(ROOT))
        except ValueError:
            return str(path)

    def _sync_source_tooltip(self) -> None:
        if not hasattr(self, "root_path"):
            return
        self.root_path.setToolTip(str(self._root_from_field()))

    def _show_source_path_start(self) -> None:
        if not hasattr(self, "root_path"):
            return
        self.root_path.setCursorPosition(0)
        self.root_path.deselect()

    def _update_run_label(self) -> None:
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            self.run_button.setText("Stop")
        else:
            self.run_button.setText("Run Organizer")

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
        found_match = FOUND_RE.search(normalized)
        if found_match:
            self.stat_found_label.setText(found_match.group(1).replace(",", ""))
        for match in PROGRESS_RE.finditer(normalized):
            percent = float(match.group(3))
            moved, renamed, dups, skipped = (int(match.group(i).replace(",", "")) for i in range(4, 8))
            self.progress.setValue(int(percent))
            self.progress_percent_label.setText(f"{int(percent)}%")
            self.status_label.setText(f"Processing {match.group(1)} of {match.group(2)} files")
            self._set_summary_row(moved, renamed, dups, skipped)
            self.stat_found_label.setText(match.group(2).replace(",", ""))
            self.stat_renamed_label.setText(str(renamed))
            self.stat_dups_label.setText(str(dups))
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
            if action not in ACTION_NAMES:
                continue
            source_path = Path(source)
            destination_path = Path(destination) if destination else None
            preview_path = self._resolve_preview_path(source_path, destination_path)
            if preview_path:
                self._show_current_media(preview_path)
            self._append_action_row([format_action(action), source, destination, "", "", ""])

    def _append_action_row(self, row: list[str]) -> None:
        row_idx = self.results_table.rowCount()
        self.results_table.insertRow(row_idx)
        for col_idx, value in enumerate(row):
            color = ACCENT_PINK if col_idx == 0 else TEXT_PRIMARY
            self.results_table.setItem(row_idx, col_idx, make_item(value, align_center=col_idx == 0, color=color))
        self.results_table.scrollToBottom()

    def _set_preview_placeholder(self) -> None:
        icon = draw_nav_icon("found", TEXT_MUTED, 40)
        self.preview.show_placeholder(icon)

    def _set_preview_empty(self) -> None:
        self._set_preview_placeholder()
        self.preview_heading.setText("Waiting for preview")
        self.preview_heading.setVisible(True)
        self.preview_hint.setVisible(True)
        self.meta_name_label.setText("—")
        self.meta_name_label.setToolTip("")
        self.meta_size_label.setText("—")
        self.meta_date_label.setText("—")
        self.meta_dims_label.setText("—")

    def _show_current_media(self, path: Path) -> None:
        self.preview_heading.setVisible(False)
        self.preview_hint.setVisible(False)
        self.meta_name_label.setText(path.name)
        self.meta_name_label.setToolTip(str(path))
        if not path.exists():
            self._set_preview_placeholder()
            self.meta_size_label.setText("—")
            self.meta_date_label.setText("—")
            self.meta_dims_label.setText("—")
            return

        stat = path.stat()
        self.meta_size_label.setText(format_size(str(stat.st_size)))
        self.meta_date_label.setText(datetime.fromtimestamp(stat.st_mtime).strftime("%b %d, %Y"))

        if path.suffix.lower() not in IMAGE_EXTS:
            self._set_preview_placeholder()
            self.meta_dims_label.setText("—")
            return
        try:
            image_bytes = path.read_bytes()
        except OSError:
            self._set_preview_placeholder()
            self.meta_dims_label.setText("—")
            return
        image = QImage()
        if not image.loadFromData(image_bytes):
            self._set_preview_placeholder()
            self.meta_dims_label.setText("—")
            return
        self.meta_dims_label.setText(f"{image.width()} × {image.height()}")
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            self._set_preview_placeholder()
            return
        self.preview.set_source_pixmap(pixmap)

    def _show_default_sample_preview(self) -> None:
        if not SAMPLE_SOURCE.exists():
            return
        files = [
            path
            for path in SAMPLE_SOURCE.iterdir()
            if path.is_file() and path.suffix.lower() in TARGET_EXTS
        ]
        if not files:
            return

        def sample_key(path: Path) -> tuple[int, str]:
            try:
                return int(path.stem), path.name
            except ValueError:
                return 10_000, path.name

        self._show_current_media(sorted(files, key=sample_key)[0])

    def _resolve_preview_path(self, source_path: Path, destination_path: Path | None) -> Path | None:
        if source_path.exists():
            return source_path
        if destination_path and destination_path.exists():
            return destination_path
        if destination_path and self.dest_root and self.dest_root.exists():
            for candidate in self.dest_root.rglob(destination_path.name):
                if candidate.is_file():
                    return candidate
        return destination_path or source_path

    def _process_finished(self, exit_code: int, _exit_status) -> None:
        self._update_run_label()
        if exit_code == 0:
            self.progress.setValue(100)
            self.progress_percent_label.setText("100%")
            self.status_label.setText("Complete.")
        else:
            self.status_label.setText(f"Stopped with exit code {exit_code}. See output for details.")
        self._load_csv_results()
        self.report_ready = bool(self.latest_csv_path and self.latest_csv_path.exists())
        self.stat_report_label.setText("Yes" if self.report_ready else "No")
        self._refresh_output_tab()
        self._refresh_report_tab()

    def _process_error(self, error) -> None:
        self._update_run_label()
        self.status_label.setText(f"Process error: {error}")

    # ------------------------------------------------------------------
    # CSV report helpers
    # ------------------------------------------------------------------
    def _read_csv_rows(self, csv_path: Path) -> tuple[list[str], list[list[str]]]:
        raw_actions: list[str] = []
        rows: list[list[str]] = []
        with csv_path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                action = row.get("action", "")
                raw_actions.append(action)
                rows.append(
                    [
                        format_action(action),
                        row.get("old_path", ""),
                        row.get("new_path", ""),
                        row.get("source", "") or row.get("note", ""),
                        format_size(row.get("size_bytes", "")),
                        format_timestamp(row.get("timestamp", "")),
                    ]
                )
        return raw_actions, rows

    def _summarize_actions(self, raw_actions: list[str]) -> tuple[int, int, int, int]:
        moved = sum(1 for action in raw_actions if action.upper() in {"MOVE", "MOVE+RENAME"})
        renamed = sum(1 for action in raw_actions if action.upper() == "MOVE+RENAME")
        dups = sum(1 for action in raw_actions if action.upper().startswith("DUP"))
        skipped = sum(1 for action in raw_actions if action.upper() == "SKIP")
        return moved, renamed, dups, skipped

    def _populate_action_table(self, table: QTableWidget, rows: list[list[str]]) -> None:
        table.setRowCount(len(rows))
        for row_idx, row in enumerate(rows):
            for col_idx, value in enumerate(row):
                color = ACCENT_PINK if col_idx == 0 else TEXT_PRIMARY
                table.setItem(row_idx, col_idx, make_item(value, align_center=col_idx == 0, color=color))
        table.scrollToTop()
        table.verticalScrollBar().setValue(0)

    def _load_csv_results(self) -> None:
        if not self.latest_csv_path or not self.latest_csv_path.exists():
            self.results_status.setText("No report was written.")
            return

        raw_actions, rows = self._read_csv_rows(self.latest_csv_path)
        self._populate_action_table(self.results_table, rows)
        self.results_status.setText(
            f'Loaded {len(rows)} report rows from '
            f'<span style="color:{ACCENT_PINK};">{self.latest_csv_path}</span>'
        )

    # ------------------------------------------------------------------
    # External open helpers
    # ------------------------------------------------------------------
    def open_csv(self) -> None:
        if not self.latest_csv_path or not self.latest_csv_path.exists():
            QMessageBox.information(self, "Report not ready", "Run with report logging enabled first.")
            return
        self._open_path(self.latest_csv_path)

    def open_output_folder(self) -> None:
        if self.dest_root and self.dest_root.exists():
            self._open_path(self.dest_root)
            return

        root = self._root_from_field()
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

    # ------------------------------------------------------------------
    # Sample library management
    # ------------------------------------------------------------------
    def _sample_media_count(self) -> int:
        if not SAMPLE_SOURCE.exists():
            return 0
        return sum(
            1
            for path in SAMPLE_SOURCE.rglob("*")
            if "all_photos" not in path.parts and path.is_file() and path.suffix.lower() in TARGET_EXTS
        )

    def _update_sample_count(self) -> None:
        self.sample_count.setText(f"Sample files: {self._sample_media_count()}")

    def _has_sample_source_media(self) -> bool:
        return self._sample_media_count() > 0

    def _ensure_sample_library(self, force: bool = False) -> None:
        SAMPLE_SOURCE.mkdir(parents=True, exist_ok=True)
        if force:
            self._clean_sample_outputs()
            self._restore_tracked_sample_files()
        elif not self._has_sample_source_media():
            self._restore_tracked_sample_files()
        self._stamp_sample_times()

    def _clean_sample_outputs(self) -> None:
        for folder_name in ("all_photos", "_DUPLICATES"):
            folder = SAMPLE_SOURCE / folder_name
            if folder.exists():
                shutil.rmtree(folder)
        for csv_path in SAMPLE_SOURCE.glob("*.csv"):
            csv_path.unlink()

    def _restore_tracked_sample_files(self) -> None:
        try:
            subprocess.run(
                ["git", "restore", "--source=HEAD", "--", str(SAMPLE_SOURCE.relative_to(ROOT))],
                cwd=ROOT,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            return

    def _stamp_sample_times(self) -> None:
        files = [
            path
            for path in SAMPLE_SOURCE.iterdir()
            if path.is_file() and path.suffix.lower() in TARGET_EXTS
        ]

        def sample_key(path: Path) -> tuple[int, str]:
            try:
                return int(path.stem), path.name
            except ValueError:
                return 10_000, path.name

        base = datetime(2021, 1, 16, 10, 30, 0)
        for idx, path in enumerate(sorted(files, key=sample_key)):
            stamp = base + timedelta(days=idx * 43, hours=idx, minutes=idx * 3)
            ts = stamp.timestamp()
            os.utime(path, (ts, ts))

    # ------------------------------------------------------------------
    # Styling
    # ------------------------------------------------------------------
    def _apply_styles(self) -> None:
        checkmark_path = checkmark_asset_path()
        self.setStyleSheet(
            f"""
            QMainWindow {{
                background: {BG};
            }}
            QWidget {{
                color: {TEXT_MUTED};
                font-size: 13px;
            }}
            #sidebar {{
                background: {SIDEBAR_GRADIENT_CSS};
                border-right: 1px solid {SIDEBAR_BORDER};
            }}
            #pages {{
                background: {BG};
            }}
            #scrollArea, #scrollArea > QWidget > QWidget {{
                background: transparent;
                border: 0;
            }}
            #brandMark {{
                min-width: 41px;
                min-height: 41px;
                max-width: 41px;
                max-height: 41px;
                border-radius: 10px;
                background: {LOGO_GRADIENT_CSS};
                color: #ffffff;
                font-size: 14px;
                font-weight: 900;
            }}
            #brandTitle {{
                color: {TEXT_PRIMARY};
                font-size: 12px;
                font-weight: 700;
                line-height: 105%;
            }}
            #navItemActive, #navItemInactive {{
                border-radius: 12px;
                border: 0;
            }}
            #navItemInactive {{
                background: transparent;
            }}
            #navItemInactive:hover {{
                background: {CARD_ALT};
            }}
            #navItemActive {{
                background: {NAV_ACTIVE_GRADIENT_CSS};
                border: 1px solid rgba(255, 255, 255, 0.10);
            }}
            #navItemInactive #navText {{
                color: {NAV_TEXT_MUTED};
                font-weight: 600;
            }}
            #navItemInactive:hover #navText {{
                color: {TEXT_PRIMARY};
            }}
            #navItemActive #navText, #navItemActive #navChevron {{
                color: #ffffff;
            }}
            #navText {{
                font-size: 13px;
                font-weight: 700;
            }}
            #navItemActive #navText {{
                font-weight: 800;
            }}
            #navChevron {{
                font-size: 15px;
                font-weight: 800;
            }}
            #primaryButton {{
                min-height: 40px;
                border-radius: 10px;
                padding: 0 18px;
                font-weight: 800;
                border: 0;
                background: {GRADIENT_CSS};
                color: #ffffff;
            }}
            #primaryButton:hover {{
                background: {ACCENT_PINK};
            }}
            #primaryButton:pressed {{
                background: {ACCENT_PURPLE};
            }}
            #ghostButton {{
                min-height: 38px;
                border-radius: 10px;
                padding: 0 16px;
                font-weight: 700;
                border: 1px solid {BORDER_SOFT};
                background: {CARD_ALT};
                color: {TEXT_PRIMARY};
            }}
            #ghostButton:hover {{
                border: 1px solid {ACCENT_PINK};
                color: {ACCENT_PINK};
            }}
            #sideCard, #panel, #statCard {{
                background: {CARD};
                border: 1px solid {BORDER};
                border-radius: 14px;
            }}
            #statCard {{
                background: {CARD_ALT};
            }}
            #sideCard {{
                background: {CARD_ALT};
                border: 1px solid {SIDEBAR_BORDER};
            }}
            #sideLabel {{
                color: {NAV_TEXT_MUTED};
                font-weight: 700;
                font-size: 12px;
            }}
            #sampleIcon {{
                background: {ACCENT_PINK};
                border-radius: 7px;
            }}
            #resetSampleButton {{
                min-height: 34px;
                border-radius: 9px;
                font-weight: 600;
                font-size: 12px;
                border: 1px solid {SAMPLE_BORDER_SOFT};
                background: {BG};
                color: {NAV_TEXT_MUTED};
            }}
            #resetSampleButton:hover {{
                border: 1px solid {ACCENT_ORANGE};
                color: {TEXT_PRIMARY};
            }}
            #sectionTitle {{
                color: {TEXT_PRIMARY};
                font-size: 15px;
                font-weight: 800;
            }}
            #statTitle {{
                color: {TEXT_MUTED};
                font-size: 11px;
                font-weight: 700;
            }}
            #statValue {{
                color: {TEXT_PRIMARY};
                font-size: 19px;
                font-weight: 900;
            }}
            #statSubtitle {{
                color: {TEXT_MUTED};
                font-size: 10px;
                font-weight: 500;
            }}
            #pageHeaderTitle {{
                color: {TEXT_PRIMARY};
                font-size: 24px;
                font-weight: 900;
            }}
            #pageSubtitle {{
                color: {TEXT_MUTED};
                font-size: 13px;
                font-weight: 500;
            }}
            #pathLabel {{
                background: {CARD_ALT};
                border: 1px solid {BORDER};
                border-radius: 10px;
                color: {TEXT_PRIMARY};
                font-weight: 700;
                padding: 0 12px;
            }}
            #preview {{
                background: {CARD_ALT};
                border: 1px solid {BORDER};
                border-radius: 12px;
                color: {TEXT_MUTED};
                font-weight: 700;
            }}
            #metaValue {{
                color: {TEXT_PRIMARY};
                font-weight: 700;
            }}
            #metaField {{
                color: {TEXT_MUTED};
                font-weight: 600;
                font-size: 12px;
            }}
            #previewHeading {{
                color: {TEXT_PRIMARY};
                font-size: 13px;
                font-weight: 800;
            }}
            #captionMuted {{
                color: {TEXT_MUTED};
                font-size: 11px;
                font-weight: 500;
            }}
            #fieldLabel {{
                color: {TEXT_PRIMARY};
                font-size: 12px;
                font-weight: 700;
            }}
            #percentLabel {{
                color: {TEXT_PRIMARY};
                font-weight: 800;
            }}
            #summaryRow {{
                color: {TEXT_PRIMARY};
                font-weight: 600;
            }}
            #statusLabel {{
                color: {TEXT_PRIMARY};
                font-weight: 800;
            }}
            #emptyState {{
                color: {TEXT_MUTED};
                font-weight: 600;
                padding: 24px;
            }}
            QLabel {{
                color: {TEXT_MUTED};
                font-weight: 600;
            }}
            QLineEdit, QComboBox, QSpinBox {{
                border: 1px solid {BORDER};
                border-radius: 10px;
                background: {CARD_ALT};
                color: {TEXT_PRIMARY};
                padding: 0 12px;
                font-weight: 600;
            }}
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
                border: 1px solid {ACCENT_PINK};
            }}
            QLineEdit:read-only {{
                color: {TEXT_MUTED};
            }}
            QPlainTextEdit {{
                border: 1px solid {BORDER};
                border-radius: 10px;
                background: {CARD_ALT};
                color: {TEXT_PRIMARY};
                padding: 8px;
                font-weight: 600;
            }}
            QComboBox::drop-down {{
                border: 0;
                width: 24px;
            }}
            QCheckBox {{
                color: {TEXT_PRIMARY};
                font-weight: 700;
                spacing: 8px;
            }}
            QCheckBox::indicator {{
                width: 18px;
                height: 18px;
                border-radius: 5px;
                border: 2px solid {BORDER_SOFT};
                background: {CARD_ALT};
            }}
            QCheckBox::indicator:hover {{
                border: 2px solid {ACCENT_PURPLE};
            }}
            QCheckBox::indicator:checked {{
                background: {ACCENT_PURPLE};
                border: 2px solid {ACCENT_PURPLE};
                image: url({checkmark_path});
            }}
            #accentCheck::indicator:hover {{
                border: 2px solid {ACCENT_PINK};
            }}
            #accentCheck::indicator:checked {{
                background: {ACCENT_PINK};
                border: 2px solid {ACCENT_PINK};
                image: url({checkmark_path});
            }}
            QProgressBar {{
                min-height: 12px;
                max-height: 12px;
                border: 0;
                border-radius: 6px;
                background: {CARD_ALT};
            }}
            QProgressBar::chunk {{
                border-radius: 6px;
                background: {GRADIENT_CSS};
            }}
            #resultsTable {{
                background: {CARD_ALT};
                color: {TEXT_PRIMARY};
                alternate-background-color: {CARD};
                border: 1px solid {BORDER};
                border-radius: 0;
                gridline-color: {BORDER};
                selection-background-color: #2a1f3d;
                selection-color: {TEXT_PRIMARY};
            }}
            #resultsTable::item {{
                padding: 6px 10px;
                border: 0;
            }}
            QHeaderView::section {{
                background: {CARD};
                color: {TEXT_MUTED};
                border: 0;
                border-bottom: 1px solid {BORDER};
                padding: 8px;
                font-weight: 800;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 10px;
            }}
            QScrollBar::handle:vertical {{
                background: {BORDER_SOFT};
                border-radius: 5px;
                min-height: 24px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
            #muted {{
                color: {TEXT_MUTED};
                font-weight: 500;
            }}
            """
        )


def main() -> int:
    app = QApplication(sys.argv)
    window = PhotoOrganizerWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
