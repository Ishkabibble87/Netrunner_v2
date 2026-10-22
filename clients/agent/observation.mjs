/** Model-facing projections of already filtered API views; never read engine state.
 * Short card references survive revisions. Actions are scoped to the revision that
 * offered them. A caller supplies its last emitted revision to opt into deltas.
 */
const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const uuid = /^[\da-f]{8}-[\da-f-]{27}$/i;

function compact(value) {
  if (Array.isArray(value)) return value.map(compact);
  if (!value || typeof value !== 'object') return value;
  return Object.fromEntries(Object.entries(value)
    .filter(([, v]) => v !== null && !(Array.isArray(v) && !v.length)
      && !(v && typeof v === 'object' && !Array.isArray(v) && !Object.keys(v).length))
    .map(([k, v]) => [k, compact(v)]));
}

function changes(before, after, path = []) {
  if (equal(before, after)) return [];
  if (before && after && !Array.isArray(before) && !Array.isArray(after)
      && typeof before === 'object' && typeof after === 'object') {
    return [...new Set([...Object.keys(before), ...Object.keys(after)])]
      .flatMap(key => changes(before[key], after[key], [...path, key]));
  }
  return [{ path, ...(after === undefined ? { remove: true } : { value: after }) }];
}

export class Observation {
  constructor() {
    this.refs = new Map();
    this.ids = new Map();
    this.last = null;
    this.latest = null;
  }

  ref(id) {
    if (!this.refs.has(id)) {
      const ref = `c${this.refs.size + 1}`;
      this.refs.set(id, ref);
      this.ids.set(ref, id);
    }
    return this.refs.get(id);
  }

  project(snapshot) {
    const state = snapshot.state;
    const cards = {};
    const convert = value => {
      if (typeof value === 'string' && uuid.test(value)) {
        if (value === state.viewer_player_id) return 'you';
        if (value === state.opponent.player_id) return 'opponent';
        return this.ref(value);
      }
      if (Array.isArray(value)) return value.map(convert);
      if (!value || typeof value !== 'object') return value;
      if (value.instance_id && value.title) {
        const ref = this.ref(value.instance_id);
        const { instance_id, ...detail } = value;
        cards[ref] = compact(Object.fromEntries(Object.entries(detail).map(([k, v]) => [k, convert(v)])));
        return ref;
      }
      return compact(Object.fromEntries(Object.entries(value).map(([k, v]) => [k === 'instance_id' ? 'ref' : k, convert(v)])));
    };
    const { game_id, viewer_player_id, history, legal_actions, setup, ...view } = state;
    const board = convert(view);
    board.cards = cards;
    board.actions = Object.fromEntries(legal_actions.map((action, i) => [`a${i + 1}`, convert(action)]));
    return board;
  }

  observe(snapshot, { after_revision, full = false } = {}) {
    this.latest = snapshot;
    const board = this.project(snapshot);
    const previous = this.last;
    const delta = !full && previous && after_revision === previous.revision
      && snapshot.revision >= previous.revision;
    const visibleHistory = snapshot.state.history ?? [];
    const availableHistory = delta ? visibleHistory.filter(h => h.sequence > previous.sequence) : visibleHistory;
    const history = availableHistory.slice(-12);
    const result = {
      revision: snapshot.revision,
      mode: delta ? 'delta' : 'full',
      ...(delta ? { base_revision: previous.revision, changes: changes(previous.board, board) } : { state: board }),
      history: history.map(h => ({ sequence: h.sequence, text: h.detail.text })),
      ...(availableHistory.length > history.length ? { history_omitted: availableHistory.length - history.length } : {}),
    };
    this.last = { board, revision: snapshot.revision, sequence: visibleHistory.at(-1)?.sequence ?? 0 };
    this.offered = { revision: snapshot.revision, actions: snapshot.state.legal_actions };
    return result;
  }

  command(revision, action, cards) {
    if (!this.offered || revision !== this.offered.revision || revision !== this.latest.revision) {
      throw new Error('Stale action reference: observe the current revision first');
    }
    if (!/^a[1-9]\d*$/.test(action)) throw new Error('Unknown action reference');
    const offered = this.offered.actions[Number(action.slice(1)) - 1];
    if (!offered) throw new Error('Unknown action reference');
    const command = structuredClone(offered);
    if (command.eligible_card_instance_ids) {
      if (!Array.isArray(cards)) throw new Error('This action requires a cards selection (use [] to decline if optional)');
      const ids = cards.map(ref => this.ids.get(ref));
      if (new Set(ids).size !== ids.length || ids.some(id => !command.eligible_card_instance_ids.includes(id))) {
        throw new Error('Select distinct eligible card references');
      }
      const count = command.required_count;
      if (ids.length > count || (!command.up_to && ids.length !== count && !(command.optional && !ids.length))) {
        throw new Error(`Selection must satisfy required_count=${count}, optional=${!!command.optional}, up_to=${!!command.up_to}`);
      }
      return { type: command.type, card_instance_ids: ids };
    }
    if (cards !== undefined) throw new Error('This action does not accept a cards selection');
    return command;
  }
}

/** Render the same observation contract as compact text or minified JSON. */
export function render(observation, format = 'text') {
  if (!['text', 'json'].includes(format)) throw new Error('format must be text or json');
  if (format === 'json') return JSON.stringify(observation);
  const { revision, mode, state, changes: delta, history, history_omitted } = observation;
  const lines = [`Revision ${revision} · ${mode}${mode === 'delta' ? ` since ${observation.base_revision}` : ''}`];
  if (mode === 'full') {
    for (const [key, value] of Object.entries(state)) {
      if (['cards', 'actions'].includes(key)) {
        lines.push(`${key}:`);
        for (const [ref, detail] of Object.entries(value)) lines.push(`${ref} ${JSON.stringify(detail)}`);
      } else lines.push(`${key}: ${JSON.stringify(value)}`);
    }
  } else for (const change of delta) {
    lines.push(`${change.path.join('.')} ${change.remove ? 'removed' : `= ${JSON.stringify(change.value)}`}`);
  }
  if (history_omitted) lines.push(`History: ${history_omitted} older entries omitted; use anr_history to read them.`);
  for (const entry of history) lines.push(`#${entry.sequence} ${entry.text}`);
  return lines.join('\n');
}
