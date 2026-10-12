import { commands } from '../src/commands.ts';
import { config, verifiedClient } from './config.ts';

async function main(): Promise<void> {
  const flags = process.argv.slice(2);
  if (flags.some((flag) => flag !== '--apply' && flag !== '--clear-guilds')) {
    throw new Error('Usage: npm run commands -- [--apply] [--clear-guilds]');
  }
  const { token, application } = config();
  console.log(JSON.stringify({ application, scope: 'global', commands, clearGuilds: flags.includes('--clear-guilds') }, null, 2));
  if (!flags.includes('--apply')) {
    console.log('Dry run. --apply replaces all global application commands with /color and /about. --clear-guilds also removes guild-specific commands in every installed server.');
    return;
  }
  const api = await verifiedClient(token, application);
  await api.request('PUT', `/applications/${application}/commands`, commands);
  if (flags.includes('--clear-guilds')) {
    let after = '0';
    for (;;) {
      const guilds = await api.request<{ id: string; name: string }[]>('GET', `/users/@me/guilds?limit=200&after=${after}`);
      for (const guild of guilds) {
        await api.request('PUT', `/applications/${application}/guilds/${guild.id}/commands`, []);
        console.log(`Cleared guild-specific commands in ${guild.name} (${guild.id}).`);
      }
      if (guilds.length < 200) break;
      after = guilds[guilds.length - 1]!.id;
    }
  }
  console.log('Registered /color and /about globally for all servers where Lantern is installed.');
}

main().catch((error: unknown) => {
  console.error(error instanceof Error ? error.message : 'Command registration failed.');
  process.exitCode = 1;
});
