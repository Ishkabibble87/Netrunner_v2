"""Translate between validated API commands and engine command values."""

from dataclasses import fields

from pydantic import BaseModel

from game import commands as cmd

from . import schemas

_COMMAND_ADAPTERS: dict[type, tuple[type[BaseModel], str]] = {
    cmd.ChooseMulligan: (schemas.MulliganCommandRequest, "choose_mulligan"),
    cmd.GainCredit: (schemas.GainCreditCommandRequest, "gain_credit"),
    cmd.DrawCard: (schemas.DrawCardCommandRequest, "draw_card"),
    cmd.RemoveTag: (schemas.RemoveTagCommandRequest, "remove_tag"),
    cmd.PurgeVirusCounters: (schemas.PurgeVirusCountersCommandRequest, "purge_virus_counters"),
    cmd.PlayCard: (schemas.PlayCardCommandRequest, "play_card"),
    cmd.DiscardCards: (schemas.DiscardCardsCommandRequest, "discard_cards"),
    cmd.TrashPrograms: (schemas.TrashProgramsCommandRequest, "trash_programs"),
    cmd.InstallCard: (schemas.InstallCardCommandRequest, "install_card"),
    cmd.AdvanceCard: (schemas.AdvanceCardCommandRequest, "advance_card"),
    cmd.ScoreAgenda: (schemas.ScoreAgendaCommandRequest, "score_agenda"),
    cmd.PassPriority: (schemas.PassPriorityCommandRequest, "pass_priority"),
    cmd.ResolveReaction: (schemas.ResolveReactionCommandRequest, "resolve_reaction"),
    cmd.RunServer: (schemas.RunServerCommandRequest, "run_server"),
    cmd.ContinueRun: (schemas.ContinueRunCommandRequest, "continue_run"),
    cmd.JackOut: (schemas.JackOutCommandRequest, "jack_out"),
    cmd.AccessCard: (schemas.AccessCardCommandRequest, "access_card"),
    cmd.FinishAccess: (schemas.FinishAccessCommandRequest, "finish_access"),
    cmd.RezCard: (schemas.RezCardCommandRequest, "rez_card"),
    cmd.UseCardAbility: (schemas.UseCardAbilityCommandRequest, "use_card_ability"),
    cmd.ChooseEffectOption: (schemas.ChooseEffectOptionRequest, "choose_effect_option"),
    cmd.SelectEffectCards: (schemas.SelectEffectCardsRequest, "select_effect_cards"),
    cmd.TrashResource: (schemas.TrashResourceRequest, "trash_resource"),
}
_REQUEST_ADAPTERS = {
    request_type: (command_type, api_type)
    for command_type, (request_type, api_type) in _COMMAND_ADAPTERS.items()
}


def _command_values(command: object) -> dict[str, object]:
    """Copy command fields into API-friendly values, normalizing tuples to lists."""
    values = {}
    for field in fields(command):
        value = getattr(command, field.name)
        values[field.name] = list(value) if isinstance(value, tuple) else value
    return values


def game_command_from_request(request: schemas.PlayerCommandRequest) -> cmd.GameCommand:
    """Translate a validated API command into the transport-independent game type."""
    adapter = _REQUEST_ADAPTERS.get(type(request))
    if adapter is None:
        raise ValueError("Unsupported API command")
    command_type, _ = adapter
    values = request.model_dump(exclude={"type"})
    values = {
        name: tuple(value) if isinstance(value, list) else value
        for name, value in values.items()
    }
    return command_type(**values)


def command_request_from_game(
    command: cmd.GameCommand | cmd.DiscardSelection | cmd.CardSelection,
) -> schemas.LegalAction:
    """Project a domain command into the shape clients can submit and display."""
    if isinstance(command, cmd.CardSelection):
        return schemas.CardSelectionAction(
            eligible_card_instance_ids=list(command.eligible_card_instance_ids),
            required_count=command.required_count,
            optional=command.optional,
            up_to=command.up_to,
        )
    if isinstance(command, cmd.DiscardSelection):
        return schemas.DiscardCardsAction(
            eligible_card_instance_ids=list(command.eligible_card_instance_ids),
            required_count=command.required_count,
        )
    adapter = _COMMAND_ADAPTERS.get(type(command))
    if adapter is None:
        raise ValueError("Unsupported game command")
    request_type, api_type = adapter
    return request_type(type=api_type, **_command_values(command))
