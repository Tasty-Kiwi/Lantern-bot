import { cloudflareTest } from '@cloudflare/vitest-plugin';
import { defineConfig } from 'vitest/config';

// Wrangler validates required secrets before Miniflare bindings are applied.
// Supply a dummy only inside the test process; production keeps its real secret.
process.env.DISCORD_TOKEN = 'test-token';

export default defineConfig({
  plugins: [cloudflareTest({
    wrangler: { configPath: './wrangler.jsonc' },
    miniflare: { bindings: { DISCORD_TOKEN: 'test-token' } },
  })],
  test: { include: ['test/**/*.test.ts'] },
});
