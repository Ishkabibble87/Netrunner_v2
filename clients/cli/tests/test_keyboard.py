"""Exercise terminal key decoding and navigation through the actual CLI apps."""

import sys
import unittest
from unittest.mock import patch

from prompt_toolkit.application import Application, create_app_session
from prompt_toolkit.application.current import set_app
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import DummyInput, create_pipe_input
from prompt_toolkit.input.vt100_parser import Vt100Parser
from prompt_toolkit.keys import Keys
from prompt_toolkit.output import DummyOutput

from anr_cli.game_ui import GameScreen
from anr_cli.main import choose
from anr_cli.terminal import get_terminal_input


class MenuKeyboardTests(unittest.TestCase):
    def test_menu_selection_and_shortcuts(self):
        for text, expected in (
            ("\x1b[B\r", 1),  # Down.
            ("\x1b[A\r", 2),  # Up wraps to last choice.
            ("\x1b[B\x1b[A\r", 0),
            ("\x1bOB\r", 1),  # Application cursor mode.
            ("\x1bOA\r", 2),
            ("j\r", 1),
            ("k\r", 2),
            ("\x0e\r", 1),
            ("\x10\r", 2),
            ("3", 2),
            ("q", None),
            ("\x03", None),
        ):
            with self.subTest(text=repr(text)):
                with create_pipe_input() as keys, create_app_session(
                    input=keys, output=DummyOutput()
                ):
                    self.assertIs(get_terminal_input(), keys)
                    keys.send_text(text)
                    self.assertEqual(choose("Choose", ["A", "B", "C"]), expected)

    def test_escape_cancels_menu(self):
        with create_pipe_input() as keys, create_app_session(
            input=keys, output=DummyOutput()
        ):
            keys.send_text("\x1b")
            self.assertIsNone(choose("Choose", ["A", "B"]))

    def test_game_arrows_browse_hand_and_cards(self):
        screen = GameScreen(
            {
                "viewer_role": "runner",
                "you": {
                    "role": "runner",
                    "hand": [{"title": "First card"}, {"title": "Second card"}],
                },
            },
            {},
        )
        with create_pipe_input() as keys, create_app_session(
            input=keys, output=DummyOutput()
        ):
            # Inspect -> hand -> second card -> previous card -> next card.
            keys.send_text("\r" + "\x1b[B" * 4 + "\r\x1b[B\r\x1b[A\x1b[Bq")
            screen.run()
        self.assertEqual(screen.level.kind, "card")
        self.assertEqual(screen.level.data["index"], 1)
        self.assertEqual(screen.level.data["cards"][1]["title"], "Second card")


class SizedOutput(DummyOutput):
    def __init__(self, columns=80, rows=24):
        self.size = Size(rows=rows, columns=columns)

    def get_size(self):
        return self.size


class GameRenderingTests(unittest.TestCase):
    def make_screen(self):
        return GameScreen(
            {
                "viewer_role": "runner",
                "you": {
                    "role": "runner",
                    "identity": {"title": "The Catalyst"},
                    "credits": 7,
                    "clicks_remaining": 4,
                    "hand": [{"title": "Long card", "definition_id": "long"}],
                },
                "opponent": {
                    "role": "corp",
                    "identity": {"title": "The Syndicate"},
                    "credits": 5,
                    "hand_count": 5,
                    "deck_count": 29,
                },
                "servers": ["hq", "rd", "archives"],
            },
            {"long": {"printed_text": "Some long card text. " * 200}},
        )

    def exercise(self, screen, output, callback):
        def run(app):
            with set_app(app):
                def render():
                    app.render_counter += 1
                    app.renderer.render(app, app.layout)
                    canvas = app.renderer._last_screen
                    return "\n".join(
                        "".join(canvas.data_buffer[y][x].char for x in range(output.size.columns))
                        for y in range(output.size.rows)
                    )

                callback(app, render)

        with create_app_session(input=DummyInput(), output=output):
            with patch.object(Application, "run", autospec=True, side_effect=run):
                screen.run()

    def test_narrow_board_shows_both_players(self):
        screen = self.make_screen()

        def check(app, render):
            text = render()
            for expected in ("The Catalyst", "The Syndicate", "7 credits", "5 credits", "29 cards in R&D"):
                self.assertIn(expected, text)

        self.exercise(screen, SizedOutput(), check)

    def test_selection_stays_visible_when_menu_and_terminal_size_change(self):
        screen = self.make_screen()
        output = SizedOutput()

        def check(app, render):
            render()
            screen._activate()
            for _ in range(4):
                screen._move(1)
            self.assertIn("> Your hand", render())
            self.assertGreater(screen.menu_pane.vertical_scroll, 0)
            output.size = Size(rows=16, columns=50)
            self.assertIn("> Your hand", render())
            output.size = Size(rows=24, columns=120)
            self.assertIn("> Your hand", render())

        self.exercise(screen, output, check)

    def test_wrapped_card_text_pages_to_the_end_and_back(self):
        screen = self.make_screen()
        screen._activate()
        screen._move(4)
        screen._activate()
        screen._activate()

        def check(app, render):
            before = render()
            screen._scroll(5)
            after = render()
            self.assertNotEqual(before, after)
            self.assertGreater(screen.menu_pane.vertical_scroll, 0)
            for _ in range(100):
                screen._scroll(5)
                last_page = render()
            self.assertIn("IN THIS GAME", last_page)
            bottom = screen.menu_pane.vertical_scroll
            screen._scroll(5)
            render()
            self.assertEqual(screen.menu_pane.vertical_scroll, bottom)
            screen._scroll(-5)
            render()
            self.assertLess(screen.menu_pane.vertical_scroll, bottom)
            screen._back()
            self.assertIn("> Long card", render())

        self.exercise(screen, SizedOutput(), check)

    def test_board_can_scroll_when_its_content_wraps(self):
        screen = self.make_screen()
        output = SizedOutput(columns=40)

        def check(app, render):
            before = render()
            app.layout.focus(screen.board_window)
            screen._scroll(5)
            self.assertNotEqual(before, render())
            self.assertGreater(screen.board_pane.vertical_scroll, 0)
            for _ in range(20):
                screen._scroll(5)
                last_page = render()
            self.assertIn("Runner: no agendas scored", last_page)
            app.layout.focus(screen.menu_window)
            output.size = Size(rows=24, columns=80)
            self.assertIn("The Catalyst", render())

        self.exercise(screen, output, check)


@unittest.skipUnless(sys.platform == "win32", "Native Windows console records")
class WindowsKeyboardTests(unittest.TestCase):
    def parse_records(self, records):
        from ctypes.wintypes import DWORD
        from anr_cli.terminal import _WindowsKeyReader

        reader = _WindowsKeyReader.__new__(_WindowsKeyReader)
        parsed = []
        parser = Vt100Parser(parsed.append)
        for text in reader._get_keys(DWORD(len(records)), records):
            parser.feed(text)
        parser.flush()
        return parsed

    def records(self, events):
        from prompt_toolkit.win32_types import INPUT_RECORD

        records = (INPUT_RECORD * len(events))()
        for record, (virtual_key, text, down, modifiers) in zip(records, events):
            record.EventType = 1
            event = record.Event.KeyEvent
            event.KeyDown = down
            event.RepeatCount = 1
            event.VirtualKeyCode = virtual_key
            event.uChar.UnicodeChar = text
            event.ControlKeyState = modifiers
        return records

    def test_native_navigation_keys_with_no_character(self):
        for virtual_key, expected in (
            (38, Keys.Up),
            (40, Keys.Down),
            (33, Keys.PageUp),
            (34, Keys.PageDown),
        ):
            with self.subTest(key=virtual_key):
                parsed = self.parse_records(self.records([(virtual_key, "\x00", 1, 0)]))
                self.assertEqual([key.key for key in parsed], [expected])

    def test_vt100_arrows_still_decode(self):
        for sequence, expected in (("\x1b[A", Keys.Up), ("\x1bOB", Keys.Down)):
            with self.subTest(sequence=repr(sequence)):
                parsed = self.parse_records(self.records([(0, char, 1, 0) for char in sequence]))
                self.assertEqual([key.key for key in parsed], [expected])

    def test_native_modifiers_and_key_releases(self):
        parsed = self.parse_records(
            self.records([(38, "\x00", 1, 0x10), (38, "\x00", 0, 0x10)])
        )
        self.assertEqual([key.key for key in parsed], [Keys.ShiftUp])

    def test_text_enter_and_escape_still_decode(self):
        parsed = self.parse_records(
            self.records([(0, char, 1, 0) for char in "j\r\x1b"])
        )
        self.assertEqual([key.key for key in parsed], ["j", Keys.ControlM, Keys.Escape])


if __name__ == "__main__":
    unittest.main()
