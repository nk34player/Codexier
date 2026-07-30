from __future__ import annotations

from textual import events, on
from textual.message import Message
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, ProgressBar, RichLog, Static


class WindowsProgressScreen(ModalScreen[None]):
    """Uncancellable progress view; the close button appears only after work ends."""

    CSS = """
    WindowsProgressScreen { align: center middle; background: rgba(0, 0, 0, 0.65); }
    #progress-shell { width: 88%; height: 80%; padding: 1 2; border: round #3b82f6; background: #131d38; }
    #progress-title { color: #8be9fd; text-style: bold; }
    #progress-status { height: 2; color: #c7d2fe; }
    #progress-log { height: 1fr; margin-top: 1; border: round #263b68; background: #080d19; }
    #progress-close { display: none; margin-top: 1; background: #374151; color: white; }
    .error { color: #ff6b8a; }
    """

    def __init__(self, title: str):
        super().__init__()
        self.title = title
        self.finished = False

    def compose(self) -> ComposeResult:
        with Vertical(id="progress-shell"):
            yield Static(self.title, id="progress-title")
            yield ProgressBar(total=100, show_eta=False, id="progress-bar")
            yield Static("Starting …", id="progress-status")
            yield RichLog(id="progress-log", wrap=True, markup=True)
            yield Button("Close", id="progress-close")

    def on_key(self, event: events.Key) -> None:
        if not self.finished:
            event.stop()

    def update_progress(self, percent: int, detail: str) -> None:
        self.query_one("#progress-bar", ProgressBar).update(progress=percent)
        self.query_one("#progress-status", Static).update(f"{percent:3d}%  {detail}")
        self.query_one("#progress-log", RichLog).write(
            f"[cyan]{percent:3d}%[/cyan] {detail}", scroll_end=True
        )

    def finish(self, message: str, *, error: bool = False) -> None:
        self.finished = True
        self.query_one("#progress-bar", ProgressBar).update(progress=100)
        status = self.query_one("#progress-status", Static)
        status.update(f"100%  {message}")
        self.query_one("#progress-log", RichLog).write(
            f"[{'red' if error else 'green'}]100%[/] {message}", scroll_end=True
        )
        if error:
            status.add_class("error")
            self.query_one("#progress-log", RichLog).write(
                f"[red]ERROR[/red] {message}", scroll_end=True
            )
        self.query_one("#progress-close", Button).display = True
        self.query_one("#progress-close", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "progress-close" and self.finished:
            self.dismiss(None)


class ConfirmationDialog(ModalScreen[bool]):
    """Arrow-key navigation for two-button confirmation dialogs."""

    def on_key(self, event: events.Key) -> None:
        if event.key not in {"up", "down", "left", "right"}:
            return
        buttons = tuple(self.query("Button"))
        if self.focused not in buttons or len(buttons) < 2:
            return
        buttons[1 - buttons.index(self.focused)].focus()
        event.stop()


class RestoreConfirmScreen(ConfirmationDialog):
    CSS = """
    RestoreConfirmScreen { align: center middle; background: rgba(0, 0, 0, 0.65); }
    #restore-confirm { width: 76%; height: auto; padding: 1 2; border: round #3b82f6; background: #131d38; }
    #restore-actions { height: auto; margin-top: 1; }
    Button { width: 1fr; margin-right: 1; background: #2563eb; color: white; }
    #cancel { background: #374151; }
    """

    def __init__(self, detail: str):
        super().__init__()
        self.detail = detail

    def compose(self) -> ComposeResult:
        with Vertical(id="restore-confirm"):
            yield Static("RESTORE APPLICATION BACKUP")
            yield Static(
                f"{self.detail}\n\nThis replaces the current application archive. "
                "The selected backup will not be modified."
            )
            with Horizontal(id="restore-actions"):
                yield Button("Restore backup", id="restore", variant="error")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "restore")


class DeleteConfirmScreen(ConfirmationDialog):
    CSS = """
    DeleteConfirmScreen { align: center middle; background: rgba(0, 0, 0, 0.65); }
    #delete-confirm { width: 76%; height: auto; padding: 1 2; border: round #ef4444; background: #131d38; }
    #delete-actions { height: auto; margin-top: 1; }
    Button { width: 1fr; margin-right: 1; background: #b91c1c; color: white; }
    #cancel { background: #374151; }
    """

    def __init__(self, detail: str):
        super().__init__()
        self.detail = detail

    def compose(self) -> ComposeResult:
        with Vertical(id="delete-confirm"):
            yield Static("DELETE APPLICATION BACKUP")
            yield Static(
                f"{self.detail}\n\nThis permanently deletes the selected backup. "
                "It cannot be restored from Codexier."
            )
            with Horizontal(id="delete-actions"):
                yield Button("Delete permanently", id="delete", variant="error")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "delete")


class ForceCloseConfirmScreen(ConfirmationDialog):
    CSS = """
    ForceCloseConfirmScreen { align: center middle; background: rgba(0, 0, 0, 0.65); }
    #force-close-confirm { width: 76%; height: auto; padding: 1 2; border: round #ef4444; background: #131d38; }
    #force-close-actions { height: auto; margin-top: 1; }
    Button { width: 1fr; margin-right: 1; background: #b91c1c; color: white; }
    #cancel { background: #374151; }
    """

    def __init__(self, pids: tuple[int, ...]):
        super().__init__()
        self.pids = pids

    def compose(self) -> ComposeResult:
        with Vertical(id="force-close-confirm"):
            yield Static("CHATGPT IS STILL RUNNING")
            yield Static(
                "ChatGPT must be closed before patching. "
                f"Forcefully kill the detected ChatGPT processes (PIDs: {', '.join(map(str, self.pids))}) "
                "and continue applying patches?\n\nUnsaved work may be lost."
            )
            with Horizontal(id="force-close-actions"):
                yield Button("Force kill and apply patches", id="force", variant="error")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "force")


class ReapplyConfirmScreen(ConfirmationDialog):
    CSS = """
    ReapplyConfirmScreen { align: center middle; background: rgba(0, 0, 0, 0.65); }
    #reapply-confirm { width: 76%; height: auto; padding: 1 2; border: round #f59e0b; background: #131d38; }
    #reapply-actions { height: auto; margin-top: 1; }
    Button { width: 1fr; margin-right: 1; background: #b91c1c; color: white; }
    #cancel { background: #374151; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="reapply-confirm"):
            yield Static("OLD PATCH VERSION DETECTED")
            yield Static(
                "You are running an old version of the desktop patches. "
                "Re-apply the latest patches now, or skip to keep the "
                "current install."
            )
            with Horizontal(id="reapply-actions"):
                yield Button("Reapply", id="reapply", variant="error")
                yield Button("Skip", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "reapply")
