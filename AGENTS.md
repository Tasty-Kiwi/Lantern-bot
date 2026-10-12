# Lantern Bot

Lantern is a Discord interactions bot with global commands for installed servers, using TypeScript,
Cloudflare Workers, Web Crypto and Discord REST API v10. It has only `/color set`,
`/color clear` and `/about`. No Gateway, Python process or database is required.

## Structure

- `src/index.ts`: bounded request reading, Ed25519 verification and interaction routing.
- `src/colors.ts`: CSS3 names and six-digit hex normalization.
- `src/color-command.ts`: deferred color role operations and original reply editing.
- `src/discord.ts`: bounded REST calls and safe error handling.
- `src/commands.ts`: slash-command registration payload.
- `scripts/`: explicit command registration and offline unused-role maintenance.
- `test/`: regression tests running in workerd through Cloudflare's Vitest plugin.
- `wrangler.jsonc`: Worker config and non-secret application identifiers.
- `worker-configuration.d.ts`: generated bindings and runtime types.

## Development

Use Node.js 22.18 or newer. Install with `npm ci`. Copy `.dev.vars.example` to
`.dev.vars` and set the existing bot token; fill the public configuration in
`wrangler.jsonc`. See README for cutover steps. `npm run dev` starts the Worker.
Local development uses the real Discord API if configured with real credentials.

Run `npm run types` after changing bindings, then `npm run check` (TypeScript,
runtime tests and deploy dry run). Test API writes must be mocked. Keep the lockfile.

## Conventions

- Keep secrets out of config, source, logs and command arguments. Ignore `.dev.vars`.
- Verify the raw timestamp and request body before parsing or processing interactions.
- Return the initial color-command defer immediately; attach work to `ctx.waitUntil()`.
- Add the requested color before removing old colors. Only manage non-managed,
  permissionless roles with six-digit hex names; preserve unrelated roles.
- Never delete whole roles in the interaction path. Discord member counts and role
  deletion are separate operations. Maintenance requires both bots to be stopped.
- Bound REST timeouts and rate-limit retries to the Worker background lifetime.
- Do not log webhook URLs, interaction tokens, bot tokens or raw error response bodies.
- Keep command registration and deployment explicit; checks must not change Discord
  commands, deploy Workers, or require production credentials.
