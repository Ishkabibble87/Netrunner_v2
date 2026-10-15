"""Keep the CLI responsible for selecting and uploading files, not parsing."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anr_cli.main import select_deck


class DeckSelectionTests(unittest.TestCase):
    def test_local_file_contents_are_uploaded_to_api(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "deck.txt"
            path.write_text("\ufeffDeck export\n", encoding="utf-8")
            with (
                patch("anr_cli.main.request_json", side_effect=[[], {"card_counts": {}}]) as request,
                patch("anr_cli.main.choose", return_value=0),
                patch("anr_cli.main.Prompt.ask", return_value=f'"{path}"'),
            ):
                self.assertEqual(select_deck("http://api"), {"card_counts": {}})
                request.assert_any_call("http://api", "/decks/import", {"text": "Deck export\n"})

    def test_preconstructed_selection_needs_no_import(self):
        decklist = {"identity_definition_id": "id", "card_counts": {"card": 3}}
        with (
            patch("anr_cli.main.request_json", return_value=[{"name": "Starter", "decklist": decklist}]) as request,
            patch("anr_cli.main.choose", return_value=0),
        ):
            self.assertEqual(select_deck("http://api"), decklist)
            request.assert_called_once_with("http://api", "/preconstructed-decks")

    def test_cancel_does_not_upload(self):
        with (
            patch("anr_cli.main.request_json", return_value=[]) as request,
            patch("anr_cli.main.choose", return_value=0),
            patch("anr_cli.main.Prompt.ask", return_value=""),
        ):
            self.assertIsNone(select_deck("http://api"))
            request.assert_called_once_with("http://api", "/preconstructed-decks")


if __name__ == "__main__":
    unittest.main()
