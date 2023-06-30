const { EmbedBuilder } = require("discord.js")

// Regex pattern for the color changer
// https://regexr.com/3ag5b; modified the first `#?` to remove # eval
const colorRegex = /([\da-fA-F]{2})([\da-fA-F]{2})([\da-fA-F]{2})/g

module.exports = async (interaction) => {
  const color = interaction.options.getString("color").toLowerCase()

  let parsedColor = color.match(colorRegex) ? color.match(colorRegex)[0] : null

  if (colorRegex.test(color) === false) {
    await interaction.reply({ content: "Received an invalid hex code. It should look like this: `ff0011`", ephemeral: true })
    return
  }
  if (parsedColor === "000000") parsedColor = "010101"

  const colorRoles = Array.from(interaction.member.roles.cache.values()).filter(role => role.name.match(colorRegex))
  
  if (colorRoles.length > 0) {
    if (colorRoles[0].members.size - 1 === 0) {
      await interaction.member.roles.remove(colorRoles[0])
      await colorRoles[0].delete(`No one has the specified color role. Command requested by ${interaction.user.tag}.`)
    } else {
      await interaction.member.roles.remove(colorRoles[0])
    }
  }
  
  const existingRole = interaction.guild.roles.cache.find((role) => role.name === parsedColor)

  if (existingRole !== undefined) {
    interaction.member.roles.add(existingRole)
  } else {
    const role = await interaction.guild.roles.create({
      name: parsedColor,
      color: parsedColor,
      reason: `Automatically created role for ${parsedColor} color. Requested by ${interaction.user.tag}.`,
      permissions: [],
    })
    interaction.member.roles.add(role)
  }
  
  const embed_colorSet = new EmbedBuilder()
    .setColor(parsedColor)
    .setTitle("Color changer")
    .setDescription(
      `Color has been changed successfully to \`${parsedColor}\`\n:arrow_left: Role color preview`
    )
  await interaction.reply({ embeds: [embed_colorSet] })
}