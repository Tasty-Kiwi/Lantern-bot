const { EmbedBuilder } = require("discord.js")

// Regex pattern for the color changer
// https://regexr.com/3ag5b; modified the first `#?` to remove # eval
const colorRegex = /([\da-fA-F]{2})([\da-fA-F]{2})([\da-fA-F]{2})/g

module.exports = async (interaction) => {
  const colorRoles = Array.from(interaction.member.roles.cache.values()).filter(role => role.name.match(colorRegex))
  
  if (colorRoles.length > 0) {
    if (colorRoles[0].members.size - 1 === 0) {
      await interaction.member.roles.remove(colorRoles[0])
      await colorRoles[0].delete(`No one has the specified color role. Command requested by ${interaction.user.tag}.`)
    } else {
      await interaction.member.roles.remove(colorRoles[0])
    }
    const embed_colorClear = new EmbedBuilder()
      .setColor(0x6b003b)
      .setTitle("Color changer")
      .setDescription("Color was removed successfully.")
    await interaction.reply({ embeds: [embed_colorClear] })
  } else {
    const embed_colorClear = new EmbedBuilder()
      .setColor(0x6b003b)
      .setTitle("Color changer")
      .setDescription("You do not have any colors selected!")
    await interaction.reply({ embeds: [embed_colorClear] })
  }
}