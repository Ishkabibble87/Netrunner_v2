"""Keyboard-driven, read-only game view for the initial API snapshot."""

from __future__ import annotations

from dataclasses import dataclass, field
from html import unescape
import re
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

from .terminal import get_terminal_input
from .navigation import navigation_bindings


@dataclass
class MenuChoice:
    label: str
    kind: str
    value: Any = None


@dataclass
class MenuLevel:
    title: str
    kind: str
    choices: list[MenuChoice] = field(default_factory=list)
    selected: int = 0
    data: Any = None


class GameScreen:
    """Show the player-visible initial state with a responsive inspector."""

    wide_columns = 106

    def __init__(
        self,
        state: dict[str, Any],
        card_catalog: dict[str, dict[str, Any]],
        *,
        connection_status: Callable[[], str] | None = None,
    ) -> None:
        self.state = state
        self.card_catalog = card_catalog
        self.connection_status = connection_status
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
            refresh_interval=1 if self.connection_status is not None else None,
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

    def _header_fragments(self) -> list[tuple[str, str]]:
        return [("class:header", "ANDROID: NETRUNNER")]

    def _footer_fragments(self) -> list[tuple[str, str]]:
        if self.level.kind == "card":
            help_text = "↑/↓ Cards · Tab Panel · PgUp/PgDn Scroll · Esc Back · Q Quit"
        else:
            help_text = "↑/↓ Move · Tab Panel · PgUp/PgDn Scroll · Enter Open · Esc Back · Q Quit"
        return [("class:footer", help_text)]

    def _board_fragments(self, *, compact: bool) -> list[tuple[str, str]]:
        fragments: list[tuple[str, str]] = []

        def line(text: str = "", style: str = "") -> None:
            fragments.append((style, f"{text}\n"))

        corp, runner = self._players_by_role()
        corp_hand = self._hand_count_for_role(corp)
        runner_hand = self._hand_count_for_role(runner)
        corp_deck = int(corp.get("deck_count", 0))
        corp_identity = corp.get("identity", {}).get("title", "Unknown identity")
        runner_identity = runner.get("identity", {}).get("title", "Unknown identity")
        corp_stats = (
            f"{corp.get('credits', 0)} credits · {corp_hand} cards in HQ · "
            f"{corp_deck} cards in R&D"
        )
        runner_stats = (
            f"{runner.get('credits', 0)} credits · {runner_hand} cards in grip"
        )
        if self.viewer_role == "corp":
            corp_stats += f" · {corp.get('clicks_remaining', 0)} clicks"
        if self.viewer_role == "runner":
            runner_stats += f" · {runner.get('clicks_remaining', 0)} clicks"

        for title, identity, stats in (
            ("CORP", corp_identity, corp_stats),
            ("RUNNER", runner_identity, runner_stats),
        ):
            line(f"{title} · {identity}" if compact else title, "class:section")
            if not compact:
                line(identity)
            line(stats)
            if not compact:
                line()

        for title, items in (
            ("SERVERS", self._server_names()),
            ("RIG", ["Programs", "Hardware", "Resources"]),
            ("SCORE AREA", ["Corp: no agendas scored", "Runner: no agendas scored"]),
        ):
            if compact:
                line(f"{title}: {' · '.join(items)}")
            else:
                line(title, "class:section")
                for item in items:
                    line(item)
                line()
        return fragments

    def _menu_fragments(self) -> list[tuple[str, str]]:
        fragments: list[tuple[str, str]] = []

        def line(text: str = "", style: str = "") -> None:
            fragments.append((style, f"{text}\n"))

        line("STATUS", "class:section")
        line(self._status_text())
        if self.connection_status is not None:
            line(self.connection_status(), "class:muted")
        line()
        game_id = str(self.state.get("game_id", ""))
        if game_id:
            line("GAME ID · share to join", "class:section")
            line(game_id)
            line()

        if self.level.kind == "card":
            self._append_card_detail(fragments)
            return fragments

        line("ACTIONS", "class:section")
        line(
            "Game actions are unavailable: the API does not yet return legal moves or accept commands.",
            "class:muted",
        )
        line()
        breadcrumb = self._breadcrumb()
        if breadcrumb:
            line(breadcrumb, "class:muted")
        line(self.level.title.upper(), "class:section")
        if self.level.kind == "server":
            line("This initial snapshot does not include installed card details.", "class:muted")
            line()
        elif self.level.kind == "rig":
            line("Programs")
            line("Hardware")
            line("Resources")
            line()
        elif self.level.kind == "hand" and not self.hand:
            line("Your hand is empty.", "class:muted")
            line()

        if not self.level.choices:
            line("No choices here.", "class:muted")
        for index, choice in enumerate(self.level.choices):
            style = "class:selected [SetCursorPosition]" if index == self.level.selected else ""
            marker = "> " if index == self.level.selected else "  "
            line(f"{marker}{choice.label}", style)
        return fragments

    def _append_card_detail(
        self,
        fragments: list[tuple[str, str]],
    ) -> None:
        level = self.level
        cards = level.data["cards"]
        index = level.data["index"]
        card = cards[index]
        details = self.card_catalog.get(str(card.get("definition_id", "")), {})

        def line(text: str = "", style: str = "") -> None:
            fragments.append((style, f"{text}\n"))

        line(self._breadcrumb(), "class:muted")
        line(f"CARD {index + 1} OF {len(cards)}", "class:section")
        line(str(details.get("title", card.get("title", "Unknown card"))), "class:card-title")

        side = details.get("side", "")
        card_type = details.get("card_type", card.get("card_type", ""))
        subtypes = details.get("subtypes", [])
        type_line = " · ".join(
            part.title()
            for part in [side, card_type, *subtypes]
            if isinstance(part, str) and part
        )
        if type_line:
            line(type_line)

        for field_name, label in (
            ("play_cost", "Play cost"),
            ("install_cost", "Install cost"),
            ("rez_cost", "Rez cost"),
            ("trash_cost", "Trash cost"),
            ("advancement_requirement", "Advancement requirement"),
            ("agenda_points", "Agenda points"),
            ("strength", "Strength"),
            ("memory_cost", "Memory cost"),
            ("base_link", "Base link"),
        ):
            value = details.get(field_name)
            if value is not None:
                line(f"{label}: {value}")

        printed_text = self._plain_text(details.get("printed_text", ""))
        if printed_text:
            line()
            line("PRINTED TEXT", "class:section")
            line(printed_text)
        line()
        line("IN THIS GAME", "class:section")
        location = level.data.get("location")
        if location:
            line(str(location))
        else:
            line("In your grip" if self.viewer_role == "runner" else "In HQ")

    def _players_by_role(self) -> tuple[dict[str, Any], dict[str, Any]]:
        if self.you.get("role") == "corp":
            return self.you, self.opponent
        return self.opponent, self.you

    def _hand_count_for_role(self, player: dict[str, Any]) -> int:
        if player.get("role") == self.viewer_role:
            return len(player.get("hand", []))
        return int(player.get("hand_count", 0))

    def _server_names(self) -> list[str]:
        return [self._server_label(server) for server in self.state.get("servers", [])]

    def _status_text(self) -> str:
        turn = self.state.get("turn", {})
        active = str(turn.get("active_player", "unknown"))
        phase = str(turn.get("phase", "unknown")).replace("_", " ")
        number = turn.get("number", "?")
        if active == self.viewer_role:
            return f"Your {phase} phase · turn {number}"
        return f"Waiting on {active.title()} action · turn {number} · {phase} phase"

    def _breadcrumb(self) -> str:
        if len(self.levels) == 1:
            return ""
        labels = ["Inspect cards"]
        for level in self.levels[1:]:
            if level.kind == "servers":
                labels.append("Servers")
            elif level.kind == "server":
                labels.append(str(level.data))
            elif level.kind == "hand":
                labels.append("Your hand")
            elif level.kind == "card":
                location = level.data.get("location")
                if location:
                    labels.append(str(location))
                else:
                    cards = level.data["cards"]
                    card = cards[level.data["index"]]
                    labels.append(str(card.get("title", "Card")))
        return " / ".join(labels)

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
        level = self.level
        if level.kind == "card":
            return
        if not level.choices:
            return
        choice = level.choices[level.selected]
        if choice.kind == "exit":
            if self.app is not None:
                self.app.exit()
        elif choice.kind == "back":
            self._back()
        elif choice.kind == "inspect":
            corp, runner = self._players_by_role()
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
        elif choice.kind == "identity":
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
        elif choice.kind == "rig":
            self._push(
                MenuLevel(
                    "Rig",
                    "rig",
                    [MenuChoice("Back", "back")],
                )
            )
        elif choice.kind == "servers":
            servers = self.state.get("servers", [])
            self._push(
                MenuLevel(
                    "Servers",
                    "servers",
                    [
                        MenuChoice(self._server_label(server), "server", server)
                        for server in servers
                    ]
                    + [MenuChoice("Back", "back")],
                )
            )
        elif choice.kind == "server":
            self._push(
                MenuLevel(
                    self._server_label(choice.value),
                    "server",
                    [MenuChoice("Back", "back")],
                    data=self._server_label(choice.value),
                )
            )
        elif choice.kind == "hand":
            hand_choices = [
                MenuChoice(
                    f"{card.get('title', 'Unknown card')} · {str(card.get('card_type', '')).title()}",
                    "card",
                    index,
                )
                for index, card in enumerate(self.hand)
            ]
            self._push(
                MenuLevel(
                    "Your hand",
                    "hand",
                    hand_choices + [MenuChoice("Back", "back")],
                )
            )
        elif choice.kind == "card":
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
        self.follow_selection = self.level.kind != "card"
        if self.app is not None:
            self.app.layout.focus(self.menu_window)

    def _invalidate(self) -> None:
        if self.app is not None:
            self.app.invalidate()

    @staticmethod
    def _server_label(server: Any) -> str:
        if isinstance(server, int):
            return f"Remote {server}"
        return {
            "hq": "HQ",
            "rd": "R&D",
            "archives": "Archives",
        }.get(str(server).lower(), str(server))

    @staticmethod
    def _plain_text(value: Any) -> str:
        text = unescape(str(value or ""))
        text = re.sub(r"<\s*br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"</\s*(p|li|div)\s*>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = text.replace("[credit]", "credit").replace("[click]", "click")
        text = text.replace("[mu]", "MU")
        return text.strip()
