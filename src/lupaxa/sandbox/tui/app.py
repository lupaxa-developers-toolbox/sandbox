"""Session browser, diff inspector, and export and apply screens.

The screens call the same session, diff, export, and apply functions as the CLI.
"""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option

from lupaxa.sandbox.operations.apply import apply_changes
from lupaxa.sandbox.operations.export import export_files
from lupaxa.sandbox.service import diff_text
from lupaxa.sandbox.sessions.store import SessionStore
from lupaxa.sandbox.workspace.changes import Change, compare_manifests
from lupaxa.sandbox.workspace.manifest import build_manifest
from lupaxa.sandbox.workspace.patterns import DEFAULT_TRACKING_EXCLUDES


class SandboxApp(App[None]):
    """Browse sessions and inspect changes without a second implementation."""

    BINDINGS = [
        Binding("e", "export", "Export"),
        Binding("a", "apply", "Apply"),
        Binding("c", "confirm_apply", "Confirm", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, data_dir: Path) -> None:
        super().__init__()
        self.data_dir = Path(data_dir)
        self.session_name: str | None = None
        self.selected_path: str | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield OptionList(id="sessions")
        yield Footer()

    def on_mount(self) -> None:
        widget = self.query_one("#sessions", OptionList)
        for session in SessionStore(self.data_dir).list_sessions():
            widget.add_option(Option(session.name, id=session.name))
        if widget.option_count:
            widget.highlighted = 0

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "sessions" or event.option_id is None:
            return
        self.session_name = event.option_id
        self.push_screen(DetailScreen(self.data_dir, event.option_id))

    def action_export(self) -> None:
        if isinstance(self.screen, ExportScreen):
            return
        if self.session_name is None or self.selected_path is None:
            return
        self.push_screen(ExportScreen(self.data_dir, self.session_name, self.selected_path))

    def action_apply(self) -> None:
        if self.session_name is None or isinstance(self.screen, ApplyScreen):
            return
        self.push_screen(ApplyScreen(self.data_dir, self.session_name))

    def action_confirm_apply(self) -> None:
        if isinstance(self.screen, ApplyScreen):
            self.screen.confirm()


class DetailScreen(Screen[None]):
    """Session overview, change list, and execution logs."""

    def __init__(self, data_dir: Path, name: str) -> None:
        super().__init__()
        self.data_dir = data_dir
        self.session_name = name
        self.changes: list[Change] = []

    def compose(self) -> ComposeResult:
        session = SessionStore(self.data_dir).get(self.session_name)
        self.changes = _session_changes(self.data_dir, self.session_name)
        overview = f"{session.name}  {session.status}\n{session.source}"
        yield Static(overview, id="overview")
        yield OptionList(id="changes")
        yield Input(placeholder="Filter changes", id="filter")
        yield OptionList(id="executions")
        yield Static("", id="log")

    def on_mount(self) -> None:
        self._fill_changes("")
        executions = self.query_one("#executions", OptionList)
        session = SessionStore(self.data_dir).get(self.session_name)
        for record in session.executions:
            label = " ".join(record.command) or record.id
            executions.add_option(Option(f"{record.exit_code}  {label}", id=record.id))
        if self.changes:
            _app(self).selected_path = self.changes[0].path

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter":
            self._fill_changes(event.value)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "changes" and event.option_id is not None:
            _app(self).selected_path = event.option_id
            text = _diff_for(self.data_dir, self.session_name)
            self.app.push_screen(DiffScreen(text))
            return
        if event.option_list.id == "executions" and event.option_id is not None:
            text = _execution_text(self.data_dir, self.session_name, event.option_id)
            self.query_one("#log", Static).update(text)

    def _fill_changes(self, text: str) -> None:
        widget = self.query_one("#changes", OptionList)
        widget.clear_options()
        needle = text.strip()
        for change in self.changes:
            if needle and needle not in change.path:
                continue
            widget.add_option(Option(f"{change.category}  {change.path}", id=change.path))
        if widget.option_count:
            widget.highlighted = 0


class DiffScreen(Screen[None]):
    """One change set, rendered by the shared diff helper."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self._text = text or "(no text diff)"

    def compose(self) -> ComposeResult:
        yield Static(self._text, id="diff")


class ExportScreen(Screen[None]):
    """Copy the selected file. Enter confirms the copy."""

    def __init__(self, data_dir: Path, name: str, path: str) -> None:
        super().__init__()
        self.data_dir = data_dir
        self.session_name = name
        self.path = path

    def compose(self) -> ComposeResult:
        yield Static(f"Export {self.path}. Enter confirms the copy.", id="export-help")
        yield Input(placeholder="Destination directory", id="export-dest")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "export-dest" or not event.value:
            return
        export_files(
            name=self.session_name,
            data_dir=self.data_dir,
            include=[self.path],
            destination=Path(event.value),
            yes=True,
        )
        self.query_one("#export-help", Static).update(f"Exported {self.path}")


class ApplyScreen(Screen[None]):
    """Preview an apply. c writes it. Conflicts stay on screen and are not forced."""

    def __init__(self, data_dir: Path, name: str) -> None:
        super().__init__()
        self.data_dir = data_dir
        self.session_name = name
        self._confirmed = False

    def compose(self) -> ComposeResult:
        preview = _apply_preview(self.data_dir, self.session_name)
        yield Static(preview, id="apply-preview")

    def confirm(self) -> None:
        if self._confirmed:
            return
        self._confirmed = True
        result = apply_changes(
            name=self.session_name,
            data_dir=self.data_dir,
            select_all=True,
            dry_run=False,
        )
        lines = [f"{item.action}  {item.path}  {item.reason}" for item in result.items]
        self.query_one("#apply-preview", Static).update("\n".join(lines) or "(nothing to apply)")


def _app(screen: Screen[None]) -> SandboxApp:
    app = screen.app
    assert isinstance(app, SandboxApp)
    return app


def _session_changes(data_dir: Path, name: str) -> list[Change]:
    store = SessionStore(data_dir)
    session = store.get(name)
    current = build_manifest(session.workspace, exclude=[])
    change_set = compare_manifests(store.baseline(session), current, DEFAULT_TRACKING_EXCLUDES)
    return change_set.changes


def _diff_for(data_dir: Path, name: str) -> str:
    store = SessionStore(data_dir)
    session = store.get(name)
    baseline = store.baseline(session)
    current = build_manifest(session.workspace, exclude=[])
    change_set = compare_manifests(baseline, current, DEFAULT_TRACKING_EXCLUDES)
    return diff_text(session, baseline, change_set)


def _execution_text(data_dir: Path, name: str, execution_id: str) -> str:
    session = SessionStore(data_dir).get(name)
    for record in session.executions:
        if record.id == execution_id:
            return f"stdout\n{record.stdout}\nstderr\n{record.stderr}"
    return ""


def _apply_preview(data_dir: Path, name: str) -> str:
    result = apply_changes(name=name, data_dir=data_dir, select_all=True, dry_run=True)
    lines = [f"{item.action}  {item.path}  {item.reason}" for item in result.items]
    lines.append("Press c to apply. Conflicts are left unchanged.")
    return "\n".join(lines)
