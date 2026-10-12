import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanupRoles, unusedRoles } from '../scripts/roles.ts';
import { DiscordClient, type Role } from '../src/discord.ts';

const guild = '111111111111111111';
const role: Role = { id: '222222222222222222', name: 'ff0000', color: 0xff0000, permissions: '0', managed: false };
afterEach(() => vi.restoreAllMocks());

describe('offline role maintenance', () => {
  it('selects only explicitly empty, unprivileged legacy color roles', () => {
    const shared = { ...role, id: '333333333333333333' };
    const missing = { ...role, id: '444444444444444444' };
    const privileged = { ...role, id: '555555555555555555', permissions: '8' };
    const managed = { ...role, id: '666666666666666666', managed: true };
    expect(unusedRoles([role, shared, missing, privileged, managed], {
      [role.id]: 0, [shared.id]: 1, [privileged.id]: 0, [managed.id]: 0,
    }, guild)).toEqual([role]);
  });

  it.each([{ [role.id]: 1 }, {}])('skips a role whose fresh count is nonzero or missing: %j', async (counts) => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json(counts));
    expect(await cleanupRoles(new DiscordClient('test'), guild, [role])).toEqual([]);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('rechecks member counts before deletion', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(Response.json({ [role.id]: 0 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    expect(await cleanupRoles(new DiscordClient('test'), guild, [role])).toEqual([role]);
    expect(fetch.mock.calls[0]?.[0]).toContain('/roles/member-counts');
    expect(fetch.mock.calls[1]?.[1]?.method).toBe('DELETE');
  });

  it('does not delete when count retrieval fails', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json({ code: 50013 }, { status: 403 }));
    await expect(cleanupRoles(new DiscordClient('test'), guild, [role])).rejects.toMatchObject({ status: 403 });
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
