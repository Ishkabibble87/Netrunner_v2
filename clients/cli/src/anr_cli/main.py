"""Minimal interactive client for creating a game through the HTTP API."""

import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

import typer
from rich.console import Console
from rich.json import JSON
from rich.panel import Panel
from rich.prompt import Prompt

from .deck_import import DeckImportError, parse_nrdb_deck


console = Console()
DEFAULT_API_URL = "http://127.0.0.1:8000"


class ApiError(Exception):
    """The game API could not complete a request."""


def request_json(api_url: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        f"{api_url.rstrip('/')}{path}",
        data=data,
        headers={
            "Accept": "application/json",
            **({"Content-Type": "application/json"} if data is not None else {}),
        },
        method="POST" if data is not None else "GET",
    )
    try:
        with urlopen(request, timeout=15) as response:
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


def choose(label: str, options: list[str]) -> int:
    for index, option in enumerate(options, start=1):
        console.print(f"  [cyan]{index}.[/] {option}")
    answer = Prompt.ask(
        label,
        choices=[str(index) for index in range(1, len(options) + 1)],
        default="1",
    )
    return int(answer) - 1


def play_ai(api_url: str) -> None:
    try:
        path_text = Prompt.ask("Path to downloaded NetrunnerDB decklist")
        path_text = path_text.strip().strip('"').strip("'")
        deck_path = Path(path_text).expanduser()
        deck_text = deck_path.read_text(encoding="utf-8-sig")
        card_catalog = request_json(api_url, "/card-catalog")
        if not isinstance(card_catalog, list):
            raise ApiError("The API returned an invalid card catalog")
        decklist = parse_nrdb_deck(deck_text, card_catalog)

        profiles = request_json(api_url, "/opponent-profiles")
        if not isinstance(profiles, list) or not profiles:
            console.print("[yellow]The API returned no opponent profiles.[/yellow]")
            return
        profile_names = [profile["name"] for profile in profiles]
        console.print("\n[bold]Select an opponent[/bold]")
        profile = profiles[choose("Opponent", profile_names)]

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
    except (ApiError, DeckImportError, OSError, KeyError, TypeError) as error:
        console.print(str(error), style="red")
        return

    initial_state = response.get("initial_state") if isinstance(response, dict) else None
    if not isinstance(initial_state, dict) or "game_id" not in initial_state:
        console.print("[red]The API returned an unexpected game response.[/red]")
        return

    console.print(f"\n[bold green]Game created:[/] {initial_state['game_id']}")
    console.print(
        Panel(
            JSON.from_data(initial_state),
            title="Player-visible initial state",
            border_style="green",
        )
    )


def new_game_menu(api_url: str) -> None:
    while True:
        console.print("\n[bold]New Game[/bold]")
        choice = choose("Choose an option", ["Host Game", "Play AI", "Back"])
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
        if choice == 0:
            new_game_menu(api_url)
        elif choice == 1:
            console.print("[yellow]Join Game is not implemented.[/yellow]")
        else:
            return


def main() -> None:
    typer.run(run_menu)
