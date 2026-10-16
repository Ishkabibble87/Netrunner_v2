# System Gateway starter decks

Unmodified NetrunnerDB text-file exports downloaded on 2026-10-14.
Main-deck counts exclude the identity card.

| File | Main deck | NetrunnerDB decklist |
| --- | --- | --- |
| `starter-runner.txt` | 30 cards | [System Gateway Starter Runner](https://netrunnerdb.com/en/decklist/d71397b7-af7b-475c-8984-18360a64f6ee) |
| `starter-corp.txt` | 34 cards | [System Gateway Starter Corp](https://netrunnerdb.com/en/decklist/0665c5c7-f7f1-4674-86b5-ca4e371888f2) |

Download URLs:

- https://netrunnerdb.com/decklist/export/text/d71397b7-af7b-475c-8984-18360a64f6ee
- https://netrunnerdb.com/decklist/export/text/0665c5c7-f7f1-4674-86b5-ca4e371888f2

These exports identify cards by title and set name, with quantity prefixes,
section headings, influence symbols, and summary lines. A plaintext importer
must resolve titles against the card catalog and separate the identity from
main-deck quantities before producing `DeckSelection`. The importer is in
`content/decks/deck_importer.py`. These are teaching decks; validate them under
starter-game rules.
