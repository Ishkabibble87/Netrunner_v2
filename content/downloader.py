"""Download exact Null Signal Games card-pack JSON into the shared content tree."""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

REPOSITORY = "https://github.com/Null-Signal-Games/netrunner-cards-json"
GITHUB_API = "https://api.github.com/repos/Null-Signal-Games/netrunner-cards-json"
RAW_ROOT = "https://raw.githubusercontent.com/Null-Signal-Games/netrunner-cards-json"
CARDS_ROOT = Path(__file__).resolve().parent / "cards"
USER_AGENT = "anr-v2-content-downloader/1.0"


def _fetch_json_document(url: str) -> tuple[bytes, Any]:
    """Fetch JSON and retain its original bytes for exact card-file downloads."""

    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = response.read()
    except HTTPError as error:
        raise RuntimeError(f"upstream returned HTTP {error.code} for {url}") from error
    except (URLError, TimeoutError) as error:
        raise RuntimeError(f"could not fetch {url}: {error}") from error

    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"upstream returned invalid UTF-8 JSON for {url}") from error
    return payload, document


def _latest_revision() -> str:
    """Resolve the current main branch once so every downloaded file is pinned."""

    _, commit = _fetch_json_document(f"{GITHUB_API}/commits/main")
    revision = commit.get("sha") if isinstance(commit, dict) else None
    if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("GitHub did not return a full commit SHA for main")
    return revision


def _raw_url(revision: str, path: str) -> str:
    return f"{RAW_ROOT}/{revision}/{path}"


def _load_pack_catalog(revision: str) -> list[dict[str, Any]]:
    _, packs = _fetch_json_document(_raw_url(revision, "packs.json"))
    if not isinstance(packs, list) or any(not isinstance(pack, dict) for pack in packs):
        raise ValueError("upstream packs.json must contain an array of pack objects")
    return packs


def _find_pack(packs: list[dict[str, Any]], pack_code: str) -> dict[str, Any]:
    matches = [pack for pack in packs if pack.get("code") == pack_code]
    if len(matches) != 1:
        raise ValueError(
            f"pack code {pack_code!r} was not found exactly once; "
            "run with --list-packs to see available codes"
        )
    pack = matches[0]
    if not isinstance(pack.get("name"), str) or not isinstance(pack.get("cycle_code"), str):
        raise ValueError(f"pack metadata for {pack_code!r} is missing its name or cycle")
    return pack


def _folder_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    if not slug:
        raise ValueError(f"cannot create a content folder for set name {name!r}")
    return slug


def _validate_cards(cards: Any, pack_code: str) -> list[dict[str, Any]]:
    if not isinstance(cards, list) or not cards:
        raise ValueError(f"upstream returned no card records for pack {pack_code!r}")

    seen_codes: set[str] = set()
    for index, card in enumerate(cards):
        if not isinstance(card, dict):
            raise ValueError(f"card record {index} for {pack_code!r} is not an object")
        if card.get("pack_code") != pack_code:
            raise ValueError(f"card record {index} does not belong to pack {pack_code!r}")
        card_code = card.get("code")
        if not isinstance(card_code, str) or not card_code:
            raise ValueError(f"card record {index} for {pack_code!r} has no card code")
        if card_code in seen_codes:
            raise ValueError(f"duplicate card code {card_code!r} in pack {pack_code!r}")
        seen_codes.add(card_code)
    return cards


def _write_bytes(path: Path, payload: bytes) -> None:
    """Replace one output file atomically so an interrupted fetch cannot truncate it."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
            temporary.write(payload)
            temporary_path = Path(temporary.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _json_bytes(document: Any) -> bytes:
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def list_packs() -> None:
    """Print the current upstream set codes and names."""

    revision = _latest_revision()
    packs = _load_pack_catalog(revision)
    print("Pack code | Set | Cycle")
    for pack in packs:
        code, name, cycle = pack.get("code"), pack.get("name"), pack.get("cycle_code")
        if isinstance(code, str) and isinstance(name, str) and isinstance(cycle, str):
            print(f"{code} | {name} | {cycle}")


def download_pack(pack_code: str) -> Path:
    """Download one pack's unchanged card JSON, set metadata, and source record."""

    if re.fullmatch(r"[a-z0-9-]+", pack_code) is None:
        raise ValueError("pack code must contain only lowercase letters, digits, and hyphens")

    revision = _latest_revision()
    pack = _find_pack(_load_pack_catalog(revision), pack_code)
    cards_url = _raw_url(revision, f"pack/{quote(pack_code, safe='')}.json")
    cards_payload, cards_document = _fetch_json_document(cards_url)
    cards = _validate_cards(cards_document, pack_code)

    output_dir = CARDS_ROOT / _folder_slug(pack["name"])
    source_path = output_dir / "source.json"
    source: dict[str, Any] = {}
    if source_path.exists():
        existing_source = json.loads(source_path.read_text(encoding="utf-8"))
        if not isinstance(existing_source, dict):
            raise ValueError(f"existing source metadata is not an object: {source_path}")
        source.update(existing_source)
    source.update(
        {
            "repository": REPOSITORY,
            "revision": revision,
            "cards_url": cards_url,
            "packs_url": _raw_url(revision, "packs.json"),
            "retrieved_date": date.today().isoformat(),
        }
    )

    _write_bytes(output_dir / "cards.json", cards_payload)
    _write_bytes(output_dir / "set.json", _json_bytes(pack))
    _write_bytes(source_path, _json_bytes(source))
    print(f"Downloaded {len(cards)} cards for {pack['name']} ({pack_code}) to {output_dir}")
    return output_dir


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download exact card data for a Netrunner set from the NSG card repository."
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--pack-code", help="upstream pack code to download")
    selection.add_argument(
        "--list-packs",
        action="store_true",
        help="list current upstream pack codes, set names, and cycles",
    )
    args = parser.parse_args()

    try:
        if args.list_packs:
            list_packs()
        else:
            download_pack(args.pack_code)
    except (RuntimeError, ValueError, json.JSONDecodeError) as error:
        parser.exit(1, f"download failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())