import tomllib

import mafic
import nextcord
from nextcord.ext import commands
from pyradios import RadioBrowser

with open("config.toml", "rb") as f:
    config = tomllib.load(f)

class MyBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.pool = mafic.NodePool(self)
        self.loop.create_task(self.add_nodes())

    async def add_nodes(self):
        await self.pool.create_node(
            host=config["lavalink"]["host"],
            port=config["lavalink"]["port"],
            label="MAIN",
            password=config["lavalink"]["password"],
        )

bot = MyBot(intents=nextcord.Intents(guilds=True, voice_states=True))
rb = RadioBrowser()

@bot.slash_command(dm_permission=False, guild_ids=config["testing_guild_ids"], description="main command")
async def radio(interaction: nextcord.Interaction):
    pass

@radio.subcommand(description="Play a radio station")
async def play(interaction: nextcord.Interaction, query: str):
    if not interaction.guild.voice_client:
        player = await interaction.user.voice.channel.connect(cls=mafic.Player)
    else:
        player = interaction.guild.voice_client

    # TODO: fix
    #if not query.startswith("http://") or not query.startswith("https://"):
    #    return await interaction.send("Please provide a valid URL.")
    
    tracks = await player.fetch_tracks(query)

    if not tracks:
        return await interaction.send("No tracks found.")

    track = tracks[0]

    await player.play(track)
    
    embed = nextcord.Embed(title=track.title, url=track.uri, color=nextcord.Color.orange())
    embed.set_author(name="Radio player")

    await interaction.send(embed=embed)

@radio.subcommand(description="Search for radio stations in Icecast radio directory")
async def search(interaction: nextcord.Interaction, query: str,
        limit: int = nextcord.SlashOption(
        name="limit",
        description="Limit of search queries [1, 10]",
        required=False
    ),
):
    if limit == None or limit < 1 or limit > 10:
        limit = 4
    
    results = rb.search(name=query, limit=limit)
    
    embeds = []
    for result in results:
        embeds.append(nextcord.Embed(title=result["name"], color=nextcord.Color.purple()).add_field(name="URL", value=result["url_resolved"], inline=False)
                      .add_field(name="Homepage", value=result["homepage"], inline=False)
                      .add_field(name="Tags", value=result["tags"], inline=False)
                      .add_field(name="Codec", value=result["codec"], inline=True)
                      .add_field(name="Bitrate", value=result["bitrate"], inline=True)
                      .set_thumbnail(result["favicon"]))
    
    await interaction.send("Search results from <https://www.radio-browser.info/>", embeds=embeds)

@bot.slash_command(dm_permission=False, guild_ids=[config["testing_guild_ids"]])
async def play(interaction: nextcord.Interaction, query: str):
    if not interaction.guild.voice_client:
        player = await interaction.user.voice.channel.connect(cls=mafic.Player)
    else:
        player = interaction.guild.voice_client

    tracks = await player.fetch_tracks(query)

    if not tracks:
        return await interaction.send("No tracks found.")

    track = tracks[0]

    await player.play(track)
    
    embed = nextcord.Embed(title=track.title, url=track.uri, color=nextcord.Color.orange())
    embed.set_author(name="Now playing")

    await interaction.send(embed=embed)

@bot.slash_command(dm_permission=False, description="Annoy your friends by using TTS!", guild_ids=[config["testing_guild_ids"]])
async def vc_tts(interaction: nextcord.Interaction, query: str):
    if not interaction.guild.voice_client:
        player = await interaction.user.voice.channel.connect(cls=mafic.Player)
    else:
        player = interaction.guild.voice_client

    tracks = await player.fetch_tracks(query, search_type=mafic.SearchType.TTS)

    if not tracks:
        return await interaction.send("Unable to say it!")

    track = tracks[0]

    await player.play(track)

    await interaction.send(f"Said `{query}`!")

@bot.slash_command(dm_permission=False, guild_ids=config["testing_guild_ids"])
async def stop(interaction: nextcord.Interaction):
    if interaction.guild.voice_client:
        player = interaction.guild.voice_client
        await player.stop()
        await interaction.send("Stopped playing.")
    else:
        await interaction.send("No player detected")

@bot.slash_command(description="Disconect the bot from the voice channel.", guild_ids=config["testing_guild_ids"])
async def disconnect(interaction: nextcord.Interaction):
    if interaction.guild.voice_client:
        player = interaction.guild.voice_client
        await player.disconnect()
        await interaction.send("Disconnected from voice channel.")
    else:
        await interaction.send("No player detected")

@bot.slash_command(description="Returns information about the bot", guild_ids=config["testing_guild_ids"])
async def about(interaction: nextcord.Interaction):
    embed = nextcord.Embed(title="Lantern Radio", color=0xdc141a, description="Copyright © tasty kiwi 2023. Powered by Nextcord and Mafic.")
    await interaction.send(embed=embed)

@bot.event
async def on_ready():
    print("Ready!")

bot.run(config["token"])