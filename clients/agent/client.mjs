/** Stateful API client shared by Pi tools and the JSON-lines command interface.
 * The socket stays open between model calls, maintains a private full snapshot,
 * and reconnects independently of the model. Secrets never enter tool results.
 */
import { randomUUID } from 'node:crypto';
import { setTimeout as sleep } from 'node:timers/promises';
import WebSocket from 'ws';
import { Observation, render } from './observation.mjs';

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

export class AgentClient {
  constructor(api = process.env.ANR_API_URL ?? 'http://127.0.0.1:8000') {
    const url = new URL(api);
    if (!['http:', 'https:'].includes(url.protocol)) throw new Error('API URL must use HTTP or HTTPS');
    this.api = api.replace(/\/$/, '');
    this.observation = new Observation();
    this.closed = false;
    this.generation = 0;
    this.connection = 'idle';
  }

  async request(path, { method = 'GET', body, token, headers = {}, signal } = {}) {
    const response = await fetch(`${this.api}${path}`, {
      method, signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(15000)]) : AbortSignal.timeout(15000),
      headers: { ...(body ? { 'Content-Type': 'application/json' } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}), ...headers },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    if (response.status === 204) return null;
    const payload = await response.json();
    if (!response.ok) throw new ApiError(response.status, JSON.stringify(payload.detail ?? 'API request failed'));
    return payload;
  }

  async decks({ deck_id } = {}) {
    const decks = await this.request('/preconstructed-decks');
    if (deck_id) {
      const deck = decks.find(d => d.deck_id === deck_id);
      if (!deck) throw new Error('Unknown deck_id');
      return deck;
    }
    return decks.map(({ deck_id, name, role }) => ({ deck_id, name, role }));
  }

  async deck(deck_id, deck_text) {
    if (deck_id && deck_text) throw new Error('Choose exactly one of deck_id or deck_text');
    if (deck_text) return this.request('/decks/import', { method: 'POST', body: { text: deck_text } });
    const decks = await this.request('/preconstructed-decks');
    const deck = decks.find(d => d.deck_id === deck_id);
    if (!deck) throw new Error('Unknown deck_id; use anr_decks');
    return deck.decklist;
  }

  assertIdle() {
    if (this.game || this.lobby) throw new Error('Leave your current game or lobby first');
    this.closed = false;
  }

  async host({ deck_id, deck_text }) {
    this.assertIdle();
    const lobby = await this.request('/lobbies', { method: 'POST', body: { decklist: await this.deck(deck_id, deck_text) } });
    this.lobby = lobby;
    return { join_code: lobby.join_code, expires_in_seconds: lobby.expires_in_seconds, status: 'waiting_for_opponent' };
  }

  async join({ join_code, deck_id, deck_text }) {
    this.assertIdle();
    const game = await this.request(`/lobbies/${encodeURIComponent(join_code)}/join`, {
      method: 'POST', body: { decklist: await this.deck(deck_id, deck_text) },
    });
    await this.attach(game);
    return this.observe();
  }

  async play_ai({ deck_id, deck_text, opponent_profile_id }) {
    this.assertIdle();
    const profiles = await this.request('/opponent-profiles');
    const profile = opponent_profile_id ?? profiles[0]?.profile_id;
    const game = await this.request('/games', { method: 'POST', body: {
      player_id: randomUUID(), decklist: await this.deck(deck_id, deck_text), opponent_profile_id: profile,
    } });
    await this.attach(game);
    return this.observe();
  }

  accept(snapshot) {
    if (snapshot.type !== 'state' || !Number.isInteger(snapshot.revision)
      || snapshot.state?.game_id !== this.game.initial_state.game_id) throw new Error('Invalid session snapshot');
    if (!this.snapshot || snapshot.revision >= this.snapshot.revision) {
      this.snapshot = snapshot;
      this.observation.latest = snapshot;
    }
  }

  async attach(game) {
    if (this.closed) throw new Error('Client closed');
    this.game = game;
    this.closed = false;
    this.snapshot = null;
    this.observation = new Observation();
    this.connection = 'connecting';
    this.terminal = null;
    this.generation += 1;
    const generation = this.generation;
    this.controller = new AbortController();
    this.listener = this.listen(generation);
    // Initial attachment must receive an authoritative revision, not assume zero.
    const deadline = Date.now() + 10000;
    while (!this.snapshot && !this.terminal && Date.now() < deadline) await sleep(25);
    if (!this.snapshot) throw new Error(this.terminal ?? 'Could not connect; use anr_wait to retry');
  }

  async listen(generation) {
    let deadline = Date.now() + this.game.reconnect_timeout_seconds * 1000;
    while (!this.closed && generation === this.generation) {
      let received = false;
      try {
        await new Promise((resolve, reject) => {
          const url = new URL(`${this.api}/games/${this.game.initial_state.game_id}/events`);
          url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
          const socket = new WebSocket(url, {
            headers: { Authorization: `Bearer ${this.game.player_token}` }, handshakeTimeout: 3000,
          });
          this.socket = socket;
          let failure;
          const initialTimeout = setTimeout(() => socket.terminate(), 5000);
          socket.on('message', data => {
            if (generation !== this.generation || this.closed) return;
            try {
              this.accept(JSON.parse(data.toString()));
              received = true;
              clearTimeout(initialTimeout);
              this.connection = 'connected';
              deadline = Date.now() + this.game.reconnect_timeout_seconds * 1000;
            } catch {
              this.terminal = 'Invalid server snapshot';
              socket.close();
            }
          });
          socket.on('error', error => { failure = error; });
          socket.on('unexpected-response', (_request, response) => {
            if ([400, 401, 403, 404].includes(response.statusCode)) this.terminal = 'Game session unavailable';
            response.resume();
            socket.terminate();
          });
          socket.on('close', code => {
            clearTimeout(initialTimeout);
            if ([1000, 1008].includes(code) && !this.closed && generation === this.generation) this.terminal = 'Game session closed';
            failure ? reject(failure) : resolve();
          });
        });
      } catch { /* Retry transport errors within the server's reconnect window. */ }
      if (this.closed || generation !== this.generation) return;
      if (received) deadline = Date.now() + this.game.reconnect_timeout_seconds * 1000;
      if (this.terminal || Date.now() >= deadline) {
        this.terminal ??= 'Reconnect deadline exceeded';
        this.connection = 'unavailable';
        return;
      }
      this.connection = 'reconnecting';
      await sleep(250, undefined, { signal: this.controller.signal }).catch(() => {});
    }
  }

  async ensureGame(signal) {
    if (this.closed) throw new Error('Client closed');
    if (!this.game && !this.lobby) throw new Error('Host, join, or start a practice game first');
    if (this.terminal) throw new Error(this.terminal);
    if (!this.game && this.lobby) {
      const game = await this.request(`/lobbies/${this.lobby.join_code}`, { token: this.lobby.lobby_token, signal });
      if (game) await this.attach(game);
    }
    return !!this.game;
  }

  async observe({ format = 'text', after_revision, full = false } = {}) {
    if (!await this.ensureGame()) return { status: 'waiting_for_opponent', join_code: this.lobby?.join_code };
    if (!this.snapshot) throw new Error('Waiting for a session snapshot');
    return render(this.observation.observe(this.snapshot, { after_revision, full }), format);
  }

  async wait({ after_revision = -1, timeout = 30, format = 'text' } = {}, signal) {
    if (timeout < 0 || timeout > 60) throw new Error('timeout must be between 0 and 60 seconds');
    const deadline = Date.now() + timeout * 1000;
    do {
      signal?.throwIfAborted();
      if (await this.ensureGame(signal) && this.snapshot
          && (this.snapshot.state.winner || (this.snapshot.revision > after_revision && this.snapshot.state.legal_actions.length))) {
        return this.observe({ after_revision, format });
      }
      if (Date.now() >= deadline) break;
      await sleep(Math.min(200, deadline - Date.now()), undefined, { signal });
    } while (!this.closed);
    return { status: 'waiting', revision: this.snapshot?.revision, connection: this.connection };
  }

  async act({ revision, action, cards, request_id, format = 'text' }) {
    if (this.connection !== 'connected') throw new Error('Wait for the session to reconnect before acting');
    // Keep a failed request intact: an uncertain transport failure must never
    // turn a retry into a second click or a different decision.
    const intent = JSON.stringify({ revision, action, cards });
    if (this.inflight) throw new Error('Another decision is in flight');
    if (this.pending && this.pending.intent !== intent) throw new Error('Retry the pending decision before submitting another');
    if (!this.pending) this.pending = {
      intent, id: request_id ?? randomUUID(), revision,
      command: this.observation.command(revision, action, cards),
    };
    const pending = this.pending;
    this.inflight = true;
    try {
      const snapshot = await this.request(`/games/${this.game.initial_state.game_id}/commands`, {
        method: 'POST', token: this.game.player_token, body: pending.command,
        headers: { 'X-Expected-Revision': String(pending.revision), 'Idempotency-Key': pending.id },
      });
      this.accept(snapshot);
      this.pending = null;
      return this.observe({ after_revision: revision, format });
    } catch (error) {
      if (error instanceof ApiError) this.pending = null;
      throw error;
    } finally { this.inflight = false; }
  }

  async card({ ref, definition_id }) {
    if (ref && definition_id) throw new Error('Choose ref or definition_id');
    const definition = definition_id ?? this.observation.last?.board.cards?.[ref]?.definition_id;
    if (!definition) throw new Error('Card details are unavailable for this reference');
    this.catalog ??= await this.request('/card-catalog');
    const entry = this.catalog.find(c => c.definition_id === definition);
    if (!entry) throw new Error('Card definition unavailable');
    const { flavor_text, illustrator, ...rules } = entry;
    return Object.fromEntries(Object.entries(rules).filter(([, value]) => value !== null));
  }

  async history({ after_sequence = 0, limit = 30 } = {}) {
    if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw new Error('limit must be between 1 and 100');
    const entries = (this.snapshot?.state.history ?? []).filter(h => h.sequence > after_sequence);
    return { entries: entries.slice(0, limit).map(h => ({ sequence: h.sequence, text: h.detail.text })),
      more: entries.length > limit };
  }

  disconnect() {
    this.closed = true;
    this.generation += 1;
    this.controller?.abort();
    this.socket?.terminate();
    this.connection = 'closed';
  }

  async leave() {
    this.disconnect();
    try {
      if (this.game) await this.request(`/games/${this.game.initial_state.game_id}/leave`, {
        method: 'POST', token: this.game.player_token, body: {},
      });
      else if (this.lobby) await this.request(`/lobbies/${this.lobby.join_code}/leave`, {
        method: 'POST', token: this.lobby.lobby_token, body: {},
      });
    } finally {
      this.game = this.lobby = this.snapshot = this.pending = this.terminal = null;
      this.observation = new Observation();
    }
    return { status: 'left' };
  }
}
