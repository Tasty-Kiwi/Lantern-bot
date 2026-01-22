import os
import nextcord
from nextcord.ext import commands
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Get environment variables
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")

if not TOKEN:
    print("Error: DISCORD_TOKEN not found in .env file.")
    exit(1)

# Convert GUILD_ID to a list of integers if it exists
guild_ids = [int(GUILD_ID)] if GUILD_ID else None

# Initialize the bot
bot = commands.Bot(intents=nextcord.Intents(guilds=True, voice_states=True, members=True)) # Added members=True for role management


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

# Load cogs
for filename in os.listdir("./cogs"):
    if filename.endswith(".py"):
        bot.load_extension(f"cogs.{filename[:-3]}")

@bot.slash_command(description="About Lantern Neo", guild_ids=guild_ids)
async def about(interaction: nextcord.Interaction):
    """
    Returns information about the bot.
    """
    embed = nextcord.Embed(
        title="About Lantern Neo",
        description="Hi! I am Lantern Neo, a utility bot for various private servers.",
        color=nextcord.Color.blue(),
    ).set_footer(text="v2.0.1, © 2026 tasty kiwi")
    await interaction.response.send_message(embed=embed)


if __name__ == "__main__":
    bot.run(TOKEN)
