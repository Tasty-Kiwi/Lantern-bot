import { DiscordClient, isColorRole, type Role } from '../src/discord.ts';

export function unusedRoles(roles: Role[], counts: Record<string, number>, guild: string): Role[] {
  return roles.filter((role) => isColorRole(role, guild) && counts[role.id] === 0);
}

// Only call while all bot instances are stopped. Discord has no conditional
// role deletion; even fresh member counts cannot protect against new assignments.
export async function cleanupRoles(api: DiscordClient, guild: string, roles: Role[]): Promise<Role[]> {
  const deleted: Role[] = [];
  for (const role of roles) {
    const latest = await api.request<Record<string, number>>('GET', `/guilds/${guild}/roles/member-counts`);
    if (unusedRoles([role], latest, guild).length) {
      await api.request('DELETE', `/guilds/${guild}/roles/${role.id}`);
      deleted.push(role);
    }
  }
  return deleted;
}
