import { createExecutionContext, waitOnExecutionContext } from 'cloudflare:test';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import worker from '../src/index.ts';

let keys: CryptoKeyPair;
let env: Env;
const application = '111111111111111111';
const guild = '222222222222222222';
const member = '333333333333333333';
const interaction = {
  type: 2, application_id: application, guild_id: guild, token: 'interaction-token',
  member: { user: { id: member }, roles: [] },
  data: { type: 1, name: 'color', options: [{ type: 1, name: 'set', options: [{ type: 3, name: 'color', value: 'hotpink' }] }] },
};

function hex(data: ArrayBuffer | JsonWebKey): string {
  if (!(data instanceof ArrayBuffer)) throw new Error('Expected a raw key or signature');
  return Array.from(new Uint8Array(data), (byte) => byte.toString(16).padStart(2, '0')).join('');
}

async function signed(payload: unknown, timestamp = String(Math.floor(Date.now() / 1000)), raw?: string): Promise<Request> {
  const body = raw ?? JSON.stringify(payload);
  const signature = hex(await crypto.subtle.sign('Ed25519', keys.privateKey, new TextEncoder().encode(timestamp + body)));
  return new Request('https://lantern.example/interactions', {
    method: 'POST', body, headers: { 'X-Signature-Ed25519': signature, 'X-Signature-Timestamp': timestamp },
  });
}

beforeAll(async () => {
  keys = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']) as CryptoKeyPair;
  env = { DISCORD_APPLICATION_ID: application, DISCORD_TOKEN: 'test-token',
    DISCORD_PUBLIC_KEY: hex(await crypto.subtle.exportKey('raw', keys.publicKey)) };
});
afterEach(() => vi.restoreAllMocks());

async function handle(request: Request, bindings = env): Promise<Response> {
  const ctx = createExecutionContext();
  const response = await worker.fetch(request, bindings, ctx);
  await waitOnExecutionContext(ctx);
  return response;
}

describe('signed Discord interactions in workerd', () => {
  it('acknowledges a valid signed PING', async () => {
    expect(await (await handle(await signed({ type: 1 }))).json()).toEqual({ type: 1 });
  });

  it('rejects unsigned requests', async () => {
    expect((await handle(new Request('https://lantern.example/interactions', { method: 'POST', body: '{"type":1}' }))).status).toBe(401);
  });

  it('rejects body tampering', async () => {
    const valid = await signed({ type: 1 });
    const tampered = new Request(valid.url, { method: 'POST', headers: valid.headers, body: '{"type":2}' });
    expect((await handle(tampered)).status).toBe(401);
  });

  it('rejects malformed signatures and stale signed requests', async () => {
    const request = await signed({ type: 1 });
    request.headers.set('X-Signature-Ed25519', 'xx'.repeat(64));
    expect((await handle(request)).status).toBe(401);
    expect((await handle(await signed({ type: 1 }, '100'))).status).toBe(401);
  });

  it('verifies raw bytes including whitespace and unicode', async () => {
    const response = await handle(await signed(null, undefined, '{ "type": 1, "value": "ž 🌈" }'));
    expect(await response.json()).toEqual({ type: 1 });
  });

  it.each(['null', '[]', '{'])('rejects malformed signed payload %s', async (raw) => {
    expect((await handle(await signed(null, undefined, raw))).status).toBe(400);
  });

  it('fails closed when configuration is missing', async () => {
    expect((await handle(await signed({ type: 1 }), { ...env, DISCORD_PUBLIC_KEY: '' })).status).toBe(503);
  });

  it('bounds the request body', async () => {
    const request = new Request('https://lantern.example/interactions', { method: 'POST', body: 'x'.repeat(65_537) });
    expect((await handle(request)).status).toBe(413);
  });

  it('rejects DMs, malformed guild IDs and other applications before API work', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch');
    for (const guild_id of [undefined, 'not-a-guild', '../guild']) {
      expect(await (await handle(await signed({ ...interaction, guild_id }))).json()).toMatchObject({ type: 4, data: { flags: 64 } });
    }
    expect((await handle(await signed({ ...interaction, application_id: '999999999999999999' }))).status).toBe(403);
    expect(fetch).not.toHaveBeenCalled();
  });

  it('accepts commands from other installed servers and uses their guild ID', async () => {
    const otherGuild = '999999999999999999';
    const fetch = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(Response.json([]))
      .mockResolvedValueOnce(Response.json({ id: '444444444444444444', name: 'ff69b4' }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(Response.json({ id: 'reply' }));
    expect(await (await handle(await signed({ ...interaction, guild_id: otherGuild }))).json()).toEqual({ type: 5, data: { flags: 64 } });
    expect(fetch.mock.calls[0]?.[0]).toContain(`/guilds/${otherGuild}/roles`);
    expect(fetch.mock.calls[2]?.[0]).toContain(`/guilds/${otherGuild}/members/${member}/roles/`);
  });

  it('returns about without the removed AI privacy policy', async () => {
    const response = await handle(await signed({ ...interaction, data: { type: 1, name: 'about' } }));
    const body = await response.text();
    expect(body).toContain('About Lantern');
    expect(body).not.toContain('Nvidia');
    expect(body).not.toContain('memories');
  });

  it('rejects invalid colors ephemerally without Discord API calls', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch');
    const data = { type: 1, name: 'color', options: [{ type: 1, name: 'set', options: [{ type: 3, name: 'color', value: '@everyone' }] }] };
    expect(await (await handle(await signed({ ...interaction, data }))).json()).toMatchObject({ type: 4, data: { flags: 64 } });
    expect(fetch).not.toHaveBeenCalled();
  });

  it('defers before slow Discord operations finish, then edits the ephemeral original', async () => {
    let release!: (response: Response) => void;
    const slow = new Promise<Response>((resolve) => { release = resolve; });
    const fetch = vi.spyOn(globalThis, 'fetch')
      .mockImplementationOnce(() => slow)
      .mockResolvedValueOnce(Response.json({ id: '444444444444444444', name: 'ff69b4' }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(Response.json({ id: 'reply' }));
    const ctx = createExecutionContext();
    const response = await worker.fetch(await signed(interaction), env, ctx);
    expect(await response.json()).toEqual({ type: 5, data: { flags: 64 } });
    expect(fetch).toHaveBeenCalledTimes(1);
    release(Response.json([]));
    await waitOnExecutionContext(ctx);
    expect(fetch).toHaveBeenCalledTimes(4);
    expect(fetch.mock.calls.at(-1)?.[0]).toContain('/messages/@original');
  });

  it('routes clear to member-role removal', async () => {
    const role = '444444444444444444';
    const fetch = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(Response.json([{ id: role, name: 'ff69b4', permissions: '0', managed: false }]))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(Response.json({ id: 'reply' }));
    const response = await handle(await signed({ ...interaction, member: { user: { id: member }, roles: [role] },
      data: { type: 1, name: 'color', options: [{ type: 1, name: 'clear' }] } }));
    expect(await response.json()).toEqual({ type: 5, data: { flags: 64 } });
    expect(fetch.mock.calls[1]?.[1]?.method).toBe('DELETE');
  });

  it('handles unknown and malformed commands without API work', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch');
    for (const data of [{ type: 1, name: 'ai' }, { type: 1, name: 'color', options: [] },
      { type: 1, name: 'color', options: [{ type: 1, name: 'set' }] }]) {
      expect(await (await handle(await signed({ ...interaction, data }))).json()).toMatchObject({ type: 4 });
    }
    expect(await (await handle(await signed({ ...interaction, member: null }))).json()).toMatchObject({ type: 4 });
    expect(fetch).not.toHaveBeenCalled();
  });
});
