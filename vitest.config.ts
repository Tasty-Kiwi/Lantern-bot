import { cloudflareTest } from '@cloudflare/vitest-plugin';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [cloudflareTest({
    wrangler: { configPath: './wrangler.jsonc' },
    miniflare: { bindings: { DISCORD_TOKEN: 'test-token' } },
  })],
  test: { include: ['test/**/*.test.ts'] },
});
