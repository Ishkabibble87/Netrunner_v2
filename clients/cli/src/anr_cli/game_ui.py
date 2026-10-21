"""Keyboard-driven game inspector and player commands through the API."""

from __future__ import annotations

from typing import Any, Callable

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import Dimension, Layout
from prompt_toolkit.layout.containers import (
    ConditionalContainer,
    HSplit,
    VSplit,
    Window,
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.scrollable_pane import ScrollablePane
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Frame

from .action_menu import build_action_choices
from .game_rendering import (
    GameRenderer,
    MenuChoice,
    MenuLevel,
    installed_details,
    players_by_role,
    server_label,
)
from .navigation import navigation_bindings
from .session import SessionConnectionError
from .terminal import get_terminal_input

class GameScreen:
    """Show private game snapshots and offer commands from the server's view."""

    wide_columns = 106

    def __init__(
        self,
        state: dict[str, Any],
        card_catalog: dict[str, dict[str, Any]],
        *,
        connection_status: Callable[[], str] | None = None,
        state_provider: Callable[[], dict[str, Any]] | None = None,
        submit_action: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.state = state
        self.card_catalog = card_catalog
        self.renderer = GameRenderer(card_catalog)
        self.connection_status = connection_status
        self.state_provider = state_provider
        self.submit_action = submit_action
        self.command_error: str | None = None
        self.you = state.get("you", {})
        self.opponent = state.get("opponent", {})
        self.viewer_role = str(state.get("viewer_role", self.you.get("role", "")))
        self.hand = self.you.get("hand", [])
        self.follow_selection = True
        self.levels = [
            MenuLevel(
                "Game",
                "root",
                [
                    MenuChoice("Inspect cards · read only", "inspect"),
                    MenuChoice("Leave game", "exit"),
                ],
            )
        ]
        self.levels[0].choices = self._root_choices()
        self.app: Application[None] | None = None
        self.board_window = self._board_window()
        self.menu_window = self._menu_window()
        self.board_pane = ScrollablePane(
            self.board_window,
            keep_cursor_visible=False,
            keep_focused_window_visible=False,
        )
        self.menu_pane = ScrollablePane(
            self.menu_window,
            keep_cursor_visible=Condition(lambda: self.follow_selection),
            keep_focused_window_visible=False,
        )

    @property
    def level(self) -> MenuLevel:
        return self.levels[-1]

    def _root_choices(self) -> list[MenuChoice]:
        actions = build_action_choices(
            self.state,
            self.card_catalog,
            submit_action_available=self.submit_action is not None,
        )
        return actions + [
            MenuChoice("History · read only", "history"),
            MenuChoice("Inspect cards · read only", "inspect"),
            MenuChoice("Leave game", "exit"),
        ]

    def update_state(self, state: dict[str, Any]) -> None:
        """Refresh the board and discard inspector references to the previous hand."""
        if state == self.state:
            return
        viewing_history = bool(getattr(self, "levels", None) and self.level.kind == "history")
        self.state = state
        self.you = state.get("you", {})
        self.opponent = state.get("opponent", {})
        self.viewer_role = str(state.get("viewer_role", self.you.get("role", "")))
        self.hand = self.you.get("hand", [])
        self.levels = [MenuLevel("Game", "root", self._root_choices())]
        if viewing_history:
            self.levels.append(MenuLevel("History", "history"))
        else:
            self._reset_menu_scroll()
        self._invalidate()

    def _refresh_state(self, app: Application) -> None:
        if self.state_provider is not None:
            self.update_state(self.state_provider())

    def run(self) -> None:
        wide = Condition(lambda: get_app().output.get_size().columns >= self.wide_columns)
        wide_layout = VSplit(
            [
                Frame(self.board_pane, title="GAME BOARD", width=Dimension(weight=3)),
                Frame(self.menu_pane, title="STATUS / MENU", width=Dimension(weight=2)),
            ],
            padding=1,
        )
        narrow_layout = HSplit(
            [
                Frame(self.board_pane, title="BOARD", height=Dimension(preferred=10, min=4, max=12)),
                Frame(self.menu_pane, title="STATUS / MENU", height=Dimension(min=6, weight=1)),
            ]
        )
        root = HSplit(
            [
                Window(
                    FormattedTextControl(self._header_fragments),
                    height=1,
                    style="class:header",
                ),
                ConditionalContainer(wide_layout, filter=wide),
                ConditionalContainer(narrow_layout, filter=~wide),
                Window(
                    FormattedTextControl(self._footer_fragments),
                    height=1,
                    style="class:footer",
                ),
            ]
        )

        bindings = navigation_bindings(
            self._move, self._activate, self._back, lambda: get_app().exit()
        )

        @bindings.add("pagedown")
        def scroll_down(event: Any) -> None:
            self._scroll(5)

        @bindings.add("pageup")
        def scroll_up(event: Any) -> None:
            self._scroll(-5)

        @bindings.add("tab")
        def switch_panel(event: Any) -> None:
            window = (
                self.menu_window
                if event.app.layout.current_window is self.board_window
                else self.board_window
            )
            event.app.layout.focus(window)

        self.app = Application(
            input=get_terminal_input(),
            layout=Layout(root, focused_element=self.menu_window),
            key_bindings=bindings,
            style=Style.from_dict(
                {
                    "header": "bold ansicyan",
                    "footer": "ansibrightblack",
                    "section": "bold ansiyellow",
                    "selected": "reverse bold",
                    "muted": "ansibrightblack",
                    "card-title": "bold ansigreen",
                }
            ),
            full_screen=True,
            refresh_interval=1 if self.connection_status is not None or self.state_provider is not None else None,
            before_render=self._refresh_state,
        )
        self.app.run()

    def _board_window(self) -> Window:
        return Window(
            FormattedTextControl(
                lambda: self._board_fragments(
                    compact=get_app().output.get_size().columns < self.wide_columns
                ),
                focusable=True,
                show_cursor=False,
            ),
            wrap_lines=True,
        )

    def _menu_window(self) -> Window:
        return Window(
            FormattedTextControl(self._menu_fragments, focusable=True, show_cursor=False),
            wrap_lines=True,
        )

    def _set_render_context(self) -> None:
        self.renderer.set_context(
            self.state,
            self.viewer_role,
            self.levels,
            self.connection_status,
            self.command_error,
        )

    def _header_fragments(self) -> list[tuple[str, str]]:
        self._set_render_context()
        return self.renderer._header_fragments()

    def _footer_fragments(self) -> list[tuple[str, str]]:
        self._set_render_context()
        return self.renderer._footer_fragments()

    def _board_fragments(self, *, compact: bool) -> list[tuple[str, str]]:
        self._set_render_context()
        return self.renderer._board_fragments(compact=compact)

    def _menu_fragments(self) -> list[tuple[str, str]]:
        self._set_render_context()
        return self.renderer._menu_fragments()

    def _scroll(self, amount: int) -> None:
        pane = (
            self.board_pane
            if get_app().layout.current_window is self.board_window
            else self.menu_pane
        )
        if pane is self.menu_pane:
            self.follow_selection = False
        pane.vertical_scroll = max(0, pane.vertical_scroll + amount)
        self._invalidate()

    @staticmethod
    def _server_label(server: Any) -> str:
        return server_label(server)

    def _installed_details(self, card: dict[str, Any]) -> str:
        return installed_details(self.state, card)

    def _selection_card_label(self, card: dict[str, Any]) -> str:
        """Distinguish installed targets by their public location and counters."""
        installed = next((c for c in self.state.get("installed_cards", [])
                          if c["instance_id"] == card["instance_id"]), None)
        if installed is None:
            return card["title"]
        location = self._server_label(installed["server_id"]) if installed.get("server_id") is not None else "rig"
        return f"{card['title']} · {location}{self._installed_details(installed)}"

    def _refresh_discard_choices(self) -> None:
        """Collect a local selection and send it as one simultaneous discard."""
        data = self.level.data
        selected = data["selected"]
        eligible = data["action"]["eligible_card_instance_ids"]
        cards = self.state.get("selection_cards", []) if data["action"]["type"] == "select_effect_cards" else self.hand
        self.level.choices = [MenuChoice(
            f"{'[x]' if card['instance_id'] in selected else '[ ]'} {self._selection_card_label(card)}",
            "toggle_discard", card["instance_id"],
        ) for card in cards if card["instance_id"] in eligible]
        if len(selected) == data["action"]["required_count"] or data["action"].get("up_to"):
            self.level.choices.append(MenuChoice("Confirm selection", "action", {
                "type": data["action"]["type"], "card_instance_ids": list(selected),
            }))
        if data["action"].get("optional"):
            self.level.choices.append(MenuChoice("Decline", "action", {
                "type": data["action"]["type"], "card_instance_ids": [],
            }))
        self.level.choices.append(MenuChoice("Back", "back"))
        self._invalidate()

    def _move(self, direction: int) -> None:
        self._reset_menu_scroll()
        if self.level.kind == "card":
            cards = self.level.data["cards"]
            if cards:
                self.level.data["index"] = (self.level.data["index"] + direction) % len(cards)
        elif self.level.choices:
            self.level.selected = (self.level.selected + direction) % len(self.level.choices)
        self._invalidate()

    def _activate(self) -> None:
        if self.state_provider is not None:
            self.update_state(self.state_provider())
        level = self.level
        if level.kind == "card" or not level.choices:
            return
        choice = level.choices[level.selected]
        handlers: dict[str, Callable[[MenuChoice], None]] = {
            "exit": self._exit_game,
            "back": lambda _choice: self._back(),
            "action": self._submit_action_choice,
            "discard": self._begin_discard,
            "toggle_discard": self._toggle_discard,
            "history": self._open_history,
            "inspect": self._open_inspector,
            "identity": self._open_identity,
            "rig": self._open_rig,
            "installed_card": self._open_installed_card,
            "servers": self._open_servers,
            "server": self._open_server,
            "hand": self._open_hand,
            "card": self._open_card,
        }
        handler = handlers.get(choice.kind)
        if handler is not None:
            handler(choice)

    def _exit_game(self, _choice: MenuChoice) -> None:
        if self.app is not None:
            self.app.exit()

    def _submit_action_choice(self, choice: MenuChoice) -> None:
        try:
            if self.submit_action is None:
                return
            state = self.submit_action(choice.value)
        except (SessionConnectionError, ValueError, OSError) as error:
            self.command_error = str(error)
        else:
            self.command_error = None
            self.update_state(state)
        self._invalidate()

    def _begin_discard(self, choice: MenuChoice) -> None:
        self._push(
            MenuLevel(
                "Choose cards",
                "discard",
                data={"action": choice.value, "selected": []},
            )
        )
        self._refresh_discard_choices()

    def _toggle_discard(self, choice: MenuChoice) -> None:
        selected = self.level.data["selected"]
        if choice.value in selected:
            selected.remove(choice.value)
        elif len(selected) < self.level.data["action"]["required_count"]:
            selected.append(choice.value)
        self._refresh_discard_choices()

    def _open_history(self, _choice: MenuChoice) -> None:
        self._push(MenuLevel("History", "history"))

    def _open_inspector(self, _choice: MenuChoice) -> None:
        corp, runner = players_by_role(self.you, self.opponent)
        corp_identity = corp.get("identity", {})
        runner_identity = runner.get("identity", {})
        self._push(
            MenuLevel(
                "Inspect cards",
                "inspect",
                [
                    MenuChoice(
                        f"Corp ID · {corp_identity.get('title', 'Unknown identity')}",
                        "identity",
                        {"player": "Corp", "card": corp_identity},
                    ),
                    MenuChoice(
                        f"Runner ID · {runner_identity.get('title', 'Unknown identity')}",
                        "identity",
                        {"player": "Runner", "card": runner_identity},
                    ),
                    MenuChoice("Servers", "servers"),
                    MenuChoice("Rig", "rig"),
                    MenuChoice("Your hand", "hand"),
                    MenuChoice("Back", "back"),
                ],
            )
        )

    def _open_identity(self, choice: MenuChoice) -> None:
        player = str(choice.value["player"])
        self._push(
            MenuLevel(
                f"{player} identity",
                "card",
                data={
                    "cards": [choice.value["card"]],
                    "index": 0,
                    "location": f"{player} identity",
                },
            )
        )

    def _open_rig(self, _choice: MenuChoice) -> None:
        cards = [
            card for card in self.state.get("installed_cards", [])
            if card.get("area") == "rig" and card.get("card")
        ]
        choices = [
            MenuChoice(
                f"{card['card']['title']}{self._installed_details(card)}",
                "installed_card",
                card["card"],
            )
            for card in cards
        ]
        self._push(MenuLevel("Rig", "rig", choices + [MenuChoice("Back", "back")]))

    def _open_installed_card(self, choice: MenuChoice) -> None:
        self._push(
            MenuLevel(
                "Card",
                "card",
                data={
                    "cards": [choice.value],
                    "index": 0,
                    "location": "Runner rig",
                },
            )
        )

    def _open_servers(self, _choice: MenuChoice) -> None:
        choices = [
            MenuChoice(server_label(server), "server", server)
            for server in self.state.get("servers", [])
        ]
        self._push(
            MenuLevel("Servers", "servers", choices + [MenuChoice("Back", "back")])
        )

    def _open_server(self, choice: MenuChoice) -> None:
        self._push(
            MenuLevel(
                server_label(choice.value),
                "server",
                [MenuChoice("Back", "back")],
                data=choice.value,
            )
        )

    def _open_hand(self, _choice: MenuChoice) -> None:
        choices = [
            MenuChoice(
                f"{card.get('title', 'Unknown card')} "
                f"· {str(card.get('card_type', '')).title()}",
                "card",
                index,
            )
            for index, card in enumerate(self.hand)
        ]
        self._push(
            MenuLevel("Your hand", "hand", choices + [MenuChoice("Back", "back")])
        )

    def _open_card(self, choice: MenuChoice) -> None:
        self._push(
            MenuLevel(
                "Card",
                "card",
                data={"cards": self.hand, "index": int(choice.value)},
            )
        )

    def _back(self) -> None:
        if len(self.levels) > 1:
            self.levels.pop()
        else:
            if self.app is not None:
                self.app.exit()
        self._reset_menu_scroll()
        self._invalidate()

    def _push(self, level: MenuLevel) -> None:
        self.levels.append(level)
        self._reset_menu_scroll()
        self._invalidate()

    def _reset_menu_scroll(self) -> None:
        self.menu_pane.vertical_scroll = 0
        self.follow_selection = self.level.kind not in {"card", "history"}
        if self.app is not None:
            self.app.layout.focus(self.menu_window)

    def _invalidate(self) -> None:
        if self.app is not None:
            self.app.invalidate()
