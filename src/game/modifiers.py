"""Derive live card modifiers from their sources, without cached player totals.

Identities, active installed cards, and agendas in the Corp's score area supply
bonuses. Printed prose stays display-only; definitions author every modifier.
"""

from collections.abc import Iterator, Mapping

from content.cards.models import CardDefinition, CardType

from .models import CardZone, GameState, PlayerRole, PlayerState


def modifier_sources(state: GameState, player: PlayerState,
                     definitions: Mapping[str, CardDefinition]) -> Iterator[CardDefinition]:
    """Yield definitions whose static abilities currently affect this player."""
    for card in state.card_instances.values():
        definition = definitions.get(card.definition_id)
        if definition is None:
            continue
        if card.location.zone is CardZone.SCORE_AREA:
            enabled = (player.role is PlayerRole.CORP
                       and card.location.player_id == player.player_id)
        else:
            enabled = (card.controller_id == player.player_id
                       and (card.location.zone is CardZone.IDENTITY
                            or (card.installed and card.face_up
                                and (player.role is PlayerRole.RUNNER or card.rezzed))))
        if enabled:
            yield definition


def memory_limit(state: GameState, player: PlayerState,
                 definitions: Mapping[str, CardDefinition]) -> int | None:
    """Apply active memory bonuses to the Runner's starting memory limit."""
    if player.role is not PlayerRole.RUNNER:
        return None
    return player.memory_limit + sum(d.memory_bonus for d in modifier_sources(state, player, definitions))


def maximum_hand_size(player: PlayerState, state: GameState | None = None,
                      definitions: Mapping[str, CardDefinition] | None = None) -> int:
    """Apply active hand-size bonuses and the Runner's core damage (CR 5.5.3)."""
    bonus = sum(d.hand_size_bonus for d in modifier_sources(state, player, definitions or {})) if state else 0
    return 5 + bonus - (player.core_damage if player.role is PlayerRole.RUNNER else 0)


def install_cost(state: GameState, player: PlayerState, definition: CardDefinition,
                 definitions: Mapping[str, CardDefinition]) -> int:
    """Stack first-program discounts, flooring the final credit cost at zero."""
    discount = 0
    if (definition.card_type is CardType.PROGRAM
            and not state.turn.occurrences.get(f"program_install:{player.role}", 0)):
        discount = sum(d.first_program_discount for d in modifier_sources(state, player, definitions))
    return max(0, definition.install_cost - discount)


def installed_icebreaker_count(state, player_id, definitions) -> int:
    """Count installed icebreakers, including the querying breaker itself."""
    return sum(c.installed and c.face_up and c.controller_id == player_id
               and (definition := definitions.get(c.definition_id)) is not None
               and "Icebreaker" in definition.subtypes
               for c in state.card_instances.values())


def memory_used(state: GameState, definitions: Mapping[str, CardDefinition]) -> int:
    """Total memory claimed by installed Runner programs (CR 3.9.3)."""
    return sum(definitions[c.definition_id].memory_cost or 0
               for c in state.card_instances.values() if c.installed
               and c.controller_id == state.runner.player_id
               and c.definition_id in definitions
               and definitions[c.definition_id].card_type is CardType.PROGRAM)
