/** JSON-lines adapter for other agents: {id, method, params} -> {id, result|error}.
 * Start with `node clients/agent/main.mjs`. Stdout contains protocol messages only.
 */
import { createInterface } from 'node:readline';
import { AgentClient } from './client.mjs';

const client = new AgentClient();
const methods = new Set(['decks', 'host', 'join', 'play_ai', 'observe', 'wait', 'act', 'card', 'history', 'leave']);
try {
  for await (const line of createInterface({ input: process.stdin, crlfDelay: Infinity })) {
    let request;
    try {
      request = JSON.parse(line);
      if (!methods.has(request.method)) throw new Error('Unknown method');
      const result = await client[request.method](request.params ?? {});
      process.stdout.write(`${JSON.stringify({ id: request.id, result })}\n`);
    } catch (error) {
      process.stdout.write(`${JSON.stringify({ id: request?.id, error: error.message })}\n`);
    }
  }
} finally { client.disconnect(); }
