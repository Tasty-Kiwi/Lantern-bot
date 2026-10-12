import { parseColor } from './colors.ts';
import { runColor } from './color-command.ts';

const EPHEMERAL = 64;
const SNOWFLAKE = /^\d{17,20}$/;

function message(content: string): Response {
  return Response.json({ type: 4, data: { content, flags: EPHEMERAL, allowed_mentions: { parse: [] } } });
}

function bytes(hex: string): Uint8Array<ArrayBuffer> {
  return Uint8Array.from(hex.match(/../g) ?? [], (byte) => parseInt(byte, 16));
}

async function verified(request: Request, body: Uint8Array, publicKey: string): Promise<boolean> {
  const signature = request.headers.get('X-Signature-Ed25519');
  const timestamp = request.headers.get('X-Signature-Timestamp');
  if (!signature || !/^[0-9a-f]{128}$/i.test(signature) || !timestamp || !/^\d+$/.test(timestamp)) return false;
  if (Math.abs(Date.now() / 1000 - Number(timestamp)) > 300) return false;
  try {
    const key = await crypto.subtle.importKey('raw', bytes(publicKey), 'Ed25519', false, ['verify']);
    const prefix = new TextEncoder().encode(timestamp);
    const signed = new Uint8Array(prefix.length + body.length);
    signed.set(prefix);
    signed.set(body, prefix.length);
    return await crypto.subtle.verify('Ed25519', key, bytes(signature), signed);
  } catch {
    return false;
  }
}

async function readBody(request: Request): Promise<Uint8Array<ArrayBuffer> | null> {
  // Bound even chunked/untrusted requests before buffering for signature checks.
  const reader = request.body?.getReader();
  if (!reader) return new Uint8Array();
  const chunks: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.length;
    if (size > 65_536) { await reader.cancel(); return null; }
    chunks.push(value);
  }
  const body = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.length; }
  return body;
}

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const path = new URL(request.url).pathname;
    if (path === '/health' && request.method === 'GET') return Response.json({ status: 'ok' });
    if (path !== '/interactions') return new Response('Not found', { status: 404 });
    if (request.method !== 'POST') return new Response('Method not allowed', { status: 405, headers: { Allow: 'POST' } });
    if (!/^[0-9a-f]{64}$/i.test(env.DISCORD_PUBLIC_KEY)
      || !SNOWFLAKE.test(env.DISCORD_APPLICATION_ID) || !env.DISCORD_TOKEN) {
      return new Response('Bot is not configured', { status: 503 });
    }
    const body = await readBody(request);
    if (body === null) return new Response('Payload too large', { status: 413 });
    if (!await verified(request, body, env.DISCORD_PUBLIC_KEY)) return new Response('Invalid signature', { status: 401 });
    let interaction: unknown;
    try { interaction = JSON.parse(new TextDecoder().decode(body)); }
    catch { return new Response('Invalid JSON', { status: 400 }); }
    if (!object(interaction)) return new Response('Invalid interaction', { status: 400 });
    if (interaction.type === 1) return Response.json({ type: 1 });
    if (interaction.type !== 2 || !object(interaction.data)) return message('Unsupported interaction.');
    if (interaction.application_id !== env.DISCORD_APPLICATION_ID) return new Response('Wrong application', { status: 403 });
    if (typeof interaction.guild_id !== 'string' || !SNOWFLAKE.test(interaction.guild_id)) return message('Use Lantern in a server where the bot is installed.');
    if (interaction.data.type !== 1) return message('Unsupported command.');
    if (interaction.data.name === 'about') {
      return Response.json({ type: 4, data: { embeds: [{
        title: 'About Lantern',
        description: 'Hi! I am Lantern, a utility bot for private servers. Use `/color set` to choose a profile color or `/color clear` to remove it. Powered by Cloudflare Workers.',
        color: 0x3498db,
        footer: { text: 'v6.0.0, © 2026 tasty kiwi' },
      }], allowed_mentions: { parse: [] } } });
    }
    if (interaction.data.name !== 'color') return message('Unknown command. Please update Lantern’s registered commands.');
    const options = interaction.data.options;
    const sub = Array.isArray(options) && options.length === 1 ? options[0] : undefined;
    if (!object(sub) || sub.type !== 1 || (sub.name !== 'set' && sub.name !== 'clear')) return message('Use `/color set` or `/color clear`.');
    let color: string | undefined;
    if (sub.name === 'set') {
      const values = sub.options;
      const option = Array.isArray(values) && values.length === 1 ? values[0] : undefined;
      if (!object(option) || option.name !== 'color' || option.type !== 3 || typeof option.value !== 'string') return message('Please provide a color.');
      const parsed = parseColor(option.value);
      if (parsed === null) return message('Received an invalid color. Please use a six-digit hex code (e.g. `ff0011`) or a CSS3 color name (e.g. `hotpink`).');
      color = parsed;
    }
    const member = interaction.member;
    if (!object(member) || !object(member.user) || typeof member.user.id !== 'string'
      || !SNOWFLAKE.test(member.user.id) || !Array.isArray(member.roles)
      || !member.roles.every((role) => typeof role === 'string' && SNOWFLAKE.test(role))
      || typeof interaction.token !== 'string' || !interaction.token) return message('This command requires a server member.');
    ctx.waitUntil(runColor({
      application: env.DISCORD_APPLICATION_ID, token: interaction.token,
      guild: interaction.guild_id, member: member.user.id, roles: member.roles, color,
    }, env.DISCORD_TOKEN));
    return Response.json({ type: 5, data: { flags: EPHEMERAL } });
  },
} satisfies ExportedHandler<Env>;
