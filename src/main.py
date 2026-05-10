import os
import sys
import discord
from discord.ext import commands
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

# Get environment variables
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")

if not TOKEN:
    print("Error: DISCORD_TOKEN not found in .env file.")
    exit(1)

# Convert GUILD_ID to a list of integers if it exists
guild_ids = [int(GUILD_ID)] if GUILD_ID else None

# Disable session persistence if --no-db flag is passed
if "--no-db" in sys.argv:
    os.environ["LANTERN_NO_DB"] = "1"
    print("[Lantern AI] Running without session persistence (--no-db)")


# Initialize the bot
class LanternBot(commands.Bot):
    async def close(self):
        print("Cleaning up voice connections...")
        for vc in self.voice_clients:
            try:
                await vc.disconnect(force=True)
            except Exception as e:
                print(f"Error disconnecting from voice: {e}")
        await super().close()
        print("Bot closed.")


bot = LanternBot(
    intents=discord.Intents(
        guilds=True, voice_states=True, members=True, messages=True, message_content=True
    )
)  # messages + message_content for chat cog


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")


# Load cogs
for filename in os.listdir("./cogs"):
    if filename.endswith(".py"):
        bot.load_extension(f"cogs.{filename[:-3]}")


@bot.slash_command(description="About Lantern", guild_ids=guild_ids)
async def about(ctx: discord.ApplicationContext):
    """
    Returns information about the bot.
    """
    embed = discord.Embed(
        title="About Lantern",
        description="Hi! I am Lantern, a utility bot for various private servers.",
        color=discord.Color.blue(),
    ).set_footer(text="v5.1.0, © 2026 tasty kiwi")
    await ctx.send_response(embed=embed)


if __name__ == "__main__":
    bot.run(TOKEN)
