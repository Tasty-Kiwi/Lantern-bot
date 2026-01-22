import nextcord
from nextcord.ext import commands
import re
import os
import webcolors

# Regex to match exactly 6 hex characters
COLOR_REGEX = re.compile(r"^[0-9a-fA-F]{6}$")

# Get guild_ids at module level for the decorator
GUILD_ID = os.getenv("GUILD_ID")
GUILD_IDS = [int(GUILD_ID)] if GUILD_ID else None


class Color(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _remove_color_roles(
        self, interaction: nextcord.Interaction, member: nextcord.Member
    ):
        """
        Removes existing color roles from the user.
        If the role has no other members, it deletes the role.
        Returns True if a role was found/removed, False otherwise.
        """
        found_any = False
        # Identify color roles the member has
        # We look for roles where the name matches the hex pattern
        roles_to_check = [role for role in member.roles if COLOR_REGEX.match(role.name)]

        if roles_to_check:
            found_any = True
            # In the original JS, it only handled the first one (colorRoles[0]).
            # We will handle the first one to match behavior, but ideally a user should only have one.
            role = roles_to_check[0]

            # If the member is the only one with this role (or somehow 0), delete the role.
            # len(role.members) includes the current user.
            if len(role.members) <= 1:
                try:
                    await role.delete(
                        reason=f"No one has the specified color role. Command requested by {interaction.user}."
                    )
                except nextcord.HTTPException:
                    # If deletion fails (e.g. perms), at least remove the role from the member
                    await member.remove_roles(role, reason="Cleaning color role")
            else:
                # Others share the role, just remove it from this member
                await member.remove_roles(role, reason="Cleaning color role")

        return found_any

    @nextcord.slash_command(
        description="Color replacer module of Lantern", guild_ids=GUILD_IDS
    )
    async def color(self, interaction: nextcord.Interaction):
        """
        Base command for color operations.
        """
        pass

    @color.subcommand(description="Creates a custom role with a color code.")
    async def set(
        self,
        interaction: nextcord.Interaction,
        color: str = nextcord.SlashOption(
            description="Color hex code (e.g. ff0000) or name (e.g. hotpink)"
        ),
    ):
        """
        Sets your color role.
        """
        original_input = color
        color = color.strip().lower()

        # Remove '#' if present (for manual hex entry)
        if color.startswith("#"):
            color = color[1:]

        # Validate: Check if it's a valid hex code
        if not COLOR_REGEX.match(color):
            # If not hex, try to parse as a color name
            try:
                # name_to_hex returns e.g. "#ff69b4"
                hex_value = webcolors.name_to_hex(original_input)
                color = hex_value[1:]  # strip the #
            except ValueError:
                await interaction.response.send_message(
                    f"Received an invalid color: `{original_input}`. Please use a hex code (e.g. `ff0011`) or a valid [CSS3 color name](<https://www.w3schools.com/cssref/css_colors.php>) (e.g. `hotpink`).",
                    ephemeral=True,
                )
                return

        # Normalize 000000 to 010101 (Discord transparency workaround)
        if color == "000000":
            color = "010101"

        # Defer reply since role operations can take a moment
        await interaction.response.defer()

        # 3. Clean up old colors
        await self._remove_color_roles(interaction, interaction.user)

        # 4. Find or Create Role
        existing_role = nextcord.utils.get(interaction.guild.roles, name=color)

        target_role = existing_role
        if not target_role:
            try:
                # Parse hex string to integer for color
                color_int = int(color, 16)
                target_role = await interaction.guild.create_role(
                    name=color,
                    color=nextcord.Color(color_int),
                    permissions=nextcord.Permissions.none(),
                    reason=f"Automatically created role for {color} color. Requested by {interaction.user}.",
                )
            except nextcord.HTTPException as e:
                await interaction.followup.send(f"Failed to create role: {e}")
                return

        # 5. Assign Role
        try:
            await interaction.user.add_roles(target_role)
        except nextcord.HTTPException as e:
            await interaction.followup.send(f"Failed to assign role: {e}")
            return

        # 6. Response
        embed = nextcord.Embed(
            title="Color changer",
            description=f"Color has been changed successfully to `{color}`\n:arrow_left: Role color preview",
            color=nextcord.Color(int(color, 16)),
        )
        await interaction.followup.send(embed=embed)

    @color.subcommand(description="Clears a color from your profile.")
    async def clear(self, interaction: nextcord.Interaction):
        """
        Removes your color role.
        """
        await interaction.response.defer()

        removed = await self._remove_color_roles(interaction, interaction.user)

        embed = nextcord.Embed(title="Color changer", color=0x6B003B)

        if removed:
            embed.description = "Color was removed successfully."
        else:
            embed.description = "You do not have any colors selected!"

        await interaction.followup.send(embed=embed)


def setup(bot):
    bot.add_cog(Color(bot))
