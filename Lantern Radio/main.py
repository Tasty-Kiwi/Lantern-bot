import tomllib

import mafic
import nextcord
from nextcord.ext import commands

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


# track_queue: dict[list[mafic.Track]] = {}
temp_track_list = {}


class Dropdown(nextcord.ui.Select):
    def __init__(self, tracks: list[mafic.Track]):
        options = []
        for track in tracks:
            options.append(
                nextcord.SelectOption(
                    label=track.title, description=f"by {track.author}", value=track.uri
                )
            )
        super().__init__(
            placeholder="Pick a track to play",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: nextcord.Interaction):
        if not interaction.guild.voice_client:
            player = await interaction.user.voice.channel.connect(cls=mafic.Player)
        else:
            player = interaction.guild.voice_client

        track: mafic.Track = temp_track_list[interaction.channel_id][self.values[0]]
        temp_track_list[interaction.channel_id] = None
        # if player.current:
        #     if track_queue.get(interaction.channel_id) == None:
        #         track_queue[interaction.channel_id] = []

        #     track_queue[interaction.channel_id].append(track)
        #     embed = nextcord.Embed(
        #         title=track.title, url=track.uri, color=nextcord.Color.orange()
        #     )
        #     embed.set_author(name="Queued:")
        #     embed.add_field(name="Track author:", value=track.author, inline=False)
        #     embed.add_field(
        #         name="Queued by:", value=f"<@{interaction.user.id}>", inline=False
        #     )
        #     embed.set_footer("Currently doesn't work")
        #     return await interaction.edit(embed=embed, view=nextcord.ui.View())

        await player.play(track)
        embed = nextcord.Embed(
            title=track.title, url=track.uri, color=nextcord.Color.orange()
        )
        embed.set_author(name="Now playing:")
        embed.add_field(name="Track author:", value=track.author, inline=False)
        embed.add_field(
            name="Played by:", value=f"<@{interaction.user.id}>", inline=False
        )
        embed.set_image(track.artwork_url)
        await interaction.edit(embed=embed, view=nextcord.ui.View())


bot = MyBot(intents=nextcord.Intents(guilds=True, voice_states=True))


@bot.slash_command(dm_permission=False, guild_ids=config["testing_guild_ids"])
async def play(interaction: nextcord.Interaction, query: str):
    if not interaction.guild.voice_client:
        player = await interaction.user.voice.channel.connect(cls=mafic.Player)
    else:
        player = interaction.guild.voice_client

    tracks = await player.fetch_tracks(query)

    if not tracks:
        return await interaction.send("No tracks found.")

    tracks = tracks[:5]
    temp_track_list[interaction.channel_id] = {}
    for track in tracks:
        temp_track_list[interaction.channel_id][track.uri] = track

    view = nextcord.ui.View()
    view.add_item(Dropdown(tracks))

    await interaction.send(view=view)


@bot.slash_command(
    dm_permission=False,
    description="Set the volume for the bot.",
    guild_ids=config["testing_guild_ids"],
)
async def volume(interaction: nextcord.Interaction, query: int):
    if not interaction.guild.voice_client:
        player = await interaction.user.voice.channel.connect(cls=mafic.Player)
    else:
        player = interaction.guild.voice_client

    await player.set_volume(query)
    await interaction.send(f"Volume set to {query}%!")


@bot.slash_command(
    dm_permission=False,
    description="Annoy your friends by using TTS!",
    guild_ids=config["testing_guild_ids"],
)
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


@bot.slash_command(
    dm_permission=False,
    description="Disconect the bot from the voice channel.",
    guild_ids=config["testing_guild_ids"],
)
async def disconnect(interaction: nextcord.Interaction):
    if interaction.guild.voice_client:
        player = interaction.guild.voice_client
        await player.disconnect()
        await interaction.send("Disconnected from voice channel.")
    else:
        await interaction.send("No player detected")


@bot.slash_command(
    description="Returns information about the bot",
    guild_ids=config["testing_guild_ids"],
)
async def about(interaction: nextcord.Interaction):
    embed = nextcord.Embed(
        title="Lantern Radio",
        color=0xDC141A,
        description="Copyright © tasty kiwi 2024. Powered by Nextcord and Mafic.",
    )
    await interaction.send(embed=embed)


@bot.event
async def on_ready():
    print("Ready!")


bot.run(config["token"])
