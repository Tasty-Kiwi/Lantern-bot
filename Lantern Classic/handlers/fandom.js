const { EmbedBuilder } = require("discord.js")
const { legacy } = require("./common_wiki")
const { fandomUris } = require("../langs.json")

module.exports = async (interaction) => {
  await interaction.deferReply()

  let fandomInfo = []
  //console.log(interaction.options.getInteger("fandom"))
  if (interaction.options.getInteger("fandom") === 0 /* esolang wiki */) {
    fandomInfo = await legacy.scrapeWiki(
      fandomUris[0],
      "https://esolangs.org/w/images/c/c9/Logo.png"
    )
  } else if (interaction.options.getInteger("fandom") === 1 /* LGBTA+ wiki */) {
    fandomInfo = await legacy.scrapeWiki(
      fandomUris[1],
      "https://static.miraheze.org/lgbtawiki/c/c9/Logo.png"
    )
  } else if (interaction.options.getInteger("fandom") === 2 /* GTA wiki */) {
    fandomInfo = await legacy.scrapeWiki(
      fandomUris[2],
      "https://gtwfilesie-thumb.grandtheftwiki.com/GTW-logo.png/135px-GTW-logo.png"
    )
  } else {
    fandomInfo = await legacy.scrapeWiki(
      fandomUris[interaction.options.getInteger("fandom")],
      "https://www.wikipedia.org/portal/wikipedia.org/assets/img/Wikipedia-logo-v2.png"
    )
  }
  //console.dir(fandomInfo)
  const embed_fandom = new EmbedBuilder()
    .setColor(0xcc9e00)
    .setTitle(fandomInfo[1])
    .setURL(fandomInfo[0])
    .setDescription(
      fandomInfo[3].length > 2000 ? fandomInfo[3].slice(0, 2000) : fandomInfo[3]
    )
    .setThumbnail(fandomInfo[2])
    .setFooter({ text: "⚠ Legacy command" })
  await interaction.editReply({ embeds: [embed_fandom] })
}
