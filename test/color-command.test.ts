import { afterEach, describe, expect, it, vi } from 'vitest';
import { runColor, type ColorCommand } from '../src/color-command.ts';
import { DiscordClient, type Role } from '../src/discord.ts';

const guild = '111111111111111111';
const old = role('222222222222222222', 'ff0000');
const target = role('333333333333333333', 'ff69b4');
const command: ColorCommand = {
  guild, application: '444444444444444444', member: '555555555555555555',
  token: 'interaction-token', roles: [old.id], color: 'ff69b4',
};

function role(id: string, name: string, extra: Partial<Role> = {}): Role {
  return { id, name, permissions: '0', managed: false, color: parseInt(name, 16), ...extra };
}

function mockApi(roles: Role[], failure?: { method: string; suffix: string; status: number }) {
  const calls: { method: string; path: string; body?: Record<string, unknown>; authorization: string | null }[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const path = new URL(String(input)).pathname;
    const method = init?.method ?? 'GET';
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) as Record<string, unknown> : undefined;
    calls.push({ method, path, body, authorization: new Headers(init?.headers).get('Authorization') });
    if (failure?.method === method && path.endsWith(failure.suffix)) return Response.json({ code: 50013 }, { status: failure.status });
    if (method === 'GET') return Response.json(roles);
    if (method === 'POST') return Response.json(target);
    if (method === 'PATCH') return Response.json({ id: 'reply' });
    return new Response(null, { status: 204 });
  });
  return calls;
}

afterEach(() => vi.restoreAllMocks());

describe('color role changes', () => {
  it('reuses an existing role and assigns it before removing all old colors', async () => {
    const other = role('666666666666666666', '00ff00');
    const calls = mockApi([old, other, target]);
    await runColor({ ...command, roles: [old.id, other.id] }, 'bot-token');
    expect(calls.map((call) => call.method)).toEqual(['GET', 'PUT', 'DELETE', 'DELETE', 'PATCH']);
    expect(calls[1]?.path).toContain(`/members/${command.member}/roles/${target.id}`);
    expect(calls.at(-1)?.authorization).toBeNull();
    expect(calls.at(-1)?.body?.embeds).toEqual([expect.objectContaining({ description: 'Color has been changed to `ff69b4`.' })]);
    expect(calls.some((call) => call.method === 'DELETE' && !call.path.includes('/members/'))).toBe(false);
  });

  it('creates a permissionless role with the requested color', async () => {
    const calls = mockApi([old]);
    await runColor(command, 'bot-token');
    expect(calls.map((call) => call.method)).toEqual(['GET', 'POST', 'PUT', 'DELETE', 'PATCH']);
    expect(calls[1]?.body).toEqual({ name: 'ff69b4', permissions: '0',
      colors: { primary_color: 0xff69b4, secondary_color: null, tertiary_color: null }, hoist: false, mentionable: false });
  });

  it.each(['POST', 'PUT'])('keeps the old color if %s fails', async (method) => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const calls = mockApi([old], { method, suffix: method === 'POST' ? '/roles' : `/roles/${target.id}`, status: 403 });
    await runColor(command, 'bot-token');
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false);
    expect(calls.at(-1)?.body?.content).toContain('Manage Roles');
  });

  it('keeps the selected role when setting the same color again', async () => {
    const calls = mockApi([old]);
    await runColor({ ...command, color: old.name }, 'bot-token');
    expect(calls.map((call) => call.method)).toEqual(['GET', 'PATCH']);
  });

  it('matches uppercase legacy names', async () => {
    const calls = mockApi([{ ...target, name: 'FF69B4' }, old]);
    await runColor(command, 'bot-token');
    expect(calls.some((call) => call.method === 'POST')).toBe(false);
  });

  it.each([
    { permissions: '8' }, { managed: true }, { color: 0x123456 },
  ])('does not reuse an unsafe or mismatched hex-named role: %j', async (extra) => {
    const calls = mockApi([old, { ...target, id: '777777777777777777', ...extra }]);
    await runColor(command, 'bot-token');
    expect(calls.some((call) => call.method === 'POST')).toBe(true);
  });

  it('clears ordinary color roles but preserves managed, privileged and everyone roles', async () => {
    const unsafe = [role('777777777777777777', '123456', { permissions: '8' }),
      role('888888888888888888', 'abcdef', { managed: true }), role(guild, '111111')];
    const calls = mockApi([old, ...unsafe]);
    await runColor({ ...command, color: undefined, roles: [old.id, ...unsafe.map((role) => role.id)] }, 'bot-token');
    expect(calls.filter((call) => call.method === 'DELETE').map((call) => call.path)).toEqual([
      `/api/v10/guilds/${guild}/members/${command.member}/roles/${old.id}`,
    ]);
    expect(calls.at(-1)?.body?.embeds).toEqual([expect.objectContaining({ description: 'Color was removed.' })]);
  });

  it('handles clear with no selected color', async () => {
    const calls = mockApi([]);
    await runColor({ ...command, color: undefined }, 'bot-token');
    expect(calls.map((call) => call.method)).toEqual(['GET', 'PATCH']);
    expect(calls.at(-1)?.body?.embeds).toEqual([expect.objectContaining({ description: 'You do not have any colors selected!' })]);
  });

  it('reports partial removal failure without deleting the newly assigned role', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const calls = mockApi([old, target], { method: 'DELETE', suffix: `/roles/${old.id}`, status: 403 });
    await runColor(command, 'bot-token');
    expect(calls.at(-1)?.body?.content).toContain('existing color may still be present');
    expect(calls.filter((call) => call.method === 'DELETE')).toHaveLength(1);
  });

  it('tolerates an old role removed concurrently', async () => {
    const calls = mockApi([old, target], { method: 'DELETE', suffix: `/roles/${old.id}`, status: 404 });
    await runColor(command, 'bot-token');
    expect(calls.at(-1)?.body?.embeds).toBeDefined();
  });
});

describe('bounded Discord API requests', () => {
  it('honors retry_after on rate limits', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(Response.json({ retry_after: 0 }, { status: 429 }))
      .mockResolvedValueOnce(Response.json([]));
    expect(await new DiscordClient('bot-token').roles(guild)).toEqual([]);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it('does not wait past the background deadline', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json({ retry_after: 60 }, { status: 429 }));
    await expect(new DiscordClient('bot-token').roles(guild)).rejects.toMatchObject({ status: 429 });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('does not retry a failed role creation or expose Discord response bodies', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json({ message: 'secret response', code: 50013 }, { status: 500 }));
    await expect(new DiscordClient('bot-token').createRole(guild, 'ff0000')).rejects.toThrow('Discord request failed (500, code 50013)');
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
