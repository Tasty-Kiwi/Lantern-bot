const { EmbedBuilder } = require("discord.js")
const { fetchRandomWikipedia } = require("./common_wiki")

module.exports = async (interaction) => {
  try {
    const wikiInfo = await fetchRandomWikipedia(
      interaction.options.getString("language"),
      interaction.options.getString("wiki")
    )
    const embed_randomWiki = new EmbedBuilder()
      .setColor(0xceced0)
      .setTitle(wikiInfo.title)
      .setURL(wikiInfo.url)
      .setDescription(
        wikiInfo.content.length > 2000
          ? wikiInfo.content.slice(0, 2000)
          : wikiInfo.content
      )
      .setThumbnail(wikiInfo.thumbnail)
      .setFooter({ text: wikiInfo.wikiType })
    await interaction.reply({ embeds: [embed_randomWiki] })
  } catch (err) {
    console.error(err)
    await interaction.reply({ content: "URL request failed (or the wiki in selected language is unavailable).", ephemeral: true })
  }
}