import { config, verifiedClient } from './config.ts';
import { cleanupRoles, unusedRoles } from './roles.ts';

async function main(): Promise<void> {
  const args = process.argv.slice(2);
  const guildIndex = args.indexOf('--guild');
  const guild = guildIndex >= 0 ? args[guildIndex + 1] : undefined;
  const flags = args.filter((_, index) => index !== guildIndex && index !== guildIndex + 1);
  if (!guild || !/^\d{17,20}$/.test(guild)) throw new Error('Choose a maintenance server: npm run cleanup -- --guild <server-id> [--apply --bot-stopped]');
  if (flags.some((flag) => flag !== '--apply' && flag !== '--bot-stopped')) {
    throw new Error('Usage: npm run cleanup -- --guild <server-id> [--apply --bot-stopped]');
  }
  if (flags.includes('--apply') && !flags.includes('--bot-stopped')) {
    throw new Error('Stop both the Worker and Python bot before cleanup; acknowledge with --bot-stopped.');
  }
  const { token, application } = config();
  const api = await verifiedClient(token, application);
  const roles = await api.roles(guild);
  const counts = await api.request<Record<string, number>>('GET', `/guilds/${guild}/roles/member-counts`);
  const unused = unusedRoles(roles, counts, guild);
  console.log(JSON.stringify({ guild, unused: unused.map(({ id, name }) => ({ id, name })) }, null, 2));
  if (!flags.includes('--apply')) {
    console.log('Dry run. Stop both bot instances, then use --apply --bot-stopped to delete these unused roles.');
    return;
  }
  for (const role of await cleanupRoles(api, guild, unused)) {
    console.log(`Deleted unused color role ${role.name} (${role.id}).`);
  }
}

main().catch((error: unknown) => {
  console.error(error instanceof Error ? error.message : 'Role cleanup failed.');
  process.exitCode = 1;
});
