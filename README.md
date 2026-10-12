# Lantern

A utility Discord bot for private servers, running as a TypeScript Cloudflare
Worker. Commands are registered globally and work in every server where Lantern
is installed. Uses your existing Discord application and roles, without a Gateway
connection, database, Python server or FFmpeg.

| Command | Behavior |
| --- | --- |
| `/color set color:<value>` | Choose a six-digit hex code, with an optional `#`, or a CSS3 color name such as `hotpink`. |
| `/color clear` | Remove your color roles. |
| `/about` | Show Lantern's information. |

Color replies are ephemeral. Black (`000000` or `black`) becomes `010101` because
Discord treats zero as no role color. Existing permissionless roles named with
six hex characters remain compatible, including uppercase names. The new color
is assigned before old colors are removed, preserving the old color if creation
or assignment fails. Roles with permissions and managed roles are never reused
or removed as color roles. AI, SFX and embedify are removed.

## Local setup

Requires Node.js **22.18+** and npm. No Discord or Cloudflare credentials are
needed for tests or a build dry run.

```sh
npm ci
cp .dev.vars.example .dev.vars
```

Set `DISCORD_TOKEN` in the ignored `.dev.vars` file to the **existing** bot token.
Set these public values in `wrangler.jsonc`:

- `DISCORD_APPLICATION_ID`: the existing application's Application ID.
- `DISCORD_PUBLIC_KEY`: its 64-character hex Public Key from General Information.

Keep the application ID as a string. Keep the token out of `wrangler.jsonc` and source control.
An old `src/.env` is ignored and is not loaded by the Worker; copy only its bot
token locally, then remove obsolete AI credentials when no longer needed.

```sh
npm run types
npm run check
npm run dev
```

`npm run check` runs TypeScript, mocked regression tests in Cloudflare's runtime,
and `wrangler deploy --dry-run`. `GET /health` is a liveness check; it does not
verify credentials or Discord permissions. Signed Discord requests go to
`POST /interactions`. Other paths are rejected. An unconfigured interactions
endpoint returns 503. Local development with real credentials performs real
Discord role mutations if sent valid signed interactions.

## Deploy and cut over

1. Keep the same Discord application and bot. Confirm it is installed in the
   guild with the `bot` and `applications.commands` OAuth scopes. Grant **Manage
   Roles** and place Lantern's highest role **above all color roles**. No
   privileged Gateway intents are required by the Worker.
2. Authenticate Wrangler to your intended Cloudflare account (`npx wrangler
   login`, or your existing account-token workflow). Confirm the account with
   `npx wrangler whoami`. Install the deployed secret via the interactive prompt:
   `npx wrangler secret put DISCORD_TOKEN`. Local `.dev.vars` is not uploaded.
3. Run `npm run check`, then `npm run deploy`. Record the returned Worker URL.
   This project uses a Worker named `lantern-bot` with no storage bindings.
4. Preview global registration with `npm run commands`. Stop the old Python
   process so it cannot re-register removed commands. Run
   `npm run commands -- --apply` to replace **all global command types** with
   `/color` (set/clear subcommands) and `/about`. The script checks that the bot
   token belongs to the configured application before writing.
5. For the migration, use `npm run commands -- --apply --clear-guilds` to also
   remove **all guild-specific command types for this application in every
   installed server**. This avoids stale commands shadowing global commands.
   Global command propagation may take time. Registration never changes roles
   or member assignments.
6. In the existing Discord application's Developer Portal → General Information,
   set **Interactions Endpoint URL** to `https://<worker-host>/interactions`.
   Saving it tests a signed PING. The Worker rejects invalid signatures and
   timestamps older/newer than five minutes.
7. In Discord test `/about`, hex and named colors, setting the same color twice,
   black, invalid input, clearing with/without a color, and two members sharing
   a color. Verify permission failures leave the old color intact. Confirm
   removed commands disappear and the Python process stays stopped.

Registration and deployment are separate explicit operations. No setup script
changes the Interactions Endpoint URL or shuts down the old process. You can
roll back by clearing that URL and restarting the previous Python revision;
restore its command registration as necessary. Existing member roles are retained.
The bot may appear offline without a Gateway connection; slash commands still
work through HTTPS.

## Automatic deployment on push

The deployed Worker is `lantern-bot` in the Tasty Kiwi account:
[health check](https://lantern-bot.tasty-kiwi.workers.dev/health). Discord's
interactions URL is `https://lantern-bot.tasty-kiwi.workers.dev/interactions`.
Its runtime bot-token secret is already installed; future deploys retain it.

Open Cloudflare → Tasty Kiwi → Workers & Pages → lantern-bot → Settings →
Build/Builds. Connect the GitHub
repository `Tasty-Kiwi/Lantern-bot` and configure:

| Setting | Value |
| --- | --- |
| Production branch | `master` |
| Root directory | Repository root (`/`) |
| Build command | `npm run check` |
| Deploy command | `npm run deploy` |
| Preview builds | Disabled for now |

The checked-in `.nvmrc` selects Node 22. Let Cloudflare create its deployment API
token. The Discord token belongs under runtime **Variables & Secrets**, where it
is already configured; it does not need to be added to build variables. Do not
add command registration to the automatic build: register explicitly when the
command schema changes.

Every push to `master` runs checks and deploys to the same Worker URL. If the
production branch was previously set to `cf-worker`, change it to `master` under
Branch control. Feature branches do not deploy while preview builds are disabled.
These dashboard settings are separate from the GitHub Actions check workflow;
a local CLI deployment does not connect the Git repository automatically.
See Cloudflare's [build settings](https://developers.cloudflare.com/workers/ci-cd/builds/configuration/)
and [branch controls](https://developers.cloudflare.com/workers/ci-cd/builds/build-branches/).

## Unused role maintenance

Discord's [role member-count API](https://docs.discord.com/developers/resources/guild#get-guild-role-member-counts)
returns a snapshot. Its role-delete API has no “delete only if still unused”
condition. Deleting during `/color` could therefore erase another member's newly
assigned color. The Worker removes old roles **from the member** and leaves empty
roles available for reuse. This deliberately replaces the old automatic deletion
with offline maintenance, keeping command handling stateless.

```sh
npm run cleanup -- --guild <server-id>
```

This read-only preview lists permissionless, non-managed hex roles with an
explicit member count of zero. Missing counts or count failures never authorize
deletion. To apply, temporarily clear the Discord Interactions Endpoint URL,
leave the Python process stopped, and wait at least 30 seconds for in-flight
Worker work to finish. Avoid manual role assignment during maintenance. Then run:

```sh
npm run cleanup -- --guild <server-id> --apply --bot-stopped
```

Each candidate's count is checked again immediately before deleting. Restore the
Worker endpoint afterward. `--bot-stopped` acknowledges the operator has paused
both bots; the script cannot detect this itself. Empty roles otherwise accumulate
toward Discord's guild role limit. Concurrent color commands can create duplicate
color roles or leave multiple colors on one member; run one command at a time per
member, and use `/color clear` or set again to resolve multiple colors.

## Runtime and operations

The Worker validates Ed25519 signatures with native Web Crypto. Color commands
return a defer before API work and edit the original ephemeral reply through
`ctx.waitUntil()`. Discord's [interaction deadline](https://docs.discord.com/developers/interactions/receiving-and-responding)
is three seconds. Role operations have a 24-second total budget, with up to four
seconds reserved for the reply, within Cloudflare's
[30-second background-work limit](https://developers.cloudflare.com/workers/platform/limits/#duration).
Rate-limit retries respect Discord's `retry_after`; long waits and other failures
produce a retry message when the webhook remains reachable. Failed role creation
is not retried automatically except after an explicit 429 response. Background
work is not a durable queue: runtime termination or a Discord outage can still
leave a deferred response unresolved.

Workers Logs and sampled traces are enabled. Application error logs include only
operation names and HTTP/API codes, avoiding tokens and raw Discord payloads.
Use `npx wrangler tail` for deployed diagnostics. Free-tier suitability should be
checked against actual traffic and CPU use after deployment.

## License

Lantern is licensed under the MIT-0 License. Credit is appreciated but not required.
