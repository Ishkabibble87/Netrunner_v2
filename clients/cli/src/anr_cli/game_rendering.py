"""Render the player's game view and navigable inspection panels."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape
from typing import Any


@dataclass
class MenuChoice:
    """One visible menu entry and the value dispatched when it is selected."""

    label: str
    kind: str
    value: Any = None

@dataclass
class MenuLevel:
    """A navigable screen within the CLI menu."""

    title: str
    kind: str
    choices: list[MenuChoice] = field(default_factory=list)
    selected: int = 0
    data: Any = None


def server_label(server: Any) -> str:
    """Format a public server identifier for the terminal."""
    if isinstance(server, int):
        return f"Remote {server}"
    return {
        "hq": "HQ",
        "rd": "R&D",
        "archives": "Archives",
    }.get(str(server).lower(), str(server))

def find_card(cards: list[dict[str, Any]], instance_id: Any) -> dict[str, Any] | None:
    """Find a visible card by its in-game instance ID."""
    return next(
        (card for card in cards if card.get("instance_id") == instance_id),
        None,
    )

def find_installed_card(state: dict[str, Any], instance_id: str) -> dict[str, Any] | None:
    """Find an installed-card view entry without exposing hidden card faces."""
    return find_card(state.get("installed_cards", []), instance_id)


def is_end_of_turn_window(state: dict[str, Any], viewer_role: str) -> bool:
    """Identify the active player's paid window after all clicks are spent."""
    turn = state.get("turn") or {}
    decision = state.get("decision") or {}
    return (
        turn.get("active_player") == viewer_role
        and turn.get("phase") in {"action", "discard"}
        and state.get("you", {}).get("clicks_remaining") == 0
        and not state.get("run")
        and decision.get("type") == "paid_ability_window"
        and decision.get("player") == viewer_role
    )

def players_by_role(
    you: dict[str, Any],
    opponent: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the public player views in Corp, Runner order."""
    if you.get("role") == "corp":
        return you, opponent
    return opponent, you

def host_label(state: dict[str, Any], instance_id: str) -> str:
    """Identify a host by public location while keeping facedown ice hidden."""
    host = next(
        card for card in state.get("installed_cards", [])
        if card["instance_id"] == instance_id
    )
    title = host["card"]["title"] if host.get("card") else "unrezzed ice"
    return (
        f"{title} at {server_label(host['server_id'])} · "
        f"ice {host['ice_distance_from_server'] + 1}"
    )

def installed_details(state: dict[str, Any], card: dict[str, Any]) -> str:
    """Describe visible counters and host location for an installed card."""
    details = "".join(
        f" · {amount} {kind} counters"
        for kind, amount in card.get("counters", {}).items()
    )
    if card.get("host_card_instance_id"):
        details += f" · hosted on {host_label(state, card['host_card_instance_id'])}"
    return details


class GameRenderer:
    """Format board, status, history, and card details for the terminal."""

    def __init__(self, card_catalog: dict[str, dict[str, Any]]) -> None:
        self.card_catalog = card_catalog

    def set_context(
        self,
        state: dict[str, Any],
        viewer_role: str,
        levels: list[MenuLevel],
        connection_status: Any,
        command_error: str | None,
        *,
        focused_panel: str = "menu",
    ) -> None:
        """Refresh the read-only data used while rendering the current screen."""
        self.state = state
        self.viewer_role = viewer_role
        self.you = state.get("you", {})
        self.opponent = state.get("opponent", {})
        self.hand = self.you.get("hand", [])
        self.levels = levels
        self.connection_status = connection_status
        self.command_error = command_error
        self.focused_panel = focused_panel

    @property
    def level(self) -> MenuLevel:
        return self.levels[-1]

    @staticmethod

    def _server_label(server: Any) -> str:
        return server_label(server)

    def _host_label(self, instance_id: str) -> str:
        return host_label(self.state, instance_id)

    def _installed_details(self, card: dict[str, Any]) -> str:
        return installed_details(self.state, card)

    def _header_fragments(self) -> list[tuple[str, str]]:
        return [("class:header", "ANDROID: NETRUNNER")]

    def _footer_fragments(self) -> list[tuple[str, str]]:
        if self.focused_panel == "board":
            help_text = "BOARD · ↑/↓ Scroll · PgUp/PgDn Page · Tab/M Menu · Q Quit"
        elif self.level.kind == "card":
            help_text = "MENU · ↑/↓ Cards · Tab/B Board · PgUp/PgDn Scroll · Esc Back · Q Quit"
        else:
            help_text = "MENU · ↑/↓ Move · Tab/B Board · PgUp/PgDn Scroll · Enter Open · Esc Back · Q Quit"
        return [("class:footer", help_text)]

    def _board_fragments(self, *, compact: bool) -> list[tuple[str, str]]:
        fragments: list[tuple[str, str]] = []

        def line(text: str = "", style: str = "") -> None:
            fragments.append((style, f"{text}\n"))

        if self.state.get("turn"):
            line(self._turn_text(), "class:section")
            line()

        if self.state.get("setup", {}).get("pending_player") is not None:
            line("YOUR OPENING HAND", "class:section")
            for card in self.hand:
                line(f"{card['title']} · {str(card.get('card_type', '')).title()}")
            line()

        if (self.state.get("turn") or {}).get("cannot_score"):
            line("Corp cannot score agendas for the remainder of this turn", "class:section")
        run = self.state.get("run")
        if run:
            phase = "Runner may jack out" if run["phase"] == "subroutine_choice" else run["phase"]
            line(f"Run on {self._server_label(run['server_id'])} · {phase}", "class:section")
            line(f"Run credits: {run.get('bad_publicity_credits', 0)} bad publicity · {run.get('event_credits', 0)} event")
            line(f"Accesses: {run.get('access_count', 0)} · agendas stolen: {run.get('agendas_stolen', 0)}")
            if run.get("source_card") and not run.get("source_event"):
                line(f"Source: {run['source_card']['title']}")
            if run.get("source_event"):
                line(f"Event: {run['source_event']['title']}")
            if run.get("server_id") in {"hq", "rd"}:
                line(f"Central accesses: {run.get('central_accesses', 0)}/{run.get('central_access_limit', 1)}")
            ice = run.get("current_ice")
            if ice:
                line(f"{ice['title']} · strength {ice['strength']}", "class:card-title")
            elif run.get("current_ice_id"):
                line("Approaching facedown ice")
            for sub in run.get("subroutines", []):
                line(f"{sub['index'] + 1}. {sub['label']} · {'broken' if sub['broken'] else 'unbroken'}")
            for installed in self.state.get("installed_cards", []):
                breaker = installed.get("card") or {}
                if installed.get("area") == "rig" and breaker.get("strength") is not None:
                    line(f"{breaker['title']} · strength {breaker['strength']}")
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
        runner_stats += f" · {runner.get('memory_used', 0)}/{runner.get('memory_limit', 4)} MU"
        corp_stats += f" · hand limit {corp.get('maximum_hand_size', 5)}"
        runner_stats += f" · hand limit {runner.get('maximum_hand_size', 5)}"
        if runner.get("tags", 0):
            runner_stats += f" · {runner['tags']} tags"
        corp_stats += f" · {corp.get('clicks_remaining', 0)} clicks"
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
        if self.state.get("setup", {}).get("pending_player") is None:
            line(f"CARDS IN HAND · {len(self.hand)}", "class:section")
            for card in self.hand:
                line(f"{card['title']} · {str(card.get('card_type', '')).title()}")
            if not self.hand:
                line("Your hand is empty.", "class:muted")
        return fragments

    def _menu_fragments(self) -> list[tuple[str, str]]:
        fragments: list[tuple[str, str]] = []

        def line(text: str = "", style: str = "") -> None:
            fragments.append((style, f"{text}\n"))

        line("STATUS", "class:section")
        if self.state.get("turn"):
            line(self._turn_text(), "class:section")
        line(self._status_text())
        if self.state.get("revealed_cards"):
            line("Revealed: " + ", ".join(c["title"] for c in self.state["revealed_cards"]))
        if self.state.get("removed_cards"):
            line("Removed from game: " + ", ".join(c["title"] for c in self.state["removed_cards"]))
        if self.connection_status is not None:
            line(self.connection_status(), "class:muted")
        line()
        game_id = str(self.state.get("game_id", ""))
        if game_id:
            line("GAME ID · share to join", "class:section")
            line(game_id)
            line()

        if self.level.kind == "history":
            self._append_history(fragments)
            return fragments

        if self.level.kind == "card":
            self._append_card_detail(fragments)
            return fragments

        line("ACTIONS", "class:section")
        line(self._last_opponent_action(), "class:muted")
        line()
        pending_player = self.state.get("setup", {}).get("pending_player")
        if pending_player == self.viewer_role:
            action_prompt = "Review your hand, then keep it or take one mulligan."
        elif pending_player is not None:
            action_prompt = f"Waiting for {str(pending_player).title()} to choose an opening hand."
        elif is_end_of_turn_window(self.state, self.viewer_role):
            action_prompt = "Use any remaining paid abilities, or choose End turn. Required discards and opponent responses follow."
        elif (
            (self.state.get("decision") or {}).get("type") == "paid_ability_window"
            and self.state["decision"].get("player") == self.viewer_role
            and (self.state.get("turn") or {}).get("active_player") != self.viewer_role
            and not self.state.get("run")
        ):
            action_prompt = "Your opponent's turn is in progress. Use a paid ability, or pass priority to let them continue."
        elif (self.state.get("decision") or {}).get("type") == "trash_for_memory":
            action_prompt = "The Runner must trash programs to fit available memory."
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

    def _last_opponent_action(self) -> str:
        """Keep the latest opponent move visible through passes and your own actions.

        Use only this viewer's history details, which omit hidden card faces.
        Setup choices and priority passes do not replace a gameplay action.
        """
        opponent_role = "runner" if self.viewer_role == "corp" else "corp"
        for entry in reversed(self.state.get("history", [])):
            if entry.get("kind") != "command" or entry.get("actor") != opponent_role:
                continue
            detail = entry["detail"]
            text = detail["text"]
            if text == "Pass Priority" or text.startswith("Choose Mulligan:"):
                continue
            title = f"{detail['title']}: " if detail.get("title") else ""
            turn = f" · turn {entry['turn_number']}" if entry.get("turn_number") else ""
            return f"Last opponent action{turn}: {title}{text}"
        return "Last opponent action: none yet."

    def _append_history(self, fragments: list[tuple[str, str]]) -> None:
        """Render the API's private history in order, grouped by turn and action."""
        entries = self.state.get("history", [])
        if not entries:
            fragments.append(("class:muted", "No history yet.\n"))
            return
        previous_turn = previous_group = None
        depths: dict[str, int] = {}
        roots = {entry["entry_id"]: entry for entry in entries}
        for entry in entries:
            turn = (entry.get("turn_number"), entry.get("turn_player"))
            if turn != previous_turn:
                label = f"Turn {turn[0]} · {str(turn[1]).title()}" if turn[0] else "Setup"
                fragments.append(("class:section", f"\n{label}\n"))
                previous_turn = turn
            if entry["group_id"] != previous_group:
                root = roots.get(entry["group_id"], entry)
                label = root["detail"]["text"]
                if root["detail"].get("title"):
                    label = f"{root['detail']['title']}: {label}"
                fragments.append(("class:section", f"\n{label}\n"))
                previous_group = entry["group_id"]
            depth = min(depths.get(entry.get("parent_id"), -1) + 1, 4)
            depths[entry["entry_id"]] = depth
            detail = entry["detail"]
            title = f"{detail['title']}: " if detail.get("title") else ""
            actor = f"{entry['actor'].title()} · " if entry.get("actor") and entry["kind"] == "command" else ""
            style = "class:section" if entry["entry_id"] == entry["group_id"] else ""
            fragments.append((style, f"{'  ' * depth}{entry['sequence']}. {actor}{title}{detail['text']}\n"))

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
            rows.append(f"{owner} rig: {title}{self._installed_details(card)}")
        return rows

    def _turn_text(self) -> str:
        """Keep the active turn visible even when the other player has priority."""
        turn = self.state["turn"]
        active = str(turn["active_player"])
        owner = "you" if active == self.viewer_role else "opponent"
        phase = str(turn["phase"]).replace("_", " ")
        return f"TURN {turn['number']} · {active.upper()} ({owner}) · {phase} phase"

    def _status_text(self) -> str:
        winner = self.state.get("winner")
        if winner:
            return f"{str(winner).title()} wins"
        pending = self.state.get("setup", {}).get("pending_player")
        if pending is not None:
            if pending == self.viewer_role:
                return "Choose your opening hand · one mulligan allowed"
            return f"Waiting for {str(pending).title()} opening-hand choice"
        if (self.state.get("run") or {}).get("phase") == "subroutine_choice":
            return "Jack out or continue resolving subroutines" if self.viewer_role == "runner" else "Runner may jack out"
        decision = self.state.get("decision") or {}
        if decision.get("type") in {"choose_effect", "choose_cards"}:
            who = "You" if decision["player"] == self.viewer_role else str(decision["player"]).title()
            return f"{who}: " + ("choose cards" if decision["type"] == "choose_cards" else "choose an effect")
        if decision.get("type") in {"paid_ability_window", "resolve_reaction"}:
            if is_end_of_turn_window(self.state, self.viewer_role):
                return "No clicks remaining · finish paid abilities or end your turn"
            who = "You" if decision["player"] == self.viewer_role else str(decision["player"]).title()
            return f"{who}: " + ("paid ability window · act or pass" if decision["type"] == "paid_ability_window" else "resolve reaction")
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
        if self.levels[1].kind == "actions":
            return " / ".join(["Game", *(level.title for level in self.levels[1:])])
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

    @staticmethod

    def _plain_text(value: Any) -> str:
        text = unescape(str(value or ""))
        text = re.sub(r"<\s*br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"</\s*(p|li|div)\s*>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = text.replace("[credit]", "credit").replace("[click]", "click")
        text = text.replace("[mu]", "MU")
        return text.strip()
