import os
import json
import asyncio
import datetime
import math
import random
import re
import sqlite3
import uuid as uuid_mod
import discord
from discord.ext import commands
import openai
import httpx
import webcolors
from mcp import ClientSession
from mcp.client.sse import sse_client

GUILD_ID = os.getenv("GUILD_ID")
GUILD_IDS = [int(GUILD_ID)] if GUILD_ID else None

_SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP_CONFIG_PATH = os.getenv("MCP_CONFIG") or os.path.join(_SRC_DIR, "mcp.json")
DB_PATH = os.path.join(_SRC_DIR, "sessions.db")
NO_DB = os.getenv("LANTERN_NO_DB") == "1"

SYSTEM_PROMPT = """You are Lantern, a helpful AI assistant for a Discord community server.

Your purpose is to help community members with their questions, research topics, and assist with various tasks. You have access to web search and browsing tools that let you find up-to-date information.

Be concise. Answer directly with minimal fluff. Use tools when needed, summarize results briefly, and keep responses short."""

MAX_HISTORY = 20


class StreamableHTTPServer:
    def __init__(self, name: str, url: str, headers: dict | None = None):
        self.name = name
        self.url = url
        self.headers = headers or {}
        self.tools = []
        self._client: httpx.AsyncClient | None = None
        self._session_id: str | None = None

    async def connect(self):
        accept_hdrs = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        headers = {**self.headers, **accept_hdrs}
        self._client = httpx.AsyncClient(headers=headers)
        r = await self._client.post(
            self.url,
            json={
                "jsonrpc": "2.0", "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "ember", "version": "1.0"},
                },
                "id": 1,
            },
        )
        self._session_id = r.headers.get("mcp-session-id")
        if not self._session_id:
            raise RuntimeError(f"Server did not return mcp-session-id: {r.text[:200]}")
        _parse_sse_json(r.text)
        await self._client.post(
            self.url,
            json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            headers={"mcp-session-id": self._session_id},
        )
        r2 = await self._client.post(
            self.url,
            json={"jsonrpc": "2.0", "method": "tools/list", "id": 2},
            headers={"mcp-session-id": self._session_id},
        )
        result = _parse_sse_json(r2.text)
        tools_data = result.get("result", {}).get("tools", [])
        self.tools = [_mcp_tool_from_dict(t) for t in tools_data]

    async def disconnect(self):
        if self._client:
            await self._client.aclose()
            self._client = None
        self._session_id = None

    async def call_tool(self, name: str, arguments: dict) -> str:
        if not self._client or not self._session_id:
            return "Tool not available."
        try:
            r = await self._client.post(
                self.url,
                json={
                    "jsonrpc": "2.0", "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                    "id": 3,
                },
                headers={"mcp-session-id": self._session_id},
            )
            result = _parse_sse_json(r.text)
            content = result.get("result", {}).get("content", [])
            parts = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(item.get("text", str(item)))
                else:
                    parts.append(str(item))
            return "\n".join(parts) if parts else str(result)
        except Exception as e:
            return f"Tool error: {e}"


class SSEServer:
    def __init__(self, name: str, url: str, headers: dict | None = None):
        self.name = name
        self.url = url
        self.headers = headers or {}
        self.tools = []
        self._sse_ctx = None
        self._session_ctx = None

    async def connect(self):
        self._sse_ctx = sse_client(url=self.url, headers=self.headers)
        self._read, self._write = await self._sse_ctx.__aenter__()
        self._session_ctx = ClientSession(self._read, self._write)
        self.session = await self._session_ctx.__aenter__()
        await self.session.initialize()
        tools_result = await self.session.list_tools()
        self.tools = tools_result.tools

    async def disconnect(self):
        if self._session_ctx:
            try:
                await self._session_ctx.__aexit__(None, None, None)
            except Exception:
                pass
            self._session_ctx = None
        if self._sse_ctx:
            try:
                await self._sse_ctx.__aexit__(None, None, None)
            except Exception:
                pass
            self._sse_ctx = None

    async def call_tool(self, name: str, arguments: dict) -> str:
        if not hasattr(self, "session") or not self.session:
            return "Tool not available."
        try:
            result = await self.session.call_tool(name, arguments)
            if hasattr(result, "content") and result.content:
                parts = []
                for item in result.content:
                    if hasattr(item, "text"):
                        parts.append(item.text)
                    else:
                        parts.append(str(item))
                return "\n".join(parts)
            return str(result)
        except Exception as e:
            return f"Tool error: {e}"


def _mcp_tool_from_dict(d: dict):
    class _Tool:
        name = d.get("name", "")
        description = d.get("description", "")
        inputSchema = d.get("inputSchema") or d.get("input_schema", {})
    return _Tool()


def _parse_sse_json(body: str) -> dict:
    import re
    m = re.search(r'^data: (.+)$', body, re.MULTILINE)
    if m:
        return json.loads(m.group(1))
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise RuntimeError(f"Could not parse SSE response: {body[:300]}")


def _build_server(name: str, opts: dict):
    transport = opts.get("transport", "sse")
    url = opts.get("url", "")
    headers = opts.get("headers") or {}
    if transport == "streamable-http":
        return StreamableHTTPServer(name, url, headers)
    return SSEServer(name, url, headers)


class MCPManager:
    def __init__(self):
        self.servers: list = []
        self._tool_map: dict[str, tuple | str] = {}

    async def connect_all(self):
        config_path = MCP_CONFIG_PATH
        if not os.path.exists(config_path):
            print(f"[Lantern AI] {config_path} not found \u2014 running without web tools")
            return
        try:
            with open(config_path) as f:
                cfg = json.load(f)
        except Exception as e:
            print(f"[Lantern AI] failed to read {config_path}: {e}")
            return
        servers_cfg = cfg.get("servers", {})
        if not servers_cfg:
            print("[Lantern AI] no servers in config \u2014 running without web tools")
            return
        for name, opts in servers_cfg.items():
            url = opts.get("url")
            if not url:
                print(f"[Lantern AI] skipping server '{name}' \u2014 no url")
                continue
            server = _build_server(name, opts)
            try:
                await server.connect()
                self.servers.append(server)
                for t in server.tools:
                    prefixed = f"{name}.{t.name}"
                    self._tool_map[prefixed] = (server, t.name)
                tool_names = [f"{name}.{t.name}" for t in server.tools]
                print(f"[Lantern AI] MCP '{name}' connected \u2014 tools: {tool_names}")
            except Exception as e:
                print(f"[Lantern AI] MCP '{name}' connection failed: {e}")
        total_tools = len(self._tool_map)
        if total_tools:
            print(f"[Lantern AI] {len(self.servers)} MCP server(s) connected \u2014 {total_tools} tool(s) available")
        else:
            print("[Lantern AI] no MCP tools available")

    async def disconnect_all(self):
        for server in self.servers:
            await server.disconnect()
        self.servers.clear()
        self._tool_map.clear()

    async def call_tool(self, prefixed_name: str, arguments: dict) -> str:
        entry = self._tool_map.get(prefixed_name)
        if not entry:
            return f"Tool '{prefixed_name}' not found."
        server, bare_name = entry
        return await server.call_tool(bare_name, arguments)

    def get_openai_tools(self):
        if not self._tool_map:
            return None
        seen = set()
        result = []
        for server in self.servers:
            for t in server.tools:
                name = f"{server.name}.{t.name}"
                if name in seen:
                    continue
                seen.add(name)
                fn = {"name": name, "description": t.description or ""}
                schema = getattr(t, "inputSchema", None) or {}
                fn["parameters"] = schema if schema else {"type": "object", "properties": {}}
                result.append({"type": "function", "function": fn})
        return result if result else None


class Database:
    def __init__(self):
        self._lock = asyncio.Lock()

    def _run(self, fn, *args, **kwargs):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            c = conn.cursor()
            result = fn(c, *args, **kwargs)
            conn.commit()
            return result
        finally:
            conn.close()

    async def init(self):
        def _init(c):
            c.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id INTEGER PRIMARY KEY,
                    owner_id INTEGER NOT NULL,
                    user_context TEXT,
                    search_enabled INTEGER NOT NULL DEFAULT 0,
                    last_activity REAL NOT NULL,
                    last_channel_id INTEGER,
                    last_message_id INTEGER
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tool_call_id TEXT,
                    tool_calls TEXT,
                    created_at REAL NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id)")
            for col in ("last_channel_id", "last_message_id"):
                try:
                    c.execute(f"ALTER TABLE sessions ADD COLUMN {col} INTEGER")
                except sqlite3.OperationalError:
                    pass
        await asyncio.to_thread(self._run, _init)

    async def save_session(self, session_id, owner_id, user_context, search_enabled, last_channel_id=None, last_message_id=None):
        def _save(c):
            c.execute(
                "INSERT OR REPLACE INTO sessions (session_id, owner_id, user_context, search_enabled, last_activity, last_channel_id, last_message_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, owner_id, user_context, int(search_enabled), datetime.datetime.now(datetime.timezone.utc).timestamp(), last_channel_id, last_message_id),
            )
        await asyncio.to_thread(self._run, _save)

    async def touch_session(self, session_id):
        def _touch(c):
            c.execute("UPDATE sessions SET last_activity = ? WHERE session_id = ?",
                      (datetime.datetime.now(datetime.timezone.utc).timestamp(), session_id))
        await asyncio.to_thread(self._run, _touch)

    async def save_message(self, session_id, role, content, tool_call_id=None, tool_calls=None):
        def _save(c):
            content_str = json.dumps(content, default=str) if isinstance(content, list) else str(content)
            tc_str = json.dumps(tool_calls, default=str) if tool_calls else None
            c.execute(
                "INSERT INTO messages (session_id, role, content, tool_call_id, tool_calls, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, role, content_str, tool_call_id, tc_str, datetime.datetime.now(datetime.timezone.utc).timestamp()),
            )
        await asyncio.to_thread(self._run, _save)

    async def delete_session(self, session_id):
        def _del(c):
            c.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            c.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        await asyncio.to_thread(self._run, _del)

    async def cleanup(self, ttl_hours=24):
        def _clean(c):
            cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=ttl_hours)).timestamp()
            c.execute("DELETE FROM messages WHERE session_id IN (SELECT session_id FROM sessions WHERE last_activity < ?)", (cutoff,))
            c.execute("DELETE FROM sessions WHERE last_activity < ?", (cutoff,))
        await asyncio.to_thread(self._run, _clean)

    async def load_all(self):
        def _load(c):
            sessions = {}
            owners = {}
            user_contexts = {}
            search_enabled = {}
            last_msgs = {}
            for row in c.execute("SELECT * FROM sessions"):
                sid = row["session_id"]
                owners[sid] = row["owner_id"]
                if row["user_context"]:
                    user_contexts[sid] = row["user_context"]
                search_enabled[sid] = bool(row["search_enabled"])
                ch = row["last_channel_id"]
                msg = row["last_message_id"]
                if ch and msg:
                    last_msgs[sid] = (ch, msg)
            for row in c.execute("SELECT * FROM messages ORDER BY id"):
                sid = row["session_id"]
                if sid not in sessions:
                    sessions[sid] = []
                m = {"role": row["role"]}
                raw = row["content"]
                try:
                    parsed = json.loads(raw)
                    m["content"] = parsed if isinstance(parsed, list) else raw
                except (json.JSONDecodeError, TypeError):
                    m["content"] = raw
                if row["tool_call_id"]:
                    m["tool_call_id"] = row["tool_call_id"]
                if row["tool_calls"]:
                    try:
                        m["tool_calls"] = json.loads(row["tool_calls"])
                    except json.JSONDecodeError:
                        pass
                sessions[sid].append(m)
            return sessions, owners, user_contexts, search_enabled, last_msgs
        return await asyncio.to_thread(self._run, _load)


class SessionManager:
    def __init__(self):
        self.db = Database() if not NO_DB else None
        self.sessions = {}
        self.owners = {}
        self.user_contexts = {}
        self.search_enabled = {}
        self._last_msgs = {}

    async def init_db(self):
        if self.db:
            await self.db.init()

    async def load_all(self):
        if not self.db:
            return
        await self.db.cleanup(ttl_hours=24)
        s, o, uc, se, lm = await self.db.load_all()
        self.sessions.update(s)
        self.owners.update(o)
        self.user_contexts.update(uc)
        self.search_enabled.update(se)
        self._last_msgs.update(lm)
        if self.sessions:
            print(f"[Lantern AI] Restored {len(self.sessions)} session(s) from DB")

    def get(self, session_id: int):
        return self.sessions.get(session_id)

    async def create(self, session_id: int, owner_id: int, user_context: str | None = None):
        now = datetime.datetime.now(datetime.timezone.utc)
        date_msg = {
            "role": "system",
            "content": f"Today is {now.strftime('%A, %Y-%m-%d')}. The current UTC time is {now.strftime('%H:%M:%S')}.",
        }
        self.sessions[session_id] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            date_msg,
        ]
        self.owners[session_id] = owner_id
        if user_context:
            self.user_contexts[session_id] = user_context
        if self.db:
            search = self.search_enabled.get(session_id, False)
            await self.db.save_session(session_id, owner_id, user_context, search)
        return self.sessions[session_id]

    async def add_message(self, session_id: int, msg: dict):
        if session_id not in self.sessions:
            now = datetime.datetime.now(datetime.timezone.utc)
            date_msg = {
                "role": "system",
                "content": f"Today is {now.strftime('%A, %Y-%m-%d')}. The current UTC time is {now.strftime('%H:%M:%S')}.",
            }
            self.sessions[session_id] = [
                {"role": "system", "content": SYSTEM_PROMPT},
                date_msg,
            ]
        self.sessions[session_id].append(msg)
        if self.db:
            await self.db.save_message(
                session_id,
                msg.get("role", ""),
                msg.get("content", ""),
                tool_call_id=msg.get("tool_call_id"),
                tool_calls=msg.get("tool_calls"),
            )
        if len(self.sessions[session_id]) > MAX_HISTORY + 2:
            self.sessions[session_id] = self.sessions[session_id][:2] + self.sessions[session_id][-MAX_HISTORY:]

    async def forget(self, session_id: int):
        self.sessions.pop(session_id, None)
        self.owners.pop(session_id, None)
        self.user_contexts.pop(session_id, None)
        self.search_enabled.pop(session_id, None)
        self._last_msgs.pop(session_id, None)
        if self.db:
            await self.db.delete_session(session_id)

    def has(self, session_id: int) -> bool:
        return session_id in self.sessions

    async def set_search(self, session_id: int, enabled: bool):
        self.search_enabled[session_id] = enabled
        if self.db:
            owner = self.owners.get(session_id, 0)
            ctx = self.user_contexts.get(session_id)
            ch, msg = self._last_msgs.get(session_id, (None, None))
            await self.db.save_session(session_id, owner, ctx, enabled, ch, msg)

    async def set_last_msg(self, session_id: int, channel_id: int, message_id: int):
        self._last_msgs[session_id] = (channel_id, message_id)
        if self.db:
            owner = self.owners.get(session_id, 0)
            ctx = self.user_contexts.get(session_id)
            se = self.search_enabled.get(session_id, False)
            await self.db.save_session(session_id, owner, ctx, se, channel_id, message_id)


class FollowUpModal(discord.ui.Modal):
    def __init__(self, cog, user_id: int, session_key: int, channel_id: int):
        super().__init__(title="Ask a follow-up")
        self.cog = cog
        self.user_id = user_id
        self.session_key = session_key
        self.channel_id = channel_id
        self._search_enabled = False
        self.add_item(discord.ui.InputText(
            label="Your question",
            style=discord.InputTextStyle.long,
            placeholder="Type your follow-up question here...",
            required=True,
        ))

    def to_components(self) -> list[dict]:
        components = super().to_components()
        checked = self.cog.sessions.search_enabled.get(self.session_key, False)
        components.append({
            "type": 18,
            "label": "Enable web search",
            "component": {
                "type": 23,
                "custom_id": "search_checkbox",
                "value": checked,
            },
        })
        return components

    def refresh(self, interaction: discord.Interaction, data: list[dict]):
        for parent in data:
            if parent.get("type") == 18:
                inner = parent.get("component", {})
                if inner.get("custom_id") == "search_checkbox":
                    self._search_enabled = inner.get("value", False)
            else:
                for comp in parent.get("components", []):
                    for child in self.children:
                        if child.custom_id == comp.get("custom_id"):
                            child.refresh_from_modal(interaction, comp)
                            break

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This follow-up is not for you.", ephemeral=True)
            return
        await self.cog.sessions.set_search(self.session_key, self._search_enabled)
        await interaction.response.defer()
        question = self.children[0].value
        print(f"[Lantern AI] {interaction.user} (ID: {self.user_id}): follow-up \"{question[:200]}\"")
        await self.cog.sessions.add_message(self.session_key, {"role": "user", "content": question})
        channel = self.cog.bot.get_channel(self.channel_id)
        prefix = f"<@{self.user_id}> asked:\n> {question[:500]}\n\n"
        msg = await interaction.followup.send(content=f"{prefix}-# Thinking...")
        view = FollowUpView(self.cog, self.user_id, self.session_key, self.channel_id)
        async with self.cog._lock:
            await self.cog._stream_response(self.session_key, msg, destination=channel, author_id=self.user_id, prefix=prefix, view=view)


class FollowUpView(discord.ui.View):
    def __init__(self, cog, user_id: int, session_key: int, channel_id: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.user_id = user_id
        self.session_key = session_key
        self.channel_id = channel_id

    @discord.ui.button(label="\U0001f4ac Ask follow-up", style=discord.ButtonStyle.primary)
    async def follow_up_button(self, button, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This follow-up is not for you.", ephemeral=True)
            return
        modal = FollowUpModal(self.cog, self.user_id, self.session_key, self.channel_id)
        await interaction.response.send_modal(modal)

    async def on_timeout(self):
        await self.cog.sessions.forget(self.session_key)


class Chat(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.sessions = SessionManager()
        self.mcp = MCPManager()
        self.ai = openai.OpenAI(
            api_key=os.getenv("NVIDIA_API_KEY"),
            base_url=os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        )
        self.model = os.getenv("NVIDIA_MODEL", "deepseek-v4-flash")
        self.vision_model = os.getenv("NVIDIA_VISION_MODEL", "meta/llama-3.2-11b-vision-instruct")
        self._lock = asyncio.Lock()
        bot.loop.create_task(self._init_mcp())

    async def _init_mcp(self):
        await self.bot.wait_until_ready()
        await self.sessions.init_db()
        await self.sessions.load_all()
        await self._restore_views()
        await self.mcp.connect_all()

    async def _restore_views(self):
        for sid, (ch_id, msg_id) in list(self.sessions._last_msgs.items()):
            owner = self.sessions.owners.get(sid)
            if not owner:
                continue
            try:
                ch = self.bot.get_channel(ch_id)
                if ch:
                    old = await ch.fetch_message(msg_id)
                    view = FollowUpView(self, owner, sid, ch_id)
                    await old.edit(view=view)
            except (discord.HTTPException, discord.NotFound):
                pass

    def cog_unload(self):
        asyncio.ensure_future(self.mcp.disconnect_all())

    def _answer_session_key(self, user_id: int, channel_id: int) -> int:
        h = hash((user_id, channel_id))
        return -(abs(h) % (10**17) + 1)

    BUILTIN_TOOLS = [
        {
            "type": "function",
            "function": {
                "name": "get_date_time",
                "description": "Get the current date, time, and timezone",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "calculate",
                "description": "Evaluate a mathematical expression safely",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "expression": {
                            "type": "string",
                            "description": "Math expression (e.g., '2 + 2', 'sqrt(144)', 'sin(pi/4)')",
                        }
                    },
                    "required": ["expression"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "random_number",
                "description": "Generate a random number within a range",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "min": {"type": "integer", "description": "Minimum value (default 0)"},
                        "max": {"type": "integer", "description": "Maximum value (default 100)"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "generate_uuid",
                "description": "Generate a UUID (version 4)",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "set_user_color",
                "description": "Change a server member's role color. Use when someone asks to change their name color, role color, or set a custom color.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {
                            "type": "string",
                            "description": "The Discord user ID of the person whose color to change",
                        },
                        "color": {
                            "type": "string",
                            "description": "Color name (e.g., 'pink', 'hotpink', 'blue') or hex code (e.g., 'ff69b4') without #",
                        },
                    },
                    "required": ["user_id", "color"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "clear_user_color",
                "description": "Remove a server member's color role, reverting them to the default role color. Use when someone asks to remove their color or reset.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {
                            "type": "string",
                            "description": "The Discord user ID of the person whose color to remove",
                        }
                    },
                    "required": ["user_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get current weather for a location. No API key needed.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {
                            "type": "string",
                            "description": "City name (e.g., 'London', 'New York', 'Tokyo')",
                        }
                    },
                    "required": ["location"],
                },
            },
        },
    ]

    _BUILTIN_NAMES = {t["function"]["name"] for t in BUILTIN_TOOLS}
    COLOR_REGEX = re.compile(r"^[0-9a-fA-F]{6}$")

    async def _call_builtin_tool(self, name: str, args: dict, destination=None, requester_id: int | None = None) -> str:
        if name == "get_date_time":
            now = datetime.datetime.now(datetime.timezone.utc)
            return (
                f"Current date and time: {now.strftime('%A, %Y-%m-%d %H:%M:%S')}\n"
                f"Timezone: UTC\n"
                f"Unix timestamp: {int(now.timestamp())}"
            )
        elif name == "calculate":
            expr = args.get("expression", "")
            allowed = {
                "abs", "round", "min", "max", "sum", "pow",
                "sqrt", "sin", "cos", "tan", "pi", "e",
                "floor", "ceil", "log", "log10", "log2",
                "radians", "degrees", "factorial",
            }
            try:
                ns = {k: getattr(math, k, None) for k in allowed}
                result = eval(expr, {"__builtins__": {}}, ns)
                return str(result)
            except Exception as e:
                return f"Calculation error: {e}"
        elif name == "random_number":
            lo = args.get("min", 0)
            hi = args.get("max", 100)
            return str(random.randint(lo, hi))
        elif name == "generate_uuid":
            return str(uuid_mod.uuid4())
        elif name == "set_user_color":
            return await self._handle_set_color(args, destination, requester_id)
        elif name == "clear_user_color":
            return await self._handle_clear_color(args, destination, requester_id)
        elif name == "get_weather":
            return await self._handle_weather(args)
        return f"Unknown built-in tool: {name}"

    async def _handle_set_color(self, args: dict, destination, requester_id: int | None = None) -> str:
        guild = getattr(destination, "guild", None) if destination else None
        if not guild:
            return "Error: could not determine the server from the conversation context."
        user_id = args.get("user_id", "")
        color_input = args.get("color", "").strip().lower()
        try:
            uid = int(user_id.strip("<@!>"))
        except (ValueError, AttributeError):
            return f"Error: invalid user_id '{user_id}'."
        if requester_id is not None and uid != requester_id:
            return "You can only change your own color."
        member = guild.get_member(uid)
        if not member:
            return f"Error: user with ID {uid} not found in this server."
        if color_input.startswith("#"):
            color_input = color_input[1:]
        if not self.COLOR_REGEX.match(color_input):
            try:
                hex_val = webcolors.name_to_hex(color_input)
                color_input = hex_val[1:]
            except ValueError:
                return (
                    f"Invalid color '{color_input}'. Use a hex code (e.g., ff69b4) "
                    f"or a CSS3 color name (e.g., hotpink)."
                )
        if color_input == "000000":
            color_input = "010101"
        color_role = discord.utils.get(guild.roles, name=color_input)
        if not color_role:
            try:
                color_int = int(color_input, 16)
                color_role = await guild.create_role(
                    name=color_input,
                    color=discord.Color(color_int),
                    permissions=discord.Permissions.none(),
                    reason=f"Color set via Lantern AI for {member} ({member.id})",
                )
            except discord.HTTPException as e:
                return f"Failed to create color role: {e}"
        existing = [r for r in member.roles if self.COLOR_REGEX.match(r.name)]
        for role in existing:
            try:
                if len(role.members) <= 1:
                    await role.delete(reason="Replacing color via Lantern AI")
                else:
                    await member.remove_roles(role, reason="Replacing color via Lantern AI")
            except discord.HTTPException:
                pass
        try:
            await member.add_roles(color_role)
        except discord.HTTPException as e:
            return f"Failed to assign color role: {e}"
        return f"Color for <@{uid}> set to `{color_input}`."

    async def _handle_clear_color(self, args: dict, destination, requester_id: int | None = None) -> str:
        guild = getattr(destination, "guild", None) if destination else None
        if not guild:
            return "Error: could not determine the server from the conversation context."
        user_id = args.get("user_id", "")
        try:
            uid = int(user_id.strip("<@!>"))
        except (ValueError, AttributeError):
            return f"Error: invalid user_id '{user_id}'."
        if requester_id is not None and uid != requester_id:
            return "You can only clear your own color."
        member = guild.get_member(uid)
        if not member:
            return f"Error: user with ID {uid} not found in this server."
        removed = False
        for role in member.roles:
            if self.COLOR_REGEX.match(role.name):
                try:
                    if len(role.members) <= 1:
                        await role.delete(reason="Clearing color via Lantern AI")
                    else:
                        await member.remove_roles(role, reason="Clearing color via Lantern AI")
                    removed = True
                except discord.HTTPException:
                    pass
        if removed:
            return f"Color removed for <@{uid}>."
        return f"<@{uid}> does not have a color role set."

    def _get_all_tools(self):
        mcp_tools = self.mcp.get_openai_tools()
        builtin = list(self.BUILTIN_TOOLS)
        if mcp_tools:
            return mcp_tools + builtin
        return builtin if builtin else None

    def _render_markdown_tables(self, text: str) -> str:
        lines = text.split("\n")
        result = []
        i = 0
        while i < len(lines):
            if lines[i].strip().startswith("|") and "|" in lines[i]:
                table_lines = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    table_lines.append(lines[i].strip())
                    i += 1
                if len(table_lines) >= 2 and re.match(r"^\|[-:| ]+\|$", table_lines[1]):
                    headers = [c.strip() for c in table_lines[0].split("|")[1:-1]]
                    rows = []
                    for row_line in table_lines[2:]:
                        cells = [c.strip() for c in row_line.split("|")[1:-1]]
                        if cells:
                            rows.append(cells)
                    if headers:
                        col_widths = [len(h) for h in headers]
                        for row in rows:
                            for ci, cell in enumerate(row):
                                if ci < len(col_widths):
                                    col_widths[ci] = max(col_widths[ci], len(cell))
                        top = "┌" + "┬".join("─" * w for w in col_widths) + "┐"
                        head = "│" + "│".join(
                            h.ljust(col_widths[ci]) for ci, h in enumerate(headers)
                        ) + "│"
                        mid = "├" + "┼".join("─" * w for w in col_widths) + "┤"
                        body = "\n".join(
                            "│" + "│".join(
                                c.ljust(col_widths[ci]) for ci, c in enumerate(row)
                            ) + "│"
                            for row in rows
                        )
                        bot = "└" + "┴".join("─" * w for w in col_widths) + "┘"
                        result.append(f"```\n{top}\n{head}\n{mid}\n{body}\n{bot}\n```")
                        continue
            result.append(lines[i])
            i += 1
        return "\n".join(result)

    WMO_CODES = {
        0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
        45: "Foggy", 48: "Depositing rime fog",
        51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
        56: "Light freezing drizzle", 57: "Dense freezing drizzle",
        61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
        66: "Light freezing rain", 67: "Heavy freezing rain",
        71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
        77: "Snow grains",
        80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
        85: "Slight snow showers", 86: "Heavy snow showers",
        95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
    }

    async def _handle_weather(self, args: dict) -> str:
        location = args.get("location", "").strip()
        if not location:
            return "Please specify a location."

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                geo = await client.get(
                    "https://geocoding-api.open-meteo.com/v1/search",
                    params={"name": location, "count": 1, "language": "en", "format": "json"},
                )
                geo.raise_for_status()
                geo_data = geo.json()
                if not geo_data.get("results"):
                    return f"Could not find a location named '{location}'."
                result = geo_data["results"][0]
                lat, lon = result["latitude"], result["longitude"]
                name = f"{result.get('name', location)}, {result.get('country', '')}"

                weather = await client.get(
                    "https://api.open-meteo.com/v1/forecast",
                    params={
                        "latitude": lat, "longitude": lon,
                        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
                        "timezone": "auto",
                    },
                )
                weather.raise_for_status()
                w = weather.json()["current"]

                code = self.WMO_CODES.get(w.get("weather_code"), "Unknown")
                temp = w.get("temperature_2m", "?")
                feels = w.get("apparent_temperature", "?")
                humidity = w.get("relative_humidity_2m", "?")
                wind = w.get("wind_speed_10m", "?")

                allergens = await client.get(
                    "https://air-quality-api.open-meteo.com/v1/air-quality",
                    params={
                        "latitude": lat, "longitude": lon,
                        "current": "alder_pollen,birch_pollen,grass_pollen,mugwort_pollen,olive_pollen,ragweed_pollen",
                    },
                )
                parts = [
                    f"Weather in {name}: {code}, {temp}\u00b0C (feels like {feels}\u00b0C). "
                    f"Humidity: {humidity}%. Wind: {wind} km/h."
                ]
                if allergens.status_code == 200:
                    a = allergens.json().get("current", {})
                    pollen = [
                        f"{k.replace('_pollen', '').title()} {v}"
                        for k, v in a.items() if isinstance(v, (int, float)) and v is not None
                    ]
                    if pollen:
                        parts.append("Allergens: " + ", ".join(pollen) + " grains/m\u00b3")
                return "\n".join(parts)
        except httpx.HTTPError as e:
            return f"Weather API error: {e}"

    def _build_user_context(self, guild, member_id: int, user=None) -> str | None:
        if guild:
            member = guild.get_member(member_id)
            if member:
                parts = [f"Discord ID: {member_id}", f"Username: {member.name}"]
                if member.nick:
                    parts.append(f"Nickname: {member.nick}")
                parts.append(f"Display name: {member.display_name}")
                parts.append(f"Server: {guild.name}")
                if member.guild_permissions.administrator:
                    parts.append("Role: Admin")
                elif member.guild_permissions.manage_guild:
                    parts.append("Role: Moderator")
                top_roles = [r.name for r in member.roles[-3:] if r.name != "@everyone"]
                if top_roles:
                    parts.append(f"Roles: {', '.join(top_roles)}")
                created_ago = (datetime.datetime.now(datetime.timezone.utc) - member.created_at).days
                parts.append(f"Account age: {created_ago} days")
                return " | ".join(parts)

        if user:
            parts = [f"Discord ID: {member_id}", f"Username: {user.name}", f"Display name: {user.display_name}"]
            created_ago = (datetime.datetime.now(datetime.timezone.utc) - user.created_at).days
            parts.append(f"Account age: {created_ago} days")
            return " | ".join(parts)

        return None

    async def _stream_response(self, session_id: int, msg: discord.Message, destination=None, author_id: int | None = None, prefix="", view=None):
        if not self.sessions.get(session_id):
            await msg.edit(content=f"{prefix}Session not found.")
            return

        tools = self._get_all_tools()
        if not self.sessions.search_enabled.get(session_id):
            tools = [t for t in (tools or []) if t["function"]["name"] in self._BUILTIN_NAMES] or None

        seen_calls = set()
        inline_notes = []
        final_text = None

        for attempt in range(5):
            messages = self.sessions.get(session_id)

            ctx_parts = []
            stored_context = self.sessions.user_contexts.get(session_id)
            if stored_context:
                ctx_parts.append({"role": "system", "content": stored_context})

            if destination:
                guild = getattr(destination, "guild", None)
                if guild and author_id:
                    mentioned = set()
                    for m in messages:
                        if m.get("role") == "user":
                            txt = ""
                            c = m.get("content", "")
                            if isinstance(c, str):
                                txt = c
                            elif isinstance(c, list):
                                for part in c:
                                    if isinstance(part, dict) and part.get("type") == "text":
                                        txt = part.get("text", "")
                            for uid in re.findall(r"<@!?(\d+)>", txt):
                                mentioned.add(int(uid))
                    mentioned.discard(author_id)
                    for uid in mentioned:
                        m = guild.get_member(uid)
                        if m:
                            info = [f"Discord ID: {uid}", f"Username: {m.name}"]
                            if m.nick:
                                info.append(f"Nickname: {m.nick}")
                            top_roles = [r.name for r in m.roles[-3:] if r.name != "@everyone"]
                            if top_roles:
                                info.append(f"Roles: {', '.join(top_roles)}")
                            ctx_parts.append({"role": "system", "content": " | ".join(info)})
            ai_messages = messages + ctx_parts

            try:
                response = await asyncio.wait_for(
                    self._ai_complete(ai_messages, tools), timeout=120
                )
            except asyncio.TimeoutError:
                await msg.edit(content=f"{prefix}AI service timed out. Please try again.")
                return
            except Exception as e:
                err = str(e)
                if "does not support image" in err.lower():
                    await msg.edit(content=f"{prefix}I cannot process this file type.")
                else:
                    await msg.edit(content=f"{prefix}AI service error: {err[:200]}")
                return

            choice = response.choices[0]

            if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
                for tc in choice.message.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        args = {}

                    sig = (tc.function.name, json.dumps(args, sort_keys=True))
                    if sig in seen_calls:
                        result = "Tool was already called with the same arguments."
                    else:
                        seen_calls.add(sig)
                        bare_name = tc.function.name.split(".")[-1]
                        note = f"-# Used: {bare_name}"
                        if note not in inline_notes:
                            inline_notes.append(note)

                        print(f"[Lantern AI] Tool call: {tc.function.name}({json.dumps(args)[:500]})")

                        if tc.function.name in self._BUILTIN_NAMES:
                            result = await self._call_builtin_tool(tc.function.name, args, destination, requester_id=author_id)
                        else:
                            result = await self.mcp.call_tool(tc.function.name, args)

                    await self.sessions.add_message(
                        session_id,
                        {"role": "tool", "tool_call_id": tc.id, "content": result},
                    )

                notes_text = "\n".join(inline_notes)
                await msg.edit(content=f"{prefix}{notes_text}\n-# Thinking...")

                assistant_msg = {"role": "assistant", "content": ""}
                assistant_msg["tool_calls"] = [
                    {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in choice.message.tool_calls
                ]
                await self.sessions.add_message(session_id, assistant_msg)
                continue

            final_text = choice.message.content or ""
            break

        if final_text is None:
            await msg.edit(content=f"{prefix}I had trouble processing your request. Please try again.")
            return

        final_text = "\n".join(l for l in final_text.split("\n") if "Used tools:" not in l).rstrip()
        final_text = self._render_markdown_tables(final_text)
        await self.sessions.add_message(session_id, {"role": "assistant", "content": final_text})

        notes_text = "\n".join(inline_notes)
        separator = "\n\n" if inline_notes and final_text else ""
        full = f"{prefix}{notes_text}{separator}{final_text}"

        total_len = len(full)
        duration = max(2, min(10, total_len / 80))

        import time as time_mod
        start = time_mod.monotonic()
        revealed = 0

        while revealed < total_len:
            elapsed = time_mod.monotonic() - start
            target = min(total_len, int((elapsed / duration) * total_len))
            if target > revealed:
                revealed = target
                try:
                    await msg.edit(content=full[:revealed])
                except discord.HTTPException:
                    break
            await asyncio.sleep(0.25)

        kwargs = {"content": full}
        if view:
            kwargs["view"] = view
            prev = self.sessions._last_msgs.get(session_id)
            if prev:
                try:
                    ch = self.bot.get_channel(prev[0])
                    if ch:
                        old = await ch.fetch_message(prev[1])
                        await old.edit(view=None)
                except (discord.HTTPException, discord.NotFound):
                    pass
            await self.sessions.set_last_msg(session_id, msg.channel.id, msg.id)
        try:
            await msg.edit(**kwargs)
        except discord.HTTPException:
            pass

    ai = discord.SlashCommandGroup("ai", "Lantern AI commands", guild_ids=GUILD_IDS)

    @ai.command(name="answer", description="Get a quick answer from Lantern AI")
    async def answer(
        self,
        ctx: discord.ApplicationContext,
        message: str = discord.Option(str, description="Your question for Lantern AI"),
        search: bool = discord.Option(bool, description="Allow web search tools", default=False),
        upload: discord.Attachment = discord.Option(discord.SlashCommandOptionType.attachment, description="Image, video, audio, PDF, or text file", required=False, default=None),
    ):
        await ctx.defer()

        print(f"[Lantern AI] {ctx.author} (ID: {ctx.author.id}): /ai answer \"{message[:200]}\" search={search} upload={'yes' if upload else 'no'}")

        session_key = self._answer_session_key(ctx.author.id, ctx.channel_id)
        await self.sessions.create(session_key, ctx.author.id, user_context=self._build_user_context(ctx.guild, ctx.author.id, ctx.author))
        await self.sessions.set_search(session_key, search)

        if upload:
            ct = upload.content_type or ""
            ext = (upload.filename or "").rsplit(".", 1)[-1].lower() if upload.filename else ""
            import base64
            raw = await upload.read()

            IMAGE_EXTS = {"png", "jpg", "jpeg", "webp"}
            VIDEO_EXTS = {"mp4", "mov", "webm"}
            AUDIO_EXTS = {"wav", "mp3"}

            if ct.startswith("text/") or ct in (
                "application/json", "application/xml", "application/yaml",
                "application/python", "application/javascript",
            ):
                text = raw.decode("utf-8", errors="replace")
                text = f"{message}\n\n```\n{text[:50000]}```" if message else f"```\n{text[:50000]}```"
                await self.sessions.add_message(session_key, {"role": "user", "content": text})

            elif ext in IMAGE_EXTS and ct.startswith("image/"):
                b64 = base64.b64encode(raw).decode()
                await self.sessions.add_message(session_key, {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": message or "What's in this image?"},
                        {"type": "image_url", "image_url": {"url": f"data:{ct};base64,{b64}"}},
                    ],
                })

            elif ext in VIDEO_EXTS and ct.startswith("video/"):
                b64 = base64.b64encode(raw).decode()
                await self.sessions.add_message(session_key, {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": message or "What's in this video?"},
                        {"type": "video_url", "video_url": {"url": f"data:{ct};base64,{b64}"}},
                    ],
                })

            elif ext in AUDIO_EXTS and ct.startswith("audio/"):
                b64 = base64.b64encode(raw).decode()
                fmt = ext or ct.split("/")[-1]
                await self.sessions.add_message(session_key, {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": message or "What's in this audio?"},
                        {"type": "input_audio", "input_audio": {"data": b64, "format": fmt}},
                    ],
                })

            elif ext == "pdf" or ct == "application/pdf":
                import fitz
                doc = fitz.open(stream=raw, filetype="pdf")
                parts = [{"type": "text", "text": message or "What's in this PDF?"}]
                for i, page in enumerate(doc):
                    if i >= 10:
                        parts.append({"type": "text", "text": f"... and {len(doc) - 10} more pages"})
                        break
                    pix = page.get_pixmap(dpi=150)
                    img_bytes = pix.tobytes("png")
                    b64 = base64.b64encode(img_bytes).decode()
                    parts.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
                doc.close()
                await self.sessions.add_message(session_key, {"role": "user", "content": parts})

            else:
                await ctx.followup.send(
                    f"Unsupported file type `.{ext}`. Supported formats:\n"
                    f"- Images: png, jpg, jpeg, webp\n"
                    f"- Video: mp4, mov, webm\n"
                    f"- Audio: wav, mp3\n"
                    f"- PDF\n"
                    f"- Text/code files"
                )
                return
        else:
            await self.sessions.add_message(session_key, {"role": "user", "content": message or "..."})

        async with self._lock:
            msg = await ctx.followup.send(content="-# Thinking...")
            await self._stream_response(session_key, msg, destination=ctx.channel, author_id=ctx.author.id, view=FollowUpView(self, ctx.author.id, session_key, ctx.channel_id))

    async def _ai_complete(self, messages: list, tools) -> openai.types.chat.ChatCompletion:
        is_multimodal = any(isinstance(m.get("content"), list) for m in messages)
        model = self.vision_model if is_multimodal else self.model
        return await asyncio.to_thread(
            self.ai.chat.completions.create,
            model=model,
            messages=messages,
            tools=tools if not is_multimodal else None,
            tool_choice=None if is_multimodal else ("auto" if tools else None),
        )


def setup(bot):
    bot.add_cog(Chat(bot))
