const { EmbedBuilder } = require("discord.js")
const { fetchWikipedia } = require("./common_wiki")

module.exports = async (interaction) => {
  const url = interaction.options.getString("url").trim()
  try {
    const embedWikiInfo = await fetchWikipedia(
      url,
      "https://www.wikipedia.org/portal/wikipedia.org/assets/img/Wikipedia-logo-v2.png"
    )

    if (embedWikiInfo !== null) {
      const embed_wikiEmbedder = new EmbedBuilder()
        .setColor(0xceced0)
        .setTitle(embedWikiInfo.title)
        .setURL(embedWikiInfo.url)
        .setDescription(
          embedWikiInfo.content.length > 2000
            ? embedWikiInfo.content.slice(0, 2000)
            : embedWikiInfo.content
        )
        .setThumbnail(embedWikiInfo.thumbnail)
        .setFooter({ text: embedWikiInfo.wikiType })
      await interaction.reply({ embeds: [embed_wikiEmbedder] })
    } else {
      await interaction.reply({ content: "Failed to parse the URL!", ephemeral: true })
    }
  } catch (err) {
    console.error(err)
    await interaction.reply({ content: "URL request failed (or the wiki in selected language is unavailable).", ephemeral: true })
  }
}