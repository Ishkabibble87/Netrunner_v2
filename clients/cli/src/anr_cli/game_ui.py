"""Keyboard-driven game inspector and player commands through the API."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape
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

from .navigation import navigation_bindings
from .session import SessionConnectionError
from .terminal import get_terminal_input


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
    """Show private game snapshots and offer commands from the server's view."""

    wide_columns = 106

    def __init__(
        self,
        state: dict[str, Any],
        card_catalog: dict[str, dict[str, Any]],
        *,
        connection_status: Callable[[], str] | None = None,
        state_provider: Callable[[], dict[str, Any]] | None = None,
        choose_opening_hand: Callable[[str], dict[str, Any]] | None = None,
        submit_action: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.state = state
        self.card_catalog = card_catalog
        self.connection_status = connection_status
        self.state_provider = state_provider
        self.choose_opening_hand = choose_opening_hand
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
        choices: list[MenuChoice] = []
        legal_actions = self.state.get("legal_actions")
        if isinstance(legal_actions, list):
            for action in legal_actions:
                if not isinstance(action, dict):
                    continue
                action_type = action.get("type")
                if action_type == "choose_mulligan" and self.choose_opening_hand is not None:
                    decision = action.get("decision")
                    if decision == "keep":
                        choices.append(MenuChoice("Keep opening hand", "opening_hand", decision))
                    elif decision == "mulligan":
                        choices.append(
                            MenuChoice("Mulligan · shuffle and draw five", "opening_hand", decision)
                        )
                elif action_type in {"run_server", "continue_run", "jack_out", "access_card", "finish_access"} and self.submit_action is not None:
                    run = self.state.get("run") or {}
                    if action_type == "run_server":
                        label = f"Run {self._server_label(action['server_id'])} · 1 click"
                    elif action_type == "continue_run":
                        label = "Continue to server"
                    elif action_type == "jack_out":
                        label = "Jack out"
                    elif action_type == "access_card":
                        target = action["target"]
                        known = run.get("accessed_cards", []) + self.opponent.get("discard_pile", [])
                        known += [c["card"] for c in self.state.get("installed_cards", []) if c.get("card")]
                        card = next((c for c in known if c["instance_id"] == target), None)
                        if target == "hq":
                            label = "Access a random card from HQ"
                        elif target == "rd":
                            label = "Access the top card of R&D"
                        else:
                            roots = [c for c in self.state.get("installed_cards", []) if c["instance_id"] == target]
                            position = self._server_label(roots[0]["server_id"]) if roots else "Archives"
                            root_cards = [c for c in self.state.get("installed_cards", [])
                                          if roots and c["server_id"] == roots[0]["server_id"] and c["area"] == "root"]
                            ordinal = next((i for i, c in enumerate(root_cards, 1) if c["instance_id"] == target), 1)
                            title = card['title'] if card else f"facedown card {ordinal}"
                            label = f"Access {title} in {position}"
                    else:
                        card = run.get("current_card") or {}
                        if action.get("trash"):
                            cost = self.card_catalog.get(card.get("definition_id"), {}).get("trash_cost", "?")
                            label = f"Trash {card.get('title', 'card')} · {cost} credits"
                        elif card.get("card_type") == "agenda":
                            label = f"Steal {card['title']}"
                        else:
                            label = "Leave card and finish access"
                    choices.append(MenuChoice(label, "action", dict(action)))
                elif action_type == "play_card" and self.submit_action is not None:
                    card = next((c for c in self.hand if c["instance_id"] == action["card_instance_id"]), None)
                    if card is not None:
                        cost = self.card_catalog.get(card["definition_id"], {}).get("play_cost", "?")
                        choices.append(MenuChoice(f"Play {card['title']} · {cost} credits", "action", dict(action)))
                elif action_type == "install_card" and self.submit_action is not None:
                    card = next((c for c in self.hand if c["instance_id"] == action["card_instance_id"]), None)
                    if card:
                        target = ("your rig" if action.get("server_id") is None else
                                  "new remote" if action["server_id"] == "new_remote" else self._server_label(action["server_id"]))
                        replacement = " · replace existing card" if action.get("trash_card_instance_ids") else ""
                        cost = self.card_catalog.get(card["definition_id"], {}).get("install_cost")
                        suffix = f" · {cost} {'credit' if cost == 1 else 'credits'}" if action.get("server_id") is None else ""
                        choices.append(MenuChoice(f"Install {card['title']} in {target}{replacement} · 1 click{suffix}", "action", dict(action)))
                elif action_type in {"rez_card", "use_card_ability"} and self.submit_action is not None:
                    card = next((c["card"] for c in self.state.get("installed_cards", [])
                                 if c["instance_id"] == action["card_instance_id"] and c.get("card")), {})
                    title = card.get("title", "card")
                    if action_type == "rez_card":
                        cost = self.card_catalog.get(card.get("definition_id"), {}).get("rez_cost", "?")
                        label = f"Rez {title} · {cost} credits"
                    else:
                        ability = card.get("ability_labels", {}).get(action["ability_id"], action["ability_id"])
                        label = f"{title}: {ability} · 1 click"
                    choices.append(MenuChoice(label, "action", dict(action)))
                elif action_type in {"advance_card", "score_agenda", "resolve_reaction"} and self.submit_action is not None:
                    installed = [c["card"] for c in self.state.get("installed_cards", []) if c.get("card")]
                    scored = self.state.get("you", {}).get("score_area", [])
                    card = next((c for c in installed + scored if c["instance_id"] == action["card_instance_id"]), {})
                    verb = {"advance_card": "Advance", "score_agenda": "Score", "resolve_reaction": "Resolve scoring ability:"}[action_type]
                    suffix = " · 1 credit" if action_type == "advance_card" else ""
                    choices.append(MenuChoice(f"{verb} {card.get('title', 'agenda')}{suffix}", "action", dict(action)))
                elif action_type == "discard_cards" and self.submit_action is not None:
                    choices.append(MenuChoice(f"Discard {action['required_count']} cards", "discard", action))
                elif self.submit_action is not None:
                    label = {
                        "pass_priority": "Pass priority",
                        "gain_credit": "Gain 1 credit",
                        "draw_card": "Draw 1 card",
                        "remove_tag": "Remove 1 tag · 2 credits",
                        "purge_virus_counters": "Purge virus counters · 3 clicks",
                    }.get(action_type)
                    if label is not None:
                        choices.append(MenuChoice(label, "action", dict(action)))
        elif (
            self.choose_opening_hand is not None
            and self.state.get("setup", {}).get("pending_player") == self.viewer_role
        ):
            # Older snapshots in local tools may predate the legal_actions field.
            choices.extend([
                MenuChoice("Keep opening hand", "opening_hand", "keep"),
                MenuChoice("Mulligan · shuffle and draw five", "opening_hand", "mulligan"),
            ])
        return choices + [
            MenuChoice("Inspect cards · read only", "inspect"),
            MenuChoice("Leave game", "exit"),
        ]

    def update_state(self, state: dict[str, Any]) -> None:
        """Refresh the board and discard inspector references to the previous hand."""
        if state == self.state:
            return
        self.state = state
        self.you = state.get("you", {})
        self.opponent = state.get("opponent", {})
        self.viewer_role = str(state.get("viewer_role", self.you.get("role", "")))
        self.hand = self.you.get("hand", [])
        self.levels = [MenuLevel("Game", "root", self._root_choices())]
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

        run = self.state.get("run")
        if run:
            line(f"Run on {self._server_label(run['server_id'])} · {run['phase']}", "class:section")
            line(f"Run credits: {run.get('bad_publicity_credits', 0)}")
            card = run.get("current_card")
            if card:
                line(f"Accessing {card['title']}", "class:card-title")
                definition = self.card_catalog.get(card["definition_id"], {})
                line(self._plain_text(definition.get("printed_text", "")))
                line(run.get("access_note", ""), "class:muted")
            line()
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
        if runner.get("tags", 0):
            runner_stats += f" · {runner['tags']} tags"
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
            ("SCORE AREA", [f"{role}: {player.get('agenda_points', 0)} points · " +
                            (", ".join(c["title"] for c in player.get("score_area", [])) or "no agendas scored")
                            for role, player in (("Corp", corp), ("Runner", runner))]),
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
        pending_player = self.state.get("setup", {}).get("pending_player")
        if pending_player == self.viewer_role:
            action_prompt = "Review your hand, then keep it or take one mulligan."
        elif pending_player is not None:
            action_prompt = f"Waiting for {str(pending_player).title()} to choose an opening hand."
        elif (self.state.get("decision") or {}).get("type") == "discard_to_hand_limit":
            action_prompt = self._status_text()
        elif (self.state.get("decision") or {}).get("player") not in (None, self.viewer_role):
            action_prompt = f"Waiting for {self.state['decision']['player'].title()} to choose an action."
        elif self.state.get("legal_actions"):
            action_prompt = "Choose an available action, or inspect the board."
        else:
            action_prompt = "No actions are available now; you can still inspect the board."
        line(action_prompt, "class:muted")
        if self.command_error:
            line(self.command_error)
        line()
        breadcrumb = self._breadcrumb()
        if breadcrumb:
            line(breadcrumb, "class:muted")
        line(self.level.title.upper(), "class:section")
        if self.level.kind == "server":
            for card in self.state.get("installed_cards", []):
                if card["server_id"] == self.level.data:
                    title = card["card"]["title"] if card.get("card") else "Facedown card"
                    line(f"{title} · {card['area']} · {card.get('counters', {}).get('advancement', 0)} advancement")
                    if "credit" in card.get("counters", {}):
                        line(f"  {card['counters']['credit']} hosted credits")
            line()
        elif self.level.kind == "rig":
            line("Installed Runner cards · select a card to read its abilities")
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
        rows = []
        for server in self.state.get("servers", []):
            cards = [c for c in self.state.get("installed_cards", []) if c["server_id"] == server]
            labels = []
            for card in cards:
                label = card["card"]["title"] if card.get("card") else "Facedown card"
                label += f" ({card['area']})"
                if card.get("counters", {}).get("advancement"):
                    label += f" · {card['counters']['advancement']} advancement"
                if "credit" in card.get("counters", {}):
                    label += f" · {card['counters']['credit']} hosted credits"
                if card.get("rezzed"):
                    label += " · rezzed"
                labels.append(label)
            rows.append(self._server_label(server) + (": " + ", ".join(labels) if labels else ""))
        rig = [c for c in self.state.get("installed_cards", []) if c.get("area") == "rig"]
        for card in rig:
            title = card["card"]["title"] if card.get("card") else "Facedown card"
            owner = "Your" if card.get("controller_id") == self.you.get("player_id") else "Opponent's"
            rows.append(f"{owner} rig: {title} · {card.get('counters', {}).get('credit', 0)} hosted credits")
        return rows

    def _status_text(self) -> str:
        winner = self.state.get("winner")
        if winner:
            return f"{str(winner).title()} wins"
        pending = self.state.get("setup", {}).get("pending_player")
        if pending is not None:
            if pending == self.viewer_role:
                return "Choose your opening hand · one mulligan allowed"
            return f"Waiting for {str(pending).title()} opening-hand choice"
        decision = self.state.get("decision") or {}
        if decision.get("type") in {"paid_ability_window", "resolve_reaction"}:
            who = "You" if decision["player"] == self.viewer_role else str(decision["player"]).title()
            return f"{who}: " + ("scoring window · score or pass" if decision["type"] == "paid_ability_window" else "resolve scoring reaction")
        if decision.get("type") == "discard_to_hand_limit":
            who = "You" if decision["player"] == self.viewer_role else str(decision["player"]).title()
            return f"{who} must discard {decision['cards_to_discard']} cards"
        turn = self.state.get("turn") or {}
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

    def _refresh_discard_choices(self) -> None:
        """Collect a local selection and send it as one simultaneous discard."""
        data = self.level.data
        selected = data["selected"]
        eligible = data["action"]["eligible_card_instance_ids"]
        self.level.choices = [MenuChoice(
            f"{'[x]' if card['instance_id'] in selected else '[ ]'} {card['title']}",
            "toggle_discard", card["instance_id"],
        ) for card in self.hand if card["instance_id"] in eligible]
        if len(selected) == data["action"]["required_count"]:
            self.level.choices.append(MenuChoice("Confirm discard", "action", {
                "type": "discard_cards", "card_instance_ids": list(selected),
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
        elif choice.kind == "opening_hand":
            try:
                state = self.choose_opening_hand(str(choice.value))
            except (SessionConnectionError, ValueError, OSError) as error:
                self.command_error = str(error)
            else:
                self.command_error = None
                self.update_state(state)
            self._invalidate()
        elif choice.kind == "action":
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
        elif choice.kind == "discard":
            self._push(MenuLevel("Choose cards to discard", "discard", data={
                "action": choice.value, "selected": [],
            }))
            self._refresh_discard_choices()
        elif choice.kind == "toggle_discard":
            selected = self.level.data["selected"]
            if choice.value in selected:
                selected.remove(choice.value)
            elif len(selected) < self.level.data["action"]["required_count"]:
                selected.append(choice.value)
            self._refresh_discard_choices()
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
            cards = [c for c in self.state.get("installed_cards", []) if c.get("area") == "rig" and c.get("card")]
            self._push(
                MenuLevel(
                    "Rig",
                    "rig",
                    [MenuChoice(f"{c['card']['title']} · {c.get('counters', {}).get('credit', 0)} hosted credits",
                                "installed_card", c["card"]) for c in cards] + [MenuChoice("Back", "back")],
                )
            )
        elif choice.kind == "installed_card":
            self._push(MenuLevel("Card", "card", data={"cards": [choice.value], "index": 0, "location": "Runner rig"}))
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
                    data=choice.value,
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
