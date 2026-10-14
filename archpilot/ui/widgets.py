"""Small, reusable pieces of interface.

These exist so that every page reports problems the same way. The single most important
principle here is that a :class:`Banner` shows a *word* as well as a colour, and that a
:func:`confirm` or :func:`show_error` always names what happened in plain language — the app is
aimed at people who are not programmers and who will not read a stack trace.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from archpilot.ui import theme


def plain_text(label: QLabel) -> QLabel:
    """Force a label to render literal text, never rich text.

    Qt labels auto-detect HTML, so a project name, a file path or an AI message containing
    ``<img src=...>`` would make the label fetch a remote URL when it is shown. Anything that can
    carry such text is put through here so it is always displayed exactly as typed.
    """
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


def _spacer() -> QWidget:
    w = QWidget()
    w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return w


class Card(QFrame):
    """A titled panel. Content goes into :attr:`body`."""

    def __init__(self, title: str = "", subtitle: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 16)
        outer.setSpacing(10)

        self.header = QHBoxLayout()
        self.header.setSpacing(10)
        self._titles = QVBoxLayout()
        self._titles.setSpacing(1)
        if title:
            label = QLabel(title)
            label.setObjectName("CardTitle")
            self._titles.addWidget(label)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("Hint")
            sub.setWordWrap(True)
            self._titles.addWidget(sub)
        self.header.addLayout(self._titles)
        self.header.addStretch(1)
        outer.addLayout(self.header)

        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        outer.addLayout(self.body)

    def add_header_widget(self, widget: QWidget) -> None:
        self.header.addWidget(widget)

    def add(self, widget: QWidget) -> None:
        self.body.addWidget(widget)

    def add_layout(self, layout: QLayout) -> None:
        self.body.addLayout(layout)


class Banner(QFrame):
    """A message strip. Severity is one of 'info', 'warning', 'ok', 'error'.

    The colour is decoration; the words carry the message.
    """

    def __init__(self, severity: str = "info", title: str = "", message: str = "",
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName(theme.SEVERITY_FRAME_OBJECT.get(severity, "BannerInfo"))
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(12, 10, 12, 10)
        self._layout.setSpacing(10)
        self._text = QVBoxLayout()
        self._text.setSpacing(2)
        self._title = plain_text(QLabel(title))
        self._title.setObjectName(theme.SEVERITY_TITLE_OBJECT.get(severity, "BannerTitleInfo"))
        self._title.setWordWrap(True)
        self._body = plain_text(QLabel(message))
        self._body.setWordWrap(True)
        self._text.addWidget(self._title)
        self._text.addWidget(self._body)
        self._layout.addLayout(self._text, 1)
        self._buttons = QHBoxLayout()
        self._layout.addLayout(self._buttons)
        self._set_visible(bool(title or message))

    def _set_visible(self, on: bool) -> None:
        self.setVisible(on)

    def update_message(self, severity: str, title: str, message: str) -> None:
        self.setObjectName(theme.SEVERITY_FRAME_OBJECT.get(severity, "BannerInfo"))
        self._title.setObjectName(
            theme.SEVERITY_TITLE_OBJECT.get(severity, "BannerTitleInfo"))
        self._title.setText(title)
        self._body.setText(message)
        self._body.setVisible(bool(message))
        self._set_visible(bool(title or message))
        _restyle(self)

    def add_action(self, text: str, callback: Callable[[], None]) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("Link")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda: callback())
        self._buttons.addWidget(button)
        return button


class StatTile(QFrame):
    """A single number with a caption, for a dashboard row."""

    def __init__(self, caption: str, value: str = "—", hint: str = "",
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(2)
        self._value = QLabel(value)
        self._value.setObjectName("BigNumber")
        self._value.setWordWrap(False)
        cap = QLabel(caption)
        cap.setObjectName("StatLabel")
        layout.addWidget(self._value)
        layout.addWidget(cap)
        self._hint = QLabel(hint)
        self._hint.setObjectName("Faint")
        self._hint.setWordWrap(True)
        self._hint.setVisible(bool(hint))
        layout.addWidget(self._hint)

    def set_value(self, value: str, hint: str = "") -> None:
        self._value.setText(value)
        self._hint.setText(hint)
        self._hint.setVisible(bool(hint))


class PageHeader(QWidget):
    """Title, one-line explanation, and an optional row of actions on the right."""

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        text = QVBoxLayout()
        text.setSpacing(2)
        self._title = QLabel(title)
        self._title.setObjectName("PageTitle")
        text.addWidget(self._title)
        self.subtitle = QLabel(subtitle)
        self.subtitle.setObjectName("PageSubtitle")
        self.subtitle.setWordWrap(True)
        self.subtitle.setVisible(bool(subtitle))
        text.addWidget(self.subtitle)
        row.addLayout(text)
        row.addStretch(1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        row.addLayout(self.actions)

    def add_action(self, button: QWidget) -> None:
        self.actions.addWidget(button)


class FieldRow(QWidget):
    """A label and its editor, aligned, with an optional unit suffix."""

    def __init__(self, label: str, editor: QWidget, hint: str = "",
                 parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        cap = QLabel(label)
        cap.setObjectName("SectionLabel")
        layout.addWidget(cap)
        layout.addWidget(editor)
        if hint:
            note = QLabel(hint)
            note.setObjectName("Faint")
            note.setWordWrap(True)
            layout.addWidget(note)


def scrollable(inner: QWidget) -> QScrollArea:
    """Wrap a page so long content scrolls instead of clipping."""
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setWidget(inner)
    return area


def key_value_grid(pairs: Iterable[tuple[str, str]], columns: int = 2) -> QGridLayout:
    """A compact read-only grid of 'label: value' pairs."""
    grid = QGridLayout()
    grid.setHorizontalSpacing(18)
    grid.setVerticalSpacing(6)
    for index, (key, value) in enumerate(pairs):
        row, col = divmod(index, columns)
        key_label = QLabel(key)
        key_label.setObjectName("StatLabel")
        value_label = QLabel(value)
        value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        grid.addWidget(key_label, row, col * 2)
        grid.addWidget(value_label, row, col * 2 + 1)
    return grid


def button(text: str, *, primary: bool = False, danger: bool = False,
           on_click: Callable[[], None] | None = None,
           tooltip: str = "", enabled: bool = True) -> QPushButton:
    b = QPushButton(text)
    if primary:
        b.setObjectName("Primary")
    elif danger:
        b.setObjectName("Danger")
    if tooltip:
        b.setToolTip(tooltip)
    b.setEnabled(enabled)
    if on_click is not None:
        b.clicked.connect(lambda: on_click())
    return b


def link_button(text: str, on_click: Callable[[], None]) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("Link")
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.clicked.connect(lambda: on_click())
    return b


def _restyle(widget: QWidget) -> None:
    """Re-apply the stylesheet after an object name changes (Qt needs prompting)."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def restyle(*widgets: QWidget) -> None:
    for widget in widgets:
        _restyle(widget)


# ---------------------------------------------------------------------------
# Messages. Always name the thing that happened.
# ---------------------------------------------------------------------------
def show_error(parent: QWidget | None, title: str, message: str,
               detail: str = "") -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle(title or "Something went wrong")
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(message)
    if detail:
        box.setDetailedText(detail)
    box.exec()


def show_info(parent: QWidget | None, title: str, message: str,
              detail: str = "") -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Information)
    box.setWindowTitle(title)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(message)
    if detail:
        box.setDetailedText(detail)
    box.exec()


def confirm(parent: QWidget | None, title: str, question: str,
            *, confirm_text: str = "Continue", cancel_text: str = "Cancel",
            destructive: bool = False) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning if destructive else QMessageBox.Icon.Question)
    box.setWindowTitle(title)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(question)
    yes = box.addButton(confirm_text, QMessageBox.ButtonRole.AcceptRole)
    box.addButton(cancel_text, QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(yes)
    box.exec()
    return box.clickedButton() is yes
