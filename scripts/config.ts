import { DiscordClient } from '../src/discord.ts';
import { readFileSync } from 'node:fs';
import { parse, type ParseError } from 'jsonc-parser';

export function config(): { token: string; application: string } {
  const errors: ParseError[] = [];
  const vars = (parse(readFileSync(new URL('../wrangler.jsonc', import.meta.url), 'utf8'), errors, { allowTrailingComma: true }) as {
    vars: { DISCORD_APPLICATION_ID: string };
  }).vars;
  if (errors.length) throw new Error('Invalid wrangler.jsonc. Fix configuration before running maintenance.');
  const token = process.env.DISCORD_TOKEN;
  const application = process.env.DISCORD_APPLICATION_ID ?? vars.DISCORD_APPLICATION_ID;
  if (!token || !application || !/^\d{17,20}$/.test(application)) {
    throw new Error('Set DISCORD_TOKEN in .dev.vars and DISCORD_APPLICATION_ID in wrangler.jsonc.');
  }
  return { token, application };
}

export async function verifiedClient(token: string, application: string): Promise<DiscordClient> {
  // Maintenance can exceed a single Worker's background-work lifetime.
  const api = new DiscordClient(token, Date.now() + 5 * 60_000);
  const identity = await api.request<{ id: string }>('GET', '/oauth2/applications/@me');
  if (identity.id !== application) throw new Error('Bot token does not belong to DISCORD_APPLICATION_ID.');
  return api;
}
