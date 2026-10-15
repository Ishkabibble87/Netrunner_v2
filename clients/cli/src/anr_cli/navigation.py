"""Shared keyboard navigation for setup menus and the game inspector."""

from collections.abc import Callable

from prompt_toolkit.key_binding import KeyBindings


def navigation_bindings(
    move: Callable[[int], None],
    activate: Callable[[], None],
    back: Callable[[], None],
    quit_view: Callable[[], None],
) -> KeyBindings:
    bindings = KeyBindings()
    for keys, callback in (
        (("up", "k", "c-p"), lambda: move(-1)),
        (("down", "j", "c-n"), lambda: move(1)),
        (("enter",), activate),
        (("escape",), back),
        (("q", "c-c"), quit_view),
    ):
        for key in keys:
            bindings.add(key, eager=True)(lambda event, callback=callback: callback())
    return bindings
