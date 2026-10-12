export const API_BASE = 'https://discord.com/api/v10';

export interface Role {
  id: string;
  name: string;
  permissions: string;
  managed: boolean;
  color: number;
  colors?: { primary_color: number; secondary_color: number | null; tertiary_color: number | null };
}

export interface Message {
  content?: string;
  embeds?: { title: string; description: string; color: number; footer?: { text: string } }[];
  allowed_mentions?: { parse: never[] };
}

export class DiscordError extends Error {
  readonly status: number;
  readonly code?: number;

  constructor(status: number, code?: number) {
    // Never include URLs (webhook tokens), response bodies, or credentials.
    super(`Discord request failed (${status}${code ? `, code ${code}` : ''})`);
    this.name = 'DiscordError';
    this.status = status;
    this.code = code;
  }
}

export class DiscordClient {
  private readonly token: string;
  private readonly deadline: number;

  constructor(token: string, deadline = Date.now() + 24_000) {
    this.token = token;
    this.deadline = deadline;
  }

  async request<T>(method: string, path: string, body?: unknown, botAuth = true): Promise<T> {
    for (let attempt = 0; attempt < 3; attempt++) {
      const remaining = this.deadline - Date.now();
      if (remaining <= 0) throw new DiscordError(408);
      const headers: Record<string, string> = { 'Content-Type': 'application/json' };
      if (botAuth) headers.Authorization = `Bot ${this.token}`;
      const response = await fetch(`${API_BASE}${path}`, {
        method, headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(Math.min(remaining, 8_000)),
      });
      if (response.status === 204) return undefined as T;
      const data = await response.json() as { code?: number; retry_after?: number };
      if (response.status === 429 && attempt < 2) {
        const delay = Number(data.retry_after ?? response.headers.get('Retry-After')) * 1000;
        if (Number.isFinite(delay) && delay >= 0 && Date.now() + delay + 500 < this.deadline) {
          await new Promise((resolve) => setTimeout(resolve, delay));
          continue;
        }
      }
      if (!response.ok) throw new DiscordError(response.status, data.code);
      return data as T;
    }
    throw new DiscordError(429);
  }

  roles(guild: string): Promise<Role[]> {
    return this.request('GET', `/guilds/${guild}/roles`);
  }

  createRole(guild: string, color: string): Promise<Role> {
    return this.request('POST', `/guilds/${guild}/roles`, {
      name: color, permissions: '0',
      colors: { primary_color: parseInt(color, 16), secondary_color: null, tertiary_color: null },
      hoist: false, mentionable: false,
    });
  }

  memberRole(method: 'PUT' | 'DELETE', guild: string, member: string, role: string): Promise<void> {
    return this.request(method, `/guilds/${guild}/members/${member}/roles/${role}`);
  }

  editReply(application: string, token: string, message: Message): Promise<unknown> {
    return this.request('PATCH', `/webhooks/${application}/${encodeURIComponent(token)}/messages/@original`,
      { ...message, allowed_mentions: { parse: [] } }, false);
  }
}

// Legacy color roles have no permissions. Never grant or remove a privileged
// role just because its administrator happened to give it a hex-shaped name.
export function isColorRole(role: Role, guild: string): boolean {
  return role.id !== guild && /^[0-9a-f]{6}$/i.test(role.name)
    && !role.managed && role.permissions === '0';
}
