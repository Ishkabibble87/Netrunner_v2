"""Board navigation must preserve focus and never trigger menu commands."""

import unittest

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import DummyInput
from prompt_toolkit.layout import HSplit, Layout
from prompt_toolkit.output import DummyOutput

from anr_cli.game_ui import GameScreen


class GameNavigationTests(unittest.TestCase):
    def setUp(self):
        self.submitted = []
        self.screen = GameScreen(
            {"legal_actions": [{"type": "gain_credit"}]}, {},
            submit_action=lambda command: self.submitted.append(command),
        )
        self.screen.app = Application(
            layout=Layout(HSplit([self.screen.board_pane, self.screen.menu_pane]),
                          focused_element=self.screen.menu_window),
            input=DummyInput(), output=DummyOutput(),
        )
        self.enterContext(set_app(self.screen.app))

    def test_board_arrows_and_pages_scroll_without_moving_menu_selection(self):
        screen = self.screen
        screen._focus_panel("board")
        selected = screen.level.selected
        screen._move(1)
        self.assertTrue(screen._board_focused())
        self.assertEqual(screen.board_pane.vertical_scroll, 1)
        screen._scroll(5)
        self.assertEqual(screen.board_pane.vertical_scroll, 6)
        screen._scroll(-5)
        screen._move(-1)
        self.assertEqual(screen.board_pane.vertical_scroll, 0)
        self.assertEqual(screen.level.selected, selected)
        self.assertIn("ACTIVE", screen._panel_title("BOARD", "board"))
        self.assertIn("BOARD ·", "".join(text for _, text in screen._footer_fragments()))

    def test_enter_and_escape_return_to_menu_without_activating_an_action(self):
        for handler in (self.screen._activate, self.screen._back):
            with self.subTest(handler=handler.__name__):
                self.screen._focus_panel("board")
                handler()
                self.assertFalse(self.screen._board_focused())
                self.assertEqual(self.submitted, [])
                self.assertIn("ACTIVE", self.screen._panel_title("MENU", "menu"))

    def test_menu_navigation_and_page_scroll_still_work(self):
        self.screen._move(1)
        self.assertEqual(self.screen.level.selected, 1)
        self.screen._scroll(5)
        self.assertEqual(self.screen.menu_pane.vertical_scroll, 5)
        self.assertFalse(self.screen.follow_selection)
        self.screen._move(-1)
        self.assertEqual(self.screen.level.selected, 0)
        self.assertEqual(self.screen.menu_pane.vertical_scroll, 0)
