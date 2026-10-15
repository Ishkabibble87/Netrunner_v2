"""Interactive CLI for creating and inspecting games through the game API."""

import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

import typer
from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from .game_ui import GameScreen
from .navigation import navigation_bindings
from .session import SessionConnection, SessionConnectionError
from .terminal import get_terminal_input


console = Console()
DEFAULT_API_URL = "http://127.0.0.1:8000"


class ApiError(Exception):
    """The game API could not complete a request."""


def request_json(
    api_url: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    player_token: str | None = None,
) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        f"{api_url.rstrip('/')}{path}",
        data=data,
        headers={
            "Accept": "application/json",
            **({"Authorization": f"Bearer {player_token}"} if player_token is not None else {}),
            **({"Content-Type": "application/json"} if data is not None else {}),
        },
        method="POST" if data is not None else "GET",
    )
    try:
        with urlopen(request, timeout=15) as response:
            if response.status == 204:
                return None
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(body).get("detail", body)
        except (json.JSONDecodeError, AttributeError):
            detail = body or error.reason
        raise ApiError(f"API returned HTTP {error.code}: {detail}") from error
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        reason = getattr(error, "reason", error)
        raise ApiError(f"Could not reach or read the API at {api_url}: {reason}") from error


def choose(label: str, options: list[str]) -> int | None:
    if not options:
        return None

    selected = 0

    def menu_fragments() -> list[tuple[str, str]]:
        fragments: list[tuple[str, str]] = [("class:title", f"{label}\n\n")]
        for index, option in enumerate(options):
            marker = "> " if index == selected else "  "
            style = "class:selected [SetCursorPosition]" if index == selected else ""
            fragments.append((style, f"{marker}{index + 1}. {option}\n"))
        fragments.append(("class:help", "\n↑/↓ or j/k move · Enter select · Esc back · Q cancel"))
        return fragments

    def move_selection(direction: int) -> None:
        nonlocal selected
        selected = (selected + direction) % len(options)

    def cancel() -> None:
        get_app().exit(result=None)

    bindings = navigation_bindings(
        move_selection, lambda: get_app().exit(result=selected), cancel, cancel
    )

    for index in range(min(len(options), 9)):
        def select_number(event: Any, index: int = index) -> None:
            event.app.exit(result=index)

        bindings.add(str(index + 1), eager=True)(select_number)

    app: Application[int | None] = Application(
        input=get_terminal_input(),
        layout=Layout(
            Window(
                FormattedTextControl(menu_fragments, focusable=True, show_cursor=False),
                wrap_lines=True,
            )
        ),
        key_bindings=bindings,
        style=Style.from_dict(
            {
                "title": "bold ansicyan",
                "selected": "reverse bold",
                "help": "ansibrightblack",
            }
        ),
        full_screen=False,
    )
    return app.run()


def select_deck(api_url: str) -> dict[str, Any] | None:
    decks = request_json(api_url, "/preconstructed-decks")
    if not isinstance(decks, list):
        raise ApiError("The API returned an invalid preconstructed deck catalog")

    options = [deck["name"] for deck in decks] + ["Import local deck file"]
    deck_index = choose("Select a deck", options)
    if deck_index is None:
        return None
    if deck_index < len(decks):
        return decks[deck_index]["decklist"]

    # TODO: Let users configure a default directory for saving new decks,
    # and list saved decks by title during deck selection.
    path_text = Prompt.ask("Path to NetrunnerDB deck file (blank to cancel)")
    path_text = path_text.strip().strip('"').strip("'")
    if not path_text:
        return None
    deck_path = Path(path_text).expanduser()
    deck_text = deck_path.read_text(encoding="utf-8-sig")
    return request_json(api_url, "/decks/import", {"text": deck_text})


def play_ai(api_url: str) -> None:
    try:
        card_catalog = request_json(api_url, "/card-catalog")
        if not isinstance(card_catalog, list):
            raise ApiError("The API returned an invalid card catalog")
        decklist = select_deck(api_url)
        if decklist is None:
            return

        profiles = request_json(api_url, "/opponent-profiles")
        if not isinstance(profiles, list) or not profiles:
            console.print("[yellow]The API returned no opponent profiles.[/yellow]")
            return
        profile_names = [profile["name"] for profile in profiles]
        profile_index = choose("Select an opponent", profile_names)
        if profile_index is None:
            return
        profile = profiles[profile_index]

        player_id = str(uuid4())
        response = request_json(
            api_url,
            "/games",
            {
                "player_id": player_id,
                "decklist": decklist,
                "opponent_profile_id": profile["profile_id"],
            },
        )
    except (ApiError, OSError, UnicodeError, KeyError, TypeError) as error:
        console.print(str(error), style="red")
        return

    initial_state = response.get("initial_state") if isinstance(response, dict) else None
    if (
        not isinstance(initial_state, dict)
        or "game_id" not in initial_state
        or not isinstance(response.get("player_token"), str)
    ):
        console.print("[red]The API returned an unexpected game response.[/red]")
        return

    catalog_by_id = {
        entry["definition_id"]: entry
        for entry in card_catalog
        if isinstance(entry, dict) and "definition_id" in entry
    }
    game_id = initial_state["game_id"]
    player_token = response["player_token"]
    try:
        with SessionConnection(
            api_url,
            game_id,
            player_token,
            reconnect_timeout_seconds=response["reconnect_timeout_seconds"],
        ) as connection:
            GameScreen(
                connection.initial_state,
                catalog_by_id,
                connection_status=lambda: connection.status,
                action_callback=connection.submit_action,
            ).run()
        if connection.error is not None:
            console.print(connection.error, style="red")
    except SessionConnectionError as error:
        console.print(str(error), style="red")
    finally:
        try:
            request_json(api_url, f"/games/{game_id}/leave", {}, player_token=player_token)
        except ApiError:
            console.print("Could not notify the API of exit; the session will expire automatically.", style="yellow")


def new_game_menu(api_url: str) -> None:
    while True:
        console.print("\n[bold]New Game[/bold]")
        choice = choose("Choose an option", ["Host Game", "Play AI", "Back"])
        if choice is None:
            return
        if choice == 0:
            console.print("[yellow]Host Game is not implemented.[/yellow]")
        elif choice == 1:
            play_ai(api_url)
        else:
            return


def run_menu(api_url: str = typer.Option(DEFAULT_API_URL, envvar="ANR_API_URL")) -> None:
    console.print(Panel("Android: Netrunner", style="bold cyan"))
    while True:
        console.print("\n[bold]Main Menu[/bold]")
        choice = choose("Choose an option", ["New Game", "Join Game", "Quit"])
        if choice is None:
            return
        if choice == 0:
            new_game_menu(api_url)
        elif choice == 1:
            console.print("[yellow]Join Game is not implemented.[/yellow]")
        else:
            return


def main() -> None:
    typer.run(run_menu)
