const {
  Client,
  Events,
  GatewayIntentBits,
  EmbedBuilder,
} = require("discord.js")
const client = new Client({ intents: [GatewayIntentBits.Guilds] })

const config = require("./config.json")

// Handlers
const randomWikiHandler = require("./handlers/wiki_random")
const embedWikiHandler = require("./handlers/wiki_embed")
const fandomHandler = require("./handlers/fandom")
const colorSetHandler = require("./handlers/color_set")
const colorClearHandler = require("./handlers/color_clear")

// For the crappy queue
function sleep(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms)
  })
}

let memberQueue = []

async function instantiateQueue(id, len) {
  if (!memberQueue.includes(id)) {
    memberQueue.push(id)
    //console.log(memberQueue)
    memberQueue.forEach(async (el, i, arr) => {
      await sleep(len)
      arr.pop()
      //console.log(memberQueue)
    })
  }
}

client.once(Events.ClientReady, (c) => {
  console.log(`Online as: ${c.user.tag}`)
})

client.on(Events.InteractionCreate, async (interaction) => {
  if (!interaction.isCommand()) return

  try {
    switch (interaction.commandName) {
      case "about":
        const embed_about = new EmbedBuilder()
          .setColor(0x0c131f)
          .setTitle("Lantern")
          .setDescription(
            "Copyright © tasty kiwi 2021 - 2024. Powered by Discord.js and cheerio."
          )
          .setFooter({ text: "Version 4.1.1" })
        await interaction.reply({ embeds: [embed_about] })
        break
      case "send":
        await interaction.reply({
          content: "Sent!",
          ephemeral: true,
        })
        await interaction.channel.send({
          content: interaction.options.getString("message"),
        })
        break
      case "fandom":
        if (!memberQueue.includes(interaction.user.id)) {
          instantiateQueue(interaction.user.id, 10_000)
          fandomHandler(interaction)
        } else {
          const embed_fandomLimit = new EmbedBuilder()
            .setColor(0xff0000)
            .setTitle("🏇 Hold your horses!")
            //.setTitle("🛑 Temporarily disabled")
            .setDescription("You may only send this command every 10 seconds.")
            //.setDescription("This command is temporarily disabled.")
          await interaction.reply({ embeds: [embed_fandomLimit] })
        }
        break
      case "wiki":
        if (!memberQueue.includes(interaction.user.id)) {
          instantiateQueue(interaction.user.id, 3_000)
          switch (interaction.options.getSubcommand()) {
            case "random":
              randomWikiHandler(interaction)
              break
            case "embed":
              embedWikiHandler(interaction) 
              break
          }
        } else {
          const embed_wikiLimit = new EmbedBuilder()
            .setColor(0xff0000)
            .setTitle("🏇 Hold your horses!")
            //.setTitle("🛑 Temporarily disabled")
            .setDescription("You may only send this command every 3 seconds.")
            //.setDescription("This command is temporarily disabled.")
          await interaction.reply({ embeds: [embed_wikiLimit] })
        }
        break
      case "color":
        if (interaction.inGuild()) {
          switch (interaction.options.getSubcommand()) {
            case "clear":
              colorClearHandler(interaction)
              break
            case "set":
              colorSetHandler(interaction)
              break
          }
        } else {
          await interaction.reply({
            content: "This command may only be ran in a server.",
            ephemeral: true,
          })
        }
        break
      default:
        await interaction.reply({
          content: "Unknown interaction.",
          ephemeral: true,
        })
        console.warn(`Unknown interaction: ${interaction.commandName}`)
        break
    }
  } catch (err) {
    console.error(err)
    await interaction.reply(
      "Uh oh. An error occured. tasty kiwi has been notified."
    )
    await fetch(config.webhookUri, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        content: `**Error:**\`\`\`fix\n${err}\n\`\`\``,
      }),
    })
  }
})

client.login(config.token)
