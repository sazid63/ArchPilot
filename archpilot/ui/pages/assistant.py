"""Assistant: an optional language-model front end to the same operations the UI uses.

Three promises this screen keeps visible:

* **Optional.** If no provider is set up, the page says so and points at Settings; every other
  screen keeps working unchanged.
* **Data, never code.** The model can only reply with a proposal drawn from a fixed list of
  building operations. ArchPilot validates each one against a strict schema and shows what will
  change *before* anything is applied. A proposal is never executed.
* **Nothing is dropped.** Suggestions the app refuses are shown with the reason, even when other
  suggestions in the same reply were accepted.
"""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout, QWidget

from archpilot.ai import ops
from archpilot.ai.providers import KIND_OPENAI_COMPATIBLE
from archpilot.ui import widgets
from archpilot.ui.page import Page

EXAMPLES = [
    "Make it 10 by 8 metres, two storeys.",
    "Add two more windows on each wall.",
    "Change the wall thickness to 300 mm.",
    "Use India prices.",
    "Note that the client wants a north-facing entrance.",
]


class AssistantPage(Page):
    key = "assistant"
    nav_label = "Assistant"
    title = "Assistant"
    subtitle = "Describe a change in plain words. You review it before anything is applied."
    requires_project = True

    def __init__(self, shell, parent: QWidget | None = None):
        super().__init__(shell, parent)
        self._proposal = None

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        header = widgets.PageHeader(self.title, self.subtitle)
        header.add_action(widgets.button("AI settings",
                                         on_click=lambda: self.shell.go_to("settings")))
        root.addWidget(header)

        self.status_banner = widgets.Banner()
        root.addWidget(self.status_banner)

        ask_card = widgets.Card("What would you like to change?")
        self.request = QPlainTextEdit()
        self.request.setPlaceholderText(
            "For example: make the building 12 by 9 metres, single storey, with a door on the "
            "south wall.")
        self.request.setMaximumHeight(110)
        ask_card.add(self.request)

        examples_row = QHBoxLayout()
        examples_row.setSpacing(6)
        examples_row.addWidget(QLabel("Try:"))
        for example in EXAMPLES:
            examples_row.addWidget(_chip(example, self._use_example))
        examples_row.addStretch(1)
        ask_card.add_layout(examples_row)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.ask_button = widgets.button("Ask the assistant", primary=True,
                                         on_click=self._ask)
        buttons.addWidget(self.ask_button)
        buttons.addStretch(1)
        ask_card.add_layout(buttons)
        root.addWidget(ask_card)

        # -- the reply and the proposal --------------------------------------
        reply_card = widgets.Card("The assistant's reply")
        self.reply = QPlainTextEdit()
        self.reply.setReadOnly(True)
        self.reply.setMaximumHeight(110)
        self.reply.setPlaceholderText("The assistant's words appear here.")
        reply_card.add(self.reply)
        root.addWidget(reply_card)

        proposal_card = widgets.Card(
            "Proposed changes", "Nothing has been applied yet. Review, then accept or discard.")
        self.proposal_list = QPlainTextEdit()
        self.proposal_list.setReadOnly(True)
        self.proposal_list.setPlaceholderText(
            "The list of changes the assistant is proposing appears here.")
        proposal_card.add(self.proposal_list)
        decision = QHBoxLayout()
        decision.setSpacing(8)
        self.apply_button = widgets.button("Apply these changes", primary=True,
                                           on_click=self._apply)
        self.discard_button = widgets.button("Discard", on_click=self._discard)
        decision.addWidget(self.apply_button)
        decision.addWidget(self.discard_button)
        decision.addStretch(1)
        proposal_card.add_layout(decision)
        root.addWidget(proposal_card, 1)

        guarantee = widgets.Banner(
            "info", "What the assistant can and cannot do",
            "It can only suggest changes from a fixed list of building operations — sizes, "
            "storeys, walls, openings, thicknesses, units and region. It cannot run code, "
            "cannot edit FreeCAD files, and cannot add structural detail. It produces "
            "rectangular footprints only.")
        root.addWidget(guarantee)

    # -- state ---------------------------------------------------------------
    def refresh(self) -> None:
        session = self.session
        if session is None:
            self._set_enabled(False)
            self.status_banner.update_message(
                "info", "No project open",
                "Open or create a project, then ask the assistant to change it.")
            return
        config = session.ai_config()
        if config.kind == KIND_OPENAI_COMPATIBLE and config.is_configured:
            key_state = "with an API key" if config.with_key_present() else "without an API key"
            self.status_banner.update_message(
                "ok", f"Assistant ready — {config.model}",
                f"Using {config.endpoint()} {key_state}. Everything here is optional: the "
                f"Design screen can make the same changes by hand.")
            self._set_enabled(True)
        else:
            self.status_banner.update_message(
                "warning", "The assistant is not set up",
                "ArchPilot works fully without it — every change it could suggest can be made "
                "on the Design screen. To enable it, open Settings and point ArchPilot at a "
                "local or online OpenAI-compatible model.")
            self._set_enabled(False)

    def _set_enabled(self, on: bool) -> None:
        self.ask_button.setEnabled(on)
        self.request.setEnabled(on)
        if not on:
            self._clear_proposal()

    # -- actions -------------------------------------------------------------
    def _use_example(self, text: str) -> None:
        self.request.setPlainText(text)

    def _ask(self) -> None:
        session = self.session
        if session is None:
            return
        text = self.request.toPlainText().strip()
        if not text:
            widgets.show_error(self, "Nothing to ask",
                               "Type what you would like to change first.")
            return

        def work(progress):
            progress("Asking the assistant…")
            return session.ask_ai(text)

        if self.shell.jobs.run(work, on_success=self._on_reply, on_error=self._on_error,
                               on_finished=lambda: self._set_busy(False)):
            self._set_busy(True)

    def _set_busy(self, busy: bool) -> None:
        self.ask_button.setEnabled(not busy)
        self.ask_button.setText("Asking…" if busy else "Ask the assistant")

    def _on_reply(self, proposal) -> None:
        self._proposal = proposal
        self.reply.setPlainText(proposal.reply_text or "(no words, just changes)")
        if proposal.error:
            self.proposal_list.setPlainText(f"The assistant could not be used:\n{proposal.error}")
            self._set_proposal_buttons(False)
            return
        if not proposal.operations:
            self.proposal_list.setPlainText(
                "The assistant did not propose any change. Try rephrasing, or describe the "
                "change in terms of size, storeys, walls, openings, thickness or region.")
            self._set_proposal_buttons(False)
            return

        # Dry-run the proposal against the current design so the user sees the real effect.
        dry = ops.apply_operations(self.session.spec if self.session else None,
                                   proposal.operations)
        lines: list[str] = []
        for description in dry.applied:
            lines.append(f"•  {description}")
        for name, reason in dry.rejected:
            lines.append(f"✗  {name} — refused: {reason}")
        for refusal in proposal.rejected:
            lines.append(f"✗  {refusal}")
        self.proposal_list.setPlainText("\n".join(lines))
        self._set_proposal_buttons(bool(dry.applied))

    def _set_proposal_buttons(self, can_apply: bool) -> None:
        self.apply_button.setEnabled(can_apply)
        self.discard_button.setEnabled(can_apply)

    def _apply(self) -> None:
        session = self.session
        proposal = self._proposal
        if session is None or proposal is None or not proposal.operations:
            return
        result = session.apply_operations(proposal.operations)
        if result.rejected:
            widgets.show_error(
                self, "Some changes were not applied",
                "\n".join(f"{name}: {reason}" for name, reason in result.rejected))
        applied = len(result.applied)
        self.shell.set_status(f"Assistant applied {applied} change(s).")
        self._clear_proposal()
        self.shell.refresh_pages()
        self.shell.go_to("design")

    def _discard(self) -> None:
        self._clear_proposal()
        self.shell.set_status("Proposal discarded. Nothing was changed.")

    def _clear_proposal(self) -> None:
        self._proposal = None
        self.proposal_list.setPlainText("")
        self.reply.setPlainText("")
        self._set_proposal_buttons(False)

    def _on_error(self, message: str, detail: str) -> None:
        self.proposal_list.setPlainText(message)
        self._set_proposal_buttons(False)
        self.shell.set_status("The assistant could not be reached.")


def _chip(text: str, on_click):
    button = widgets.button(text, on_click=lambda t=text: on_click(t))
    button.setToolTip("Use this example")
    return button
