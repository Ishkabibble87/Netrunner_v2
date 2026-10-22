/** Load with `pi --extension ./clients/agent/extension.ts` after `npm ci` here.
 * One extension instance owns one player seat. The model receives observations
 * and action references; session credentials remain inside the client.
 */
import { Type } from '@earendil-works/pi-ai';
import { defineTool, type ExtensionAPI } from '@earendil-works/pi-coding-agent';
import { AgentClient } from './client.mjs';

export default function (pi: ExtensionAPI) {
  const client = new AgentClient();
  // Serialize tools so a wait/observe cannot race a join or replace action refs.
  let queue: Promise<unknown> = Promise.resolve();
  const format = Type.Optional(Type.Union([Type.Literal('text'), Type.Literal('json')]));
  const revision = Type.Integer({ minimum: 0 });
  const deck = {
    deck_id: Type.Optional(Type.String()),
    deck_text: Type.Optional(Type.String({ description: 'NetrunnerDB deck export text; choose exactly one of deck_id or deck_text' })),
  };
  const tools = [
    ['decks', 'List available decks and sides, or inspect one deck including its card counts.', Type.Object({ deck_id: Type.Optional(Type.String()) })],
    ['host', 'Host a game. Share the join code; call anr_wait while the other player joins.',
      Type.Object(deck)],
    ['join', 'Join a human or agent host using an opposite-side deck.',
      Type.Object({ join_code: Type.String(), ...deck })],
    ['play_ai', 'Start a practice game against a scripted opponent.',
      Type.Object({ ...deck, opponent_profile_id: Type.Optional(Type.String()) })],
    ['observe', 'Read your private state and legal actions. Supply your last received revision for changes; full=true resets the view. Action references expire when revision changes.',
      Type.Object({ after_revision: Type.Optional(revision), full: Type.Optional(Type.Boolean()), format })],
    ['wait', 'Wait for your next decision, including priority during the opponent turn, or game end. Waiting does not pass priority. Retry after a waiting result.',
      Type.Object({ after_revision: Type.Optional(Type.Integer({ minimum: -1 })), timeout: Type.Optional(Type.Number({ minimum: 0, maximum: 60 })), format })],
    ['act', 'Choose an action reference from your observation at that revision. For a selection action supply distinct eligible card references. Retry identical arguments after a transport error.',
      Type.Object({ revision, action: Type.String(), cards: Type.Optional(Type.Array(Type.String())), format })],
    ['card', 'Read printed rules and stats for a visible card reference or a public definition ID.', Type.Object({ ref: Type.Optional(Type.String()), definition_id: Type.Optional(Type.String()) })],
    ['history', 'Read older visible history omitted from compact observations.',
      Type.Object({ after_sequence: Type.Optional(Type.Integer({ minimum: 0 })), limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 100 })) })],
    ['leave', 'Leave your current game or cancel your pending invitation.', Type.Object({})],
  ] as const;
  for (const [method, description, parameters] of tools) {
    pi.registerTool(defineTool({
      name: `anr_${method}`, label: `Netrunner ${method}`, description, parameters,
      async execute(_id, params, signal) {
        const work = queue.then(() => {
          signal?.throwIfAborted();
          return client[method](params, signal);
        });
        queue = work.catch(() => {});
        const result = await work;
        return { content: [{ type: 'text', text: typeof result === 'string' ? result : JSON.stringify(result) }], details: {} };
      },
    }));
  }
  pi.on('session_shutdown', () => client.disconnect());
}
