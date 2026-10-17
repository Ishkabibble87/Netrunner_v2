"""Mirror the application game path through the real CLI and a local API.

Run from the repository root:
uv run --project clients/cli --with-editable . python -m unittest discover -s clients/cli/tests -v

Only the terminal is substituted: keyboard input, menus, HTTP requests, WebSocket
updates, and game resolution all run normally. Each step waits for a rendered
checkpoint instead of relying on sleeps or prerecorded game responses.
"""

from contextlib import contextmanager
from io import StringIO
from queue import Empty, Queue
import socket
from threading import Thread
from time import monotonic, sleep
import unittest
from unittest.mock import patch

from prompt_toolkit.application import Application, create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console
import uvicorn

from anr_cli import main as cli


@contextmanager
def running_api():
    """Serve the production app on an OS-assigned port for this scenario only."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(
            "api.app:create_app", factory=True, log_level="error",
        ))
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, name="cli-test-api", daemon=True)
        thread.start()
        try:
            deadline = monotonic() + 10
            while not server.started:
                if not thread.is_alive() or monotonic() >= deadline:
                    raise AssertionError("The test API did not start")
                sleep(0.01)
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            if thread.is_alive():
                raise AssertionError("The test API did not stop")


class TerminalOutput(DummyOutput):
    """Provide enough terminal space to read the board and all game choices."""

    def get_size(self):
        return Size(rows=45, columns=120)


class TerminalSession:
    """Run real CLI applications and expose rendered frames to the scenario.

    The run wrapper only observes rendering; the original application loop and
    keyboard bindings still execute. A queue lets the test await visible changes.
    """

    def __init__(self, api_url, keys):
        self.api_url = api_url
        self.keys = keys
        self.frames = Queue()
        self.last_frame = ""
        self.error = None
        self.thread = Thread(target=self.run, name="cli-test-terminal", daemon=True)

    def run(self):
        original_run = Application.run

        def observe_run(app, *args, **kwargs):
            app.after_render += self.capture_frame
            try:
                return original_run(app, *args, **kwargs)
            finally:
                app.after_render -= self.capture_frame

        try:
            with create_app_session(input=self.keys, output=TerminalOutput()):
                with patch.object(Application, "run", observe_run):
                    cli.run_menu(self.api_url)
        except BaseException as error:
            self.error = error
        finally:
            self.frames.put(None)

    def capture_frame(self, app):
        screen = app.renderer.last_rendered_screen
        if screen is not None and not app.is_done:
            size = app.output.get_size()
            self.frames.put("\n".join(
                "".join(screen.data_buffer[y][x].char for x in range(size.columns)).rstrip()
                for y in range(size.rows)
            ))

    def expect(self, text):
        deadline = monotonic() + 10
        while True:
            try:
                frame = self.frames.get(timeout=max(0, deadline - monotonic()))
            except Empty:
                raise AssertionError(
                    f"CLI did not show {text!r}. Last frame:\n{self.last_frame}"
                ) from self.error
            if frame is None:
                raise AssertionError(f"CLI exited before showing {text!r}") from self.error
            self.last_frame = frame
            if text in frame:
                return frame

    def send(self, keys):
        self.keys.send_text(keys)

    def stop(self):
        """Unblock input even when an assertion fails midway through a menu."""
        self.keys.close()
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            raise AssertionError("The test CLI did not stop")


class CliGameProgressTests(unittest.TestCase):
    """Follow the same Corp game path as the application test, from the keyboard."""

    def test_game_path(self):
        with running_api() as api_url, create_pipe_input() as keys:
            with patch.object(cli, "console", Console(file=StringIO())):
                self.terminal = TerminalSession(api_url, keys)
                self.terminal.thread.start()
                try:
                    self.select_deck()
                    self.select_opponent_and_start_game()
                    state = self.mulligan_while_opponent_keeps()
                    self.assert_corp_first_turn_started(state)
                    self.take_basic_actions()
                    self.leave_game_and_quit()
                finally:
                    self.terminal.stop()

    def select_deck(self):
        self.terminal.expect("New Game")
        self.terminal.send("1")
        self.terminal.expect("Play AI")
        self.terminal.send("2")
        frame = self.terminal.expect("Select a deck")
        self.assertIn("System Gateway Starter Corp", frame)
        self.terminal.send("1")
        # Reaching opponent selection proves the CLI loaded and accepted the deck.
        self.terminal.expect("Select an opponent")

    def select_opponent_and_start_game(self):
        self.assertIn("System Gateway Training Opponent", self.terminal.last_frame)
        self.terminal.send("1")
        frame = self.terminal.expect("Choose your opening hand")
        self.assertIn("The Syndicate: Profit over Principle", frame)
        self.assertIn("The Catalyst: Convention Breaker", frame)
        self.assertIn("5 credits · 5 cards in HQ · 29 cards in R&D · 0 clicks", frame)
        self.assertIn("5 credits · 5 cards in grip", frame)
        self.assertIn("> Keep opening hand", frame)
        self.assertIn("Mulligan · shuffle and draw five", frame)
        self.assertIn("Connected", frame)
        self.assertNotIn("Gain 1 credit", frame)

    def mulligan_while_opponent_keeps(self):
        self.terminal.send("j\r")
        frame = self.terminal.expect("Your action phase · turn 1")
        self.assertNotIn("Keep opening hand", frame)
        self.assertNotIn("Mulligan · shuffle and draw five", frame)
        self.assertIn("5 credits · 5 cards in grip", frame)
        return frame

    def assert_corp_first_turn_started(self, frame):
        self.assertIn("5 credits · 6 cards in HQ · 28 cards in R&D · 3 clicks", frame)
        self.assertIn("> Gain 1 credit", frame)
        self.assertIn("Draw 1 card", frame)
        self.assertIn("Purge virus counters · 3 clicks", frame)

    def take_basic_actions(self):
        self.terminal.send("j\r")
        frame = self.terminal.expect("5 credits · 7 cards in HQ · 27 cards in R&D · 2 clicks")
        self.assertIn("Your action phase · turn 1", frame)
        self.assertNotIn("Purge virus counters", frame)
        self.assertIn("> Gain 1 credit", frame)

        self.terminal.send("\r")
        frame = self.terminal.expect("6 credits · 7 cards in HQ · 27 cards in R&D · 1 clicks")
        self.assertIn("Your action phase · turn 1", frame)
        self.assertIn("5 credits · 5 cards in grip", frame)
        self.assertIn("Connected", frame)

    def leave_game_and_quit(self):
        self.terminal.send("q")
        self.terminal.expect("Play AI")
        self.terminal.send("3")
        self.terminal.expect("Join Game")
        self.terminal.send("3")
        self.terminal.thread.join(timeout=10)
        self.assertFalse(self.terminal.thread.is_alive(), "CLI did not quit")
        if self.terminal.error is not None:
            raise self.terminal.error


if __name__ == "__main__":
    unittest.main()
