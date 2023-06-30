const { SlashCommandBuilder } = require("@discordjs/builders")
const { REST } = require("@discordjs/rest")
const { Routes } = require("discord-api-types/v9")
const { applicationId, guildIds, token, isGlobal } = require("./config.json")
const { wikipediaSelections, fandomSelections, wikiSelections } = require("./langs.json")

const commands = [
  new SlashCommandBuilder()
    .setName("wiki")
    .setDescription("Wiki module of Lantern")
    .addSubcommand((subcommand) =>
      subcommand
        .setName("random")
        .setDescription("Returns a random Wikipedia article.")
        .addStringOption((option) =>
          option
            .setName("language")
            .setDescription(
              "Language to search in (default is English)"
            )
            .addChoices(...wikipediaSelections)
        )
        .addStringOption((option) =>
        option
          .setName("wiki")
          .setDescription(
            "Wiki to show a random article from"
          )
          .addChoices(...wikiSelections)
      )
    )
    .addSubcommand((subcommand) =>
      subcommand
        .setName("embed")
        .setDescription(
          "Creates a nice wiki embed. Only Wikipedia, Wikivoyage, Wikiquote and Wikiversity are supported."
        )
        .addStringOption((option) =>
          option
            .setName("url")
            .setDescription("Supported URL")
            .setRequired(true)
        )
    ),
  new SlashCommandBuilder()
    .setName("fandom")
    .setDescription("(Legacy) Returns a random article from non-Wikimedia wikis.")
    .addIntegerOption((option) =>
      option
        .setName("fandom")
        .setDescription("Fandom to search in!")
        .setRequired(true)
        .addChoices(...fandomSelections)
    ),
  new SlashCommandBuilder()
    .setName("about")
    .setDescription("Information about the bot."),
  //new SlashCommandBuilder().setName("devicehealth").setDescription("Reports information about device the bot is running on."),
  new SlashCommandBuilder()
    .setName("color")
    .setDescription("Color replacer module of Lantern")
    .addSubcommand((subcommand) =>
      subcommand
        .setName("set")
        .setDescription("Creates a custom role with a color code.")
        .addStringOption((option) =>
          option
            .setName("color")
            .setDescription("color hex code.")
            .setRequired(true)
        )
    )
    .addSubcommand((subcommand) =>
      subcommand
        .setName("clear")
        .setDescription("Clears a color from your profile.")
    ),
  new SlashCommandBuilder()
    .setName("send")
    .setDescription("Send a message as if you were a bot!")
    .addStringOption((option) =>
      option
        .setName("message")
        .setDescription("Message content")
        .setRequired(true)
    ),
].map((command) => command.toJSON())

const rest = new REST({ version: "9" }).setToken(token)

;(async () => {
  try {
    if (!isGlobal) {
      guildIds.forEach(async (guildId) => {
        await rest.put(
          Routes.applicationGuildCommands(applicationId, guildId),
          {
            body: commands,
          }
        )
      })
    } else {
      await rest.put(Routes.applicationCommands(applicationId), {
        body: commands,
      })
    }
    console.log("Successfully registered application commands.")
  } catch (error) {
    console.error(error)
  }
})()
