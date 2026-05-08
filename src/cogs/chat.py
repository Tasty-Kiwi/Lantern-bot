import os
import json
import asyncio
import datetime
import math
import random
import re
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

SYSTEM_PROMPT = """You are Lantern, a helpful AI assistant for a Discord community server.

Your purpose is to help community members with their questions, research topics, and assist with various tasks. You have access to web search and browsing tools that let you find up-to-date information.

Be concise. Answer directly with minimal fluff. Use tools when needed, summarize results briefly, and keep responses short."""

MAX_HISTORY = 20
MAX_RESPONSE_LENGTH = 3800
MAX_FOLLOWUP_LENGTH = 1900
MESSAGE_DELAY = 1


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


class SessionManager:
    def __init__(self):
        self.sessions = {}
        self.owners = {}
        self.user_contexts = {}
        self.search_enabled = {}

    def get(self, session_id: int):
        return self.sessions.get(session_id)

    def create(self, session_id: int, owner_id: int, user_context: str | None = None):
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
        return self.sessions[session_id]

    def add_message(self, session_id: int, msg: dict):
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
        if len(self.sessions[session_id]) > MAX_HISTORY + 2:
            self.sessions[session_id] = self.sessions[session_id][:2] + self.sessions[session_id][-MAX_HISTORY:]

    def forget(self, session_id: int):
        self.sessions.pop(session_id, None)
        self.owners.pop(session_id, None)
        self.user_contexts.pop(session_id, None)
        self.search_enabled.pop(session_id, None)

    def has(self, session_id: int) -> bool:
        return session_id in self.sessions


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
        self.cog.sessions.search_enabled[self.session_key] = self._search_enabled
        await interaction.response.defer()
        question = self.children[0].value
        print(f"[Lantern AI] {interaction.user} (ID: {self.user_id}): follow-up \"{question[:200]}\"")
        self.cog.sessions.add_message(self.session_key, {"role": "user", "content": question})
        channel = self.cog.bot.get_channel(self.channel_id)
        async with self.cog._lock:
            response = await self.cog._get_ai_response(self.session_key, destination=channel, author_id=self.user_id)
        formatted = f"<@{self.user_id}> asked:\n> {question[:500]}\n\n{response}"
        view = FollowUpView(self.cog, self.user_id, self.session_key, self.channel_id)
        await self.cog._send_long_message_follow(interaction, formatted, view=view)


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
        self.cog.sessions.forget(self.session_key)


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
        self._lock = asyncio.Lock()
        bot.loop.create_task(self._init_mcp())

    async def _init_mcp(self):
        await self.bot.wait_until_ready()
        await self.mcp.connect_all()

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

    async def _send_long_message_follow(self, interaction, content: str, view=None):
        if not content:
            return
        chunks = [content[i:i + MAX_FOLLOWUP_LENGTH] for i in range(0, len(content), MAX_FOLLOWUP_LENGTH)]
        for i, chunk in enumerate(chunks):
            kwargs = {}
            if i == len(chunks) - 1 and view:
                kwargs["view"] = view
            await interaction.followup.send(chunk, **kwargs)
            if i < len(chunks) - 1:
                await asyncio.sleep(MESSAGE_DELAY)

    ai = discord.SlashCommandGroup("ai", "Lantern AI commands", guild_ids=GUILD_IDS)

    @ai.command(name="answer", description="Get a quick answer from Lantern AI")
    async def answer(
        self,
        ctx: discord.ApplicationContext,
        message: str = discord.Option(str, description="Your question for Lantern AI"),
        search: bool = discord.Option(bool, description="Allow web search tools", default=False),
    ):
        await ctx.defer()

        print(f"[Lantern AI] {ctx.author} (ID: {ctx.author.id}): /ai answer \"{message[:200]}\" search={search}")

        session_key = self._answer_session_key(ctx.author.id, ctx.channel_id)
        self.sessions.create(session_key, ctx.author.id, user_context=self._build_user_context(ctx.guild, ctx.author.id, ctx.author))
        self.sessions.search_enabled[session_key] = search
        self.sessions.add_message(session_key, {"role": "user", "content": message})

        async with self._lock:
            response = await self._get_ai_response(session_key, destination=ctx.channel, author_id=ctx.author.id)

        view = FollowUpView(self, ctx.author.id, session_key, ctx.channel_id)
        await self._send_long_message_follow(ctx, response, view=view)

    async def _ai_complete(self, messages: list, tools) -> openai.types.chat.ChatCompletion:
        return await asyncio.to_thread(
            self.ai.chat.completions.create,
            model=self.model,
            messages=messages,
            tools=tools,
            tool_choice="auto" if tools else None,
        )

    async def _get_ai_response(self, session_id: int, destination=None, author_id: int | None = None) -> str:
        if not self.sessions.get(session_id):
            return "Session not found. Use `/ai chat ask` to start a new conversation."

        tools = self._get_all_tools()
        if not self.sessions.search_enabled.get(session_id):
            tools = [t for t in (tools or []) if t["function"]["name"] in self._BUILTIN_NAMES] or None
        seen_calls = set()
        tool_counts = {}

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
                    for msg in messages:
                        if msg.get("role") == "user":
                            for uid in re.findall(r"<@!?(\d+)>", msg.get("content", "")):
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
                            ctx_parts.append({
                                "role": "system",
                                "content": " | ".join(info),
                            })
            ai_messages = messages + ctx_parts

            try:
                response = await asyncio.wait_for(
                    self._ai_complete(ai_messages, tools), timeout=120
                )
            except asyncio.TimeoutError:
                return "AI service timed out. Please try again."
            except Exception as e:
                msg = str(e)
                if "does not support image" in msg.lower():
                    return "I can only process text messages. I cannot read images or files."
                return f"AI service error: {msg[:200]}"

            choice = response.choices[0]

            if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
                msg = choice.message
                assistant_msg = {"role": "assistant", "content": msg.content or ""}
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    assistant_msg["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in msg.tool_calls
                    ]
                self.sessions.add_message(session_id, assistant_msg)

                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        args = {}

                    sig = (tc.function.name, json.dumps(args, sort_keys=True))
                    if sig in seen_calls:
                        result = "This tool was already called with the same arguments. Proceed with the information you already have."
                    else:
                        seen_calls.add(sig)
                        bare_name = tc.function.name.split(".")[-1]
                        tool_counts[bare_name] = tool_counts.get(bare_name, 0) + 1

                        args_str_display = json.dumps(args)[:500]
                        print(f"[Lantern AI] Tool call: {tc.function.name}({args_str_display})")

                        if tc.function.name in self._BUILTIN_NAMES:
                            result = await self._call_builtin_tool(tc.function.name, args, destination, requester_id=author_id)
                        else:
                            result = await self.mcp.call_tool(tc.function.name, args)

                    self.sessions.add_message(
                        session_id,
                        {"role": "tool", "tool_call_id": tc.id, "content": result},
                    )
                continue

            content = choice.message.content or ""
            content = "\n".join(l for l in content.split("\n") if "Used tools:" not in l).rstrip()
            if tool_counts:
                summary = ", ".join(f"{n} {c}x" for n, c in sorted(tool_counts.items()))
                content += f"\n-# Used tools: {summary}"
            self.sessions.add_message(session_id, {"role": "assistant", "content": content})
            return content

        return "I had trouble processing your request. Please try again."


def setup(bot):
    bot.add_cog(Chat(bot))
