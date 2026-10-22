import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import test from 'node:test';
import { Observation, render } from '../observation.mjs';

function fixture() {
  const card = { instance_id: randomUUID(), definition_id: 'sure-gamble', title: 'Sure Gamble', ability_labels: {}, strength: null };
  return { type: 'state', revision: 4, state: {
    game_id: randomUUID(), viewer_player_id: randomUUID(), viewer_role: 'runner',
    you: { credits: 5, hand: [card] }, opponent: { player_id: randomUUID(), credits: 8, hand_count: 5 },
    decision: { player: 'runner', type: 'choose_action' }, run: null,
    legal_actions: [{ type: 'play_card', card_instance_id: card.instance_id, server_id: null }],
    history: Array.from({ length: 20 }, (_, i) => ({ entry_id: randomUUID(), sequence: i + 1, detail: { text: `event ${i + 1}` } })),
  } };
}

test('compact projection deduplicates visible cards, preserves resources and limits history', () => {
  const snapshot = fixture();
  const projection = new Observation();
  const output = projection.observe(snapshot);
  assert.deepEqual(output.state.you.hand, ['c1']);
  assert.equal(output.state.cards.c1.title, 'Sure Gamble');
  assert.equal(output.state.you.credits, 5);
  assert.equal(output.state.actions.a1.card_instance_id, 'c1');
  assert.equal(output.history.length, 12);
  assert.equal(output.history_omitted, 8);
  const json = render(output, 'json');
  assert(!json.includes(snapshot.state.you.hand[0].instance_id));
  assert(!json.includes('entry_id'));
  assert(json.length < JSON.stringify(snapshot).length / 2);
  assert.match(render(output), /a1.*play_card/);
});

test('delta reconstructs the full board, including removed run and card fields', () => {
  const snapshot = fixture();
  snapshot.state.run = { phase: 'approach', server_id: 'hq', successful: false };
  const projection = new Observation();
  const first = projection.observe(snapshot);
  const next = structuredClone(snapshot);
  next.revision++;
  next.state.you.credits = 0;
  next.state.you.hand = [];
  next.state.run = null;
  next.state.legal_actions = [];
  next.state.history.push({ sequence: 21, detail: { text: 'Paid credits' } });
  const delta = projection.observe(next, { after_revision: 4 });
  assert.equal(delta.mode, 'delta');
  assert.equal(delta.base_revision, 4);
  const reconstructed = structuredClone(first.state);
  for (const { path, value, remove } of delta.changes) {
    const parent = path.slice(0, -1).reduce((object, key) => object[key], reconstructed);
    if (remove) delete parent[path.at(-1)];
    else parent[path.at(-1)] = value;
  }
  assert.deepEqual(reconstructed, projection.project(next));
  assert.deepEqual(delta.history, [{ sequence: 21, text: 'Paid credits' }]);
  assert.equal(projection.observe(next, { after_revision: 5 }).changes.length, 0);
  assert.equal(projection.observe(next, { after_revision: 2 }).mode, 'full');
  assert.equal(projection.observe(next, { after_revision: 5, full: true }).mode, 'full');
});

test('action references are revision scoped and selection templates enforce eligibility', () => {
  const snapshot = fixture();
  const projection = new Observation();
  const id = snapshot.state.you.hand[0].instance_id;
  snapshot.state.legal_actions = [{ type: 'discard_cards', eligible_card_instance_ids: [id], required_count: 1 }];
  projection.observe(snapshot);
  assert.deepEqual(projection.command(4, 'a1', ['c1']), { type: 'discard_cards', card_instance_ids: [id] });
  assert.throws(() => projection.command(4, 'a1', []), /required_count/);
  assert.throws(() => projection.command(4, 'a1', ['c1', 'c1']), /distinct/);
  assert.throws(() => projection.command(4, 'a1', ['unknown']), /eligible/);
  assert.throws(() => projection.command(4, 'a1'), /requires/);
  assert.throws(() => projection.command(3, 'a1', ['c1']), /Stale/);
  projection.latest = { ...snapshot, revision: 5 };
  assert.throws(() => projection.command(4, 'a1', ['c1']), /Stale/);
  snapshot.revision = 5;
  snapshot.state.legal_actions[0] = { type: 'select_effect_cards', eligible_card_instance_ids: [id], required_count: 1, optional: true };
  projection.observe(snapshot);
  assert.deepEqual(projection.command(5, 'a1', []), { type: 'select_effect_cards', card_instance_ids: [] });
});


test('a long gap still emits bounded history with an explicit omission count', () => {
  const snapshot = fixture();
  const projection = new Observation();
  projection.observe(snapshot);
  snapshot.revision = 40;
  for (let sequence = 21; sequence <= 100; sequence++) {
    snapshot.state.history.push({ sequence, detail: { text: `event ${sequence}` } });
  }
  const delta = projection.observe(snapshot, { after_revision: 4 });
  assert.equal(delta.history.length, 12);
  assert.equal(delta.history_omitted, 68);
  assert.equal(delta.history.at(-1).sequence, 100);
  assert.equal(projection.observe(snapshot, { after_revision: 40 }).history.length, 0);
});
