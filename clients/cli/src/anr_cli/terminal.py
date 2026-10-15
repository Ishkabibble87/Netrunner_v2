"""Terminal input that accepts native Windows keys alongside VT100 sequences."""

import sys

from prompt_toolkit.application.current import get_app_session
from prompt_toolkit.input import Input


if sys.platform == "win32":
    from collections.abc import Iterator
    from ctypes import Array
    from ctypes.wintypes import DWORD

    from prompt_toolkit.input.win32 import (
        ConsoleInputReader,
        Vt100ConsoleInputReader,
        Win32Input,
    )
    from prompt_toolkit.win32_types import INPUT_RECORD

    class _WindowsKeyReader(Vt100ConsoleInputReader, ConsoleInputReader):
        def _get_keys(
            self, read: DWORD, input_records: Array[INPUT_RECORD]
        ) -> Iterator[str]:
            for record in input_records[:read.value]:
                if record.EventType != 1 or not record.Event.KeyEvent.KeyDown:
                    continue
                event = record.Event.KeyEvent
                if event.uChar.UnicodeChar != "\x00":
                    yield event.uChar.UnicodeChar
                else:
                    # Native arrows/page keys have a virtual key code but no text.
                    # Feed their ANSI equivalents through the existing VT100 parser.
                    for key in self._event_to_key_presses(event):
                        yield self._insert_key_data(key).data


def get_terminal_input() -> Input:
    """Keep session/test input; adapt the default Windows VT100 reader once."""
    terminal_input = get_app_session().input
    if sys.platform == "win32" and isinstance(terminal_input, Win32Input):
        reader = terminal_input.console_input_reader
        if type(reader) is Vt100ConsoleInputReader:
            terminal_input.console_input_reader = _WindowsKeyReader()
            reader.close()
    return terminal_input
