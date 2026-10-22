/** Exercise two real clients against a running API: npm run test:live.
 * ANR_API_URL selects the server. Uses ordinary decks and authenticated views.
 */
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { AgentClient, ApiError } from '../client.mjs';

test('agents host, join, choose hands, alternate priority and take actions', async () => {
  const corp = new AgentClient();
  const runner = new AgentClient();
  try {
    const decks = await corp.decks();
    const corpDeck = decks.find(d => d.role === 'corp').deck_id;
    const runnerDeck = decks.find(d => d.role === 'runner').deck_id;
    const invitation = await corp.host({ deck_id: corpDeck });
    assert(!JSON.stringify(invitation).includes('token'));
    await assert.rejects(runner.join({ join_code: invitation.join_code, deck_id: corpDeck }), error => error.status === 422);
    await runner.join({ join_code: invitation.join_code, deck_id: runnerDeck });
    await corp.observe({ format: 'json' });
    assert.equal(corp.game.initial_state.game_id, runner.game.initial_state.game_id);
    assert.notEqual(corp.game.player_token, runner.game.player_token);
    await assert.rejects(corp.request(`/games/${corp.game.initial_state.game_id}/state`, { token: invitation.join_code }), error => error.status === 403);
    const raw = JSON.stringify(corp.snapshot);
    const compact = await corp.observe({ format: 'json', full: true });
    console.log(`Initial observation: API ${raw.length} bytes; compact JSON ${compact.length} bytes`);
    assert(compact.length < raw.length);
    assert(!compact.includes(runner.snapshot.state.you.hand[0].instance_id));
    const first = JSON.parse(compact);
    assert(first.state.cards[first.state.you.hand[0]].title);
    const details = await corp.card({ ref: first.state.you.hand[0] });
    assert(details.printed_text);
    const initialWait = await corp.wait({ after_revision: corp.snapshot.revision, timeout: 0 });
    assert.equal(initialWait.status, 'waiting');
    let sawRun = false;
    const moves = new Set();
    for (let step = 0; step < 1000; step++) {
      const revision = Math.max(corp.snapshot.revision, runner.snapshot.revision);
      // Synchronize both private socket views before choosing the actor.
      const deadline = Date.now() + 3000;
      while ((corp.snapshot.revision < revision || runner.snapshot.revision < revision) && Date.now() < deadline) {
        await new Promise(resolve => setTimeout(resolve, 10));
      }
      assert.equal(corp.snapshot.revision, runner.snapshot.revision);
      const actor = corp.snapshot.state.legal_actions.length ? corp : runner;
      const state = actor.snapshot.state;
      if (state.winner) break;
      assert(state.legal_actions.length);
      const view = JSON.parse(await actor.observe({ format: 'json', full: true }));
      const actions = Object.entries(view.state.actions);
      const ice = actions.find(([, a]) => a.type === 'install_card' && a.server_id === 'hq' && view.state.cards[a.card_instance_id]?.card_type === 'ice');
      const hasIce = state.installed_cards.some(c => c.server_id === 'hq' && c.area === 'ice');
      const candidate = (state.viewer_role === 'corp' && !sawRun ? ice : undefined)
        ?? actions.find(([, a]) => a.type === 'choose_mulligan' && a.decision === 'keep')
        ?? (!sawRun && hasIce ? actions.find(([, a]) => a.type === 'run_server' && a.server_id === 'hq') : undefined)
        ?? (state.viewer_role === 'corp' && !hasIce && !sawRun ? actions.find(([, a]) => a.type === 'draw_card') : undefined)
        ?? actions.find(([, a]) => a.type === 'continue_run')
        ?? actions.find(([, a]) => ['gain_credit', 'pass_priority', 'jack_out'].includes(a.type))
        ?? actions[0];
      const [action, command] = candidate;
      sawRun ||= command.type === 'run_server';
      moves.add(`${state.viewer_role}:${command.type}`);
      const params = { revision: view.revision, action, format: 'json' };
      if (command.eligible_card_instance_ids) params.cards = command.eligible_card_instance_ids.slice(0, command.required_count);
      if (step === 0) {
        const request = actor.request.bind(actor);
        let lost = false;
        actor.request = async (...args) => {
          const response = await request(...args);
          if (!lost && args[0].endsWith('/commands')) {
            lost = true;
            throw new Error('Simulated lost HTTP response');
          }
          return response;
        };
        await assert.rejects(actor.act(params), /lost HTTP/);
        assert(actor.pending);
        const updateDeadline = Date.now() + 3000;
        while (actor.snapshot.revision === params.revision && Date.now() < updateDeadline) {
          await new Promise(resolve => setTimeout(resolve, 10));
        }
        assert.equal(actor.snapshot.revision, params.revision + 1);
        await assert.rejects(actor.act({ ...params, action: 'a999' }), /pending/);
        actor.request = request;
      }
      const result = JSON.parse(await actor.act(params));
      assert.equal(result.mode, 'delta');
      assert.equal(result.base_revision, view.revision);
      if (step === 0) {
        await assert.rejects(actor.act(params), /Stale/);
        const history = await actor.history();
        assert(history.entries.length);
      }
    }
    assert(sawRun);
    for (const move of ['corp:gain_credit', 'runner:gain_credit', 'corp:pass_priority', 'runner:run_server']) assert(moves.has(move), move);
    const oldSocket = corp.socket;
    oldSocket.terminate();
    const reconnectDeadline = Date.now() + 4000;
    while ((corp.socket === oldSocket || corp.connection !== 'connected') && Date.now() < reconnectDeadline) {
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    assert.notEqual(corp.socket, oldSocket);
    assert.equal(corp.connection, 'connected');
    assert(corp.snapshot.state.winner, 'The clients must play through game end');
    const outcome = await corp.wait({ after_revision: corp.snapshot.revision, timeout: 0, format: 'json' });
    assert.equal(typeof outcome, 'string');
    assert.equal(JSON.parse(outcome).revision, corp.snapshot.revision);
    console.log(`Live match reached revision ${corp.snapshot.revision}; exercised ${moves.size} decision types`);
  } finally { await Promise.allSettled([corp.leave(), runner.leave()]); }
});

test('practice games and cancellation use the same compact interface', async () => {
  const client = new AgentClient();
  try {
    const deck = (await client.decks()).find(d => d.role === 'runner').deck_id;
    const invitation = await client.host({ deck_id: deck });
    await client.leave();
    await assert.rejects(client.request(`/lobbies/${invitation.join_code}/join`, { method: 'POST', body: { decklist: await client.deck(deck) } }), error => error instanceof ApiError && error.status === 404);
    const deck_text = await readFile(new URL('../../../content/decks/system-gateway/starter-runner.txt', import.meta.url), 'utf8');
    await client.play_ai({ deck_text });
    const view = JSON.parse(await client.observe({ format: 'json' }));
    const inspected = await client.decks({ deck_id: deck });
    assert(inspected.decklist.card_counts);
    const card = await client.card({ definition_id: inspected.decklist.identity_definition_id });
    assert.equal(card.card_type, 'identity');
    assert.equal(view.state.viewer_role, 'runner');
    assert(view.state.actions);
  } finally { await client.leave(); }
});
