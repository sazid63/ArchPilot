"""Visual language for ArchPilot: a drafting-office look, defined once.

The palette is deliberately engineering rather than consumer: blueprint navy for primary
actions, concrete greys for surfaces, survey-amber for anything that needs attention, and a
monospace face for every number that ends up in a bill. Colour never carries meaning on its
own — every status also has words next to it — because a cost report has to survive being
printed in black and white.

All sizes are in device-independent pixels. The whole stylesheet is generated from the tokens
below, so changing the palette changes every screen at once.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    """One named colour per role. Nothing in the UI should hard-code a hex value."""

    # Surfaces
    paper: str = "#F4F5F7"          # window background: drafting paper
    panel: str = "#FFFFFF"          # cards and tables
    panel_alt: str = "#EEF1F5"      # alternating rows, inset areas
    hairline: str = "#D5DAE2"       # 1px separators

    # Ink
    ink: str = "#1A2029"            # primary text
    ink_soft: str = "#5A6572"       # secondary text, captions
    ink_faint: str = "#8C96A3"      # disabled, placeholders

    # Brand: blueprint navy
    primary: str = "#1B4F8A"
    primary_hover: str = "#22629F"
    primary_pressed: str = "#143C6B"
    on_primary: str = "#FFFFFF"
    primary_wash: str = "#E7EFF8"   # selected nav item, hovered row

    # Signal colours
    accent: str = "#B06A00"         # survey amber: attention, preliminary warnings
    accent_wash: str = "#FDF3E3"
    ok: str = "#1E7A46"             # verified / up to date
    ok_wash: str = "#E6F4EC"
    danger: str = "#B3261E"         # errors, refused suggestions
    danger_wash: str = "#FCECEA"

    # Code / numbers
    mono: str = "Cascadia Mono, Consolas, DejaVu Sans Mono, monospace"
    sans: str = "Segoe UI, Inter, Noto Sans, sans-serif"


PALETTE = Palette()

#: Human labels for the units this app produces, so the UI never shows a bare "m3" alone.
UNIT_LABELS = {"m3": "m³", "m2": "m²", "m": "m", "nos": "nr", "kg": "kg", "t": "t",
               "l": "L", "day": "day", "hr": "hr", "lot": "lot"}


def unit_label(unit: str) -> str:
    """A readable unit for display, without altering the stored unit string."""
    return UNIT_LABELS.get(unit, unit)


def stylesheet(palette: Palette = PALETTE) -> str:
    """The application stylesheet, generated from the palette."""
    p = palette
    return f"""
* {{
    font-family: "{p.sans}";
    font-size: 10.0pt;
    color: {p.ink};
}}
QWidget#Root, QDialog, QMainWindow {{ background: {p.paper}; }}

/* ---- Navigation rail -------------------------------------------------- */
QListWidget#Nav {{
    background: {p.primary};
    border: none;
    outline: 0;
    padding: 8px 0;
}}
QListWidget#Nav::item {{
    color: #CBD9EA;
    padding: 11px 16px;
    border: none;
    border-left: 3px solid transparent;
}}
QListWidget#Nav::item:hover {{ background: {p.primary_hover}; color: #FFFFFF; }}
QListWidget#Nav::item:selected {{
    background: {p.primary_pressed};
    color: #FFFFFF;
    border-left: 3px solid {p.accent};
    font-weight: 600;
}}

/* ---- Cards ------------------------------------------------------------- */
QFrame#Card {{
    background: {p.panel};
    border: 1px solid {p.hairline};
    border-radius: 8px;
}}
QLabel#CardTitle {{ font-size: 11.5pt; font-weight: 600; }}
QLabel#PageTitle {{ font-size: 17pt; font-weight: 600; }}
QLabel#PageSubtitle {{ color: {p.ink_soft}; font-size: 10pt; }}
QLabel#SectionLabel {{
    color: {p.ink_soft};
    font-weight: 600;
    font-size: 9pt;
    text-transform: uppercase;
    letter-spacing: 0.6px;
}}
QLabel#Hint {{ color: {p.ink_soft}; }}
QLabel#Faint {{ color: {p.ink_faint}; }}
QLabel#Mono {{ font-family: "{p.mono}"; }}
QLabel#BigNumber {{ font-family: "{p.mono}"; font-size: 20pt; font-weight: 600; }}
QLabel#StatLabel {{ color: {p.ink_soft}; font-size: 9pt; }}

/* ---- Buttons ----------------------------------------------------------- */
QPushButton {{
    background: {p.panel};
    border: 1px solid {p.hairline};
    border-radius: 6px;
    padding: 7px 14px;
    color: {p.ink};
}}
QPushButton:hover {{ border-color: {p.primary}; background: {p.primary_wash}; }}
QPushButton:pressed {{ background: {p.panel_alt}; }}
QPushButton:disabled {{
    color: {p.ink_faint}; border-color: {p.hairline}; background: {p.panel_alt};
}}
QPushButton#Primary {{
    background: {p.primary};
    color: {p.on_primary};
    border: 1px solid {p.primary};
    font-weight: 600;
}}
QPushButton#Primary:hover {{ background: {p.primary_hover}; border-color: {p.primary_hover}; }}
QPushButton#Primary:pressed {{ background: {p.primary_pressed}; }}
QPushButton#Primary:disabled {{ background: #A9B7C6; border-color: #A9B7C6; color: #EEF1F5; }}
QPushButton#Danger {{ color: {p.danger}; border-color: #E7B9B5; }}
QPushButton#Danger:hover {{ background: {p.danger_wash}; border-color: {p.danger}; }}
QPushButton#Link {{ background: transparent; border: none; color: {p.primary}; padding: 2px 4px; }}
QPushButton#Link:hover {{ text-decoration: underline; }}

/* ---- Inputs ------------------------------------------------------------ */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {p.panel};
    border: 1px solid {p.hairline};
    border-radius: 6px;
    padding: 6px 8px;
    selection-background-color: {p.primary};
    selection-color: #FFFFFF;
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QComboBox:focus {{ border: 1px solid {p.primary}; }}
QLineEdit:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
    background: {p.panel_alt}; color: {p.ink_faint};
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {p.panel};
    border: 1px solid {p.hairline};
    selection-background-color: {p.primary_wash};
    selection-color: {p.ink};
    outline: 0;
}}
QCheckBox, QRadioButton {{ spacing: 7px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 15px; height: 15px; }}

/* ---- Tables ------------------------------------------------------------ */
QTableView, QTableWidget {{
    background: {p.panel};
    alternate-background-color: {p.panel_alt};
    border: 1px solid {p.hairline};
    border-radius: 8px;
    gridline-color: {p.hairline};
    selection-background-color: {p.primary_wash};
    selection-color: {p.ink};
    outline: 0;
}}
QHeaderView::section {{
    background: {p.panel_alt};
    color: {p.ink_soft};
    font-weight: 600;
    padding: 7px 8px;
    border: none;
    border-right: 1px solid {p.hairline};
    border-bottom: 1px solid {p.hairline};
}}
QTableView::item {{ padding: 4px 6px; }}
QTableCornerButton::section {{ background: {p.panel_alt}; border: none; }}

/* ---- Progress and status ---------------------------------------------- */
QProgressBar {{
    background: {p.panel_alt};
    border: 1px solid {p.hairline};
    border-radius: 6px;
    height: 10px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background: {p.primary}; border-radius: 5px; }}
QProgressBar#Busy::chunk {{ background: {p.accent}; }}

/* ---- Banners ----------------------------------------------------------- */
QFrame#Banner {{ border-radius: 8px; border: 1px solid {p.hairline}; }}
QFrame#BannerInfo {{ background: {p.primary_wash}; border: 1px solid #C6D9EE; }}
QFrame#BannerWarn {{ background: {p.accent_wash}; border: 1px solid #EBD3A8; }}
QFrame#BannerOk {{ background: {p.ok_wash}; border: 1px solid #BFE0CC; }}
QFrame#BannerError {{ background: {p.danger_wash}; border: 1px solid #E7B9B5; }}
QLabel#BannerTitleInfo {{ color: {p.primary}; font-weight: 600; }}
QLabel#BannerTitleWarn {{ color: {p.accent}; font-weight: 600; }}
QLabel#BannerTitleOk {{ color: {p.ok}; font-weight: 600; }}
QLabel#BannerTitleError {{ color: {p.danger}; font-weight: 600; }}

/* ---- Misc -------------------------------------------------------------- */
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QSplitter::handle {{ background: {p.hairline}; }}
QToolTip {{
    background: {p.ink};
    color: #FFFFFF;
    border: none;
    padding: 5px 7px;
    border-radius: 4px;
}}
QStatusBar {{ background: {p.panel}; border-top: 1px solid {p.hairline}; }}
QStatusBar::item {{ border: none; }}
QTabWidget::pane {{ border: 1px solid {p.hairline}; border-radius: 8px; background: {p.panel}; }}
QTabBar::tab {{
    background: transparent; padding: 8px 14px; border: none; color: {p.ink_soft};
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{
    color: {p.primary}; border-bottom: 2px solid {p.primary}; font-weight: 600;
}}
QGroupBox {{
    border: 1px solid {p.hairline}; border-radius: 8px; margin-top: 12px; padding-top: 10px;
    background: {p.panel};
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {p.ink_soft};
    font-weight: 600;
}}
QMessageBox {{ background: {p.panel}; }}
QMenu {{ background: {p.panel}; border: 1px solid {p.hairline}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 4px; }}
QMenu::item:selected {{ background: {p.primary_wash}; }}
"""


#: Severity names used by banners and status labels. Words always accompany the colour.
SEVERITY_TITLE_OBJECT = {
    "info": "BannerTitleInfo",
    "warning": "BannerTitleWarn",
    "ok": "BannerTitleOk",
    "error": "BannerTitleError",
}
SEVERITY_FRAME_OBJECT = {
    "info": "BannerInfo",
    "warning": "BannerWarn",
    "ok": "BannerOk",
    "error": "BannerError",
}
