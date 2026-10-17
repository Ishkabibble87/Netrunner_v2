# Netrunner card sets

The original System Gateway cards.json came from the [NSG
netrunner-cards-json repository](https://github.com/Null-Signal-Games/netrunner-cards-json)
at the commit recorded in source.json. That repository stores NetrunnerDB card
data in per-pack JSON files. The [current NetrunnerDB V3 API
documentation](https://api.netrunnerdb.com/api/docs/) shows JSON:API card
resources, which do not have the same shape as this project's imported
cards.json.

## Download a set

From the repository root, list upstream pack codes and download a pack:

    python content/downloader.py --list-packs
    python content/downloader.py --pack-code df

The downloader resolves the latest main commit once, downloads
pack/<pack-code>.json and the matching pack record from packs.json at that same
commit, and writes:

- cards/<set-name>/cards.json — unchanged upstream JSON
- cards/<set-name>/set.json — pack metadata
- cards/<set-name>/source.json — source URLs, pinned commit, and retrieval date

The set folder is derived from its upstream name. Running the command again
updates that set and refreshes its source record.

## Planned card pool

The project notes set System Gateway as the initial playable pool and the
future compatibility target as the Null Signal Games Standard pool recorded
on 2026-10-14. The current upstream pack codes are:

| Set | Pack code |
| --- | --- |
| System Gateway | sg |
| Downfall (Ashes Cycle) | df |
| Uprising (Ashes Cycle) | ur |
| Midnight Sun (Borealis Cycle) | ms |
| Parhelion (Borealis Cycle) | ph |
| The Automata Initiative (Liberation Cycle) | tai |
| Rebellion Without Rehearsal (Liberation Cycle) | rwr |
| Elevation | elev |
| Vantage Point | vp |

System Update 2021 is outside that target.