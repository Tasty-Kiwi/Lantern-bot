import nextcord
from nextcord.ext import commands, tasks
import os
import datetime

# Get guild_ids at module level for the decorator
GUILD_ID = os.getenv("GUILD_ID")
GUILD_IDS = [int(GUILD_ID)] if GUILD_ID else None

SFX_DIR = "sfx"


class Sfx(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.last_activity = {}  # {guild_id: datetime}
        self.timeout_check.start()

    def cog_unload(self):
        self.timeout_check.cancel()

    def get_sound_files(self):
        """Returns a list of available sound files (without extension)"""
        if not os.path.exists(SFX_DIR):
            return []
        files = []
        for f in os.listdir(SFX_DIR):
            if f.endswith(".wav") or f.endswith(".mp3"):
                files.append(os.path.splitext(f)[0])
        return sorted(files)

    @nextcord.slash_command(
        description="Sound effects module", guild_ids=GUILD_IDS
    )
    async def sfx(self, interaction: nextcord.Interaction):
        """
        Base command for SFX operations.
        """
        pass

    @sfx.subcommand(description="Plays a sound effect.")
    async def play(
        self,
        interaction: nextcord.Interaction,
        sound: str = nextcord.SlashOption(
            description="The name of the sound to play",
            required=True
        ),
    ):
        """
        Plays the specified sound effect in your voice channel.
        """
        if not interaction.user.voice:
            await interaction.response.send_message(
                "You are not in a voice channel!", ephemeral=True
            )
            return

        if interaction.user.voice.deaf or interaction.user.voice.self_deaf:
            await interaction.response.send_message(
                "You cannot play sound effects while deafened!", ephemeral=True
            )
            return

        # Check for .wav then .mp3
        sound_path = os.path.join(SFX_DIR, f"{sound}.wav")
        if not os.path.exists(sound_path):
            sound_path = os.path.join(SFX_DIR, f"{sound}.mp3")
            if not os.path.exists(sound_path):
                await interaction.response.send_message(
                    f"Sound `{sound}` not found.", ephemeral=True
                )
                return

        await interaction.response.defer()

        voice_channel = interaction.user.voice.channel
        voice_client = interaction.guild.voice_client

        # Connect or move
        if voice_client:
            if voice_client.channel != voice_channel:
                await voice_client.move_to(voice_channel)
        else:
            try:
                voice_client = await voice_channel.connect()
            except Exception as e:
                await interaction.followup.send(f"Failed to connect to voice: {e}")
                return

        # Update activity timestamp
        self.last_activity[interaction.guild.id] = datetime.datetime.now()

        # Stop any currently playing audio
        if voice_client.is_playing():
            voice_client.stop()

        # Play
        try:
            source = nextcord.FFmpegPCMAudio(sound_path)
            voice_client.play(source)
            await interaction.followup.send(f"Playing `{sound}`")
        except Exception as e:
            await interaction.followup.send(f"Error playing sound: {e}")

    @play.on_autocomplete("sound")
    async def play_autocomplete(self, interaction: nextcord.Interaction, sound: str):
        all_sounds = self.get_sound_files()
        if not sound:
            await interaction.response.send_autocomplete(all_sounds[:25])
            return

        filtered = [s for s in all_sounds if sound.lower() in s.lower()]
        await interaction.response.send_autocomplete(filtered[:25])

    @sfx.subcommand(description="Disconnects the bot from voice.")
    async def leave(self, interaction: nextcord.Interaction):
        """
        Leaves the voice channel.
        """
        if interaction.guild.voice_client:
            await interaction.guild.voice_client.disconnect()
            self.last_activity.pop(interaction.guild.id, None)
            await interaction.response.send_message("Disconnected.")
        else:
            await interaction.response.send_message(
                "I am not in a voice channel.", ephemeral=True
            )

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """
        Disconnects if the bot is left alone in a voice channel.
        """
        if member.id == self.bot.user.id:
            return

        voice_client = member.guild.voice_client
        
        # Check if the user left the channel the bot is in
        if voice_client and before.channel and before.channel == voice_client.channel:
            # If only 1 member is left, and it's the bot (handled by len check)
            if len(voice_client.channel.members) == 1:
                await voice_client.disconnect()
                self.last_activity.pop(member.guild.id, None)

    @tasks.loop(minutes=2)
    async def timeout_check(self):
        """
        Disconnects if no audio has played for 10 minutes.
        """
        for vc in self.bot.voice_clients:
            guild_id = vc.guild.id
            
            # If currently playing, update the activity timestamp
            if vc.is_playing():
                self.last_activity[guild_id] = datetime.datetime.now()
            else:
                last_active = self.last_activity.get(guild_id)
                
                # If we have no record, assume inactivity starts now
                if last_active is None:
                    self.last_activity[guild_id] = datetime.datetime.now()
                    continue

                # Check if 10 minutes (600 seconds) have passed
                if (datetime.datetime.now() - last_active).total_seconds() > 600:
                    await vc.disconnect()
                    self.last_activity.pop(guild_id, None)

    @timeout_check.before_loop
    async def before_timeout_check(self):
        await self.bot.wait_until_ready()


def setup(bot):
    bot.add_cog(Sfx(bot))