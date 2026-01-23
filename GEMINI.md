# Lantern-bot

A monorepo containing the Lantern family of Discord bots.

## Project Structure

*   **Lantern Classic:** A Node.js Discord bot (v14) focusing on utility features like Wiki searches, Fandom integration, and role color management.
*   **Lantern Radio:** A Python Discord bot focusing on music/radio playback using Lavalink.
*   **Lantern Neo:** A new generation Python Discord bot using `py-cord` and `.env` for configuration.

## Lantern Classic (Node.js)

### Prerequisites
*   Node.js (v16.9.0 or higher required by discord.js v14)
*   NPM

### Setup
1.  Navigate to the directory:
    ```bash
    cd "Lantern Classic"
    ```
2.  Install dependencies:
    ```bash
    npm install
    ```
3.  Create a `config.json` file in `Lantern Classic/` with the following structure:
    ```json
    {
      "token": "YOUR_DISCORD_BOT_TOKEN",
      "applicationId": "YOUR_APPLICATION_ID",
      "guildIds": ["GUILD_ID_1", "GUILD_ID_2"],
      "isGlobal": false
    }
    ```
    *   Set `isGlobal` to `true` to register commands globally (can take up to an hour to propagate).
    *   `guildIds` is only used if `isGlobal` is `false`.

### Running
*   **Register Slash Commands:** (Run this once or whenever commands change)
    ```bash
    npm run deploy
    # or
    node deploy_slash_commands.js
    ```
*   **Start the Bot:**
    ```bash
    npm start
    # or
    node index.js
    ```

## Lantern Radio (Python)

### Prerequisites
*   Python 3.11+ (Uses `tomllib`)
*   Java (for Lavalink)

### Setup
1.  Navigate to the directory:
    ```bash
    cd "Lantern Radio"
    ```
2.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```
3.  Create a `config.toml` file in `Lantern Radio/` with the following structure:
    ```toml
    token = "YOUR_DISCORD_BOT_TOKEN"
    testing_guild_ids = []

    [lavalink]
    host = "127.0.0.1"
    port = 2333
    password = "youshallnotpass"
    ```

### Lavalink Setup
The `lavalink/` directory contains the Lavalink server JAR and plugins.
1.  Ensure you have a valid `application.yml` in `Lantern Radio/lavalink/` (usually provided or configured manually).
2.  Start Lavalink before starting the bot:
    ```bash
    cd lavalink
    java -jar Lavalink.jar
    ```

### Running the Bot
1.  Start the bot:
    ```bash
    python main.py
    ```

## Lantern Neo (Python)

### Prerequisites
*   Python 3.11+
*   `py-cord`, `python-dotenv`, `webcolors`

### Setup
1.  Navigate to the directory:
    ```bash
    cd lantern-neo
    ```
2.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```
3.  Create a `.env` file in `lantern-neo/` with the following structure:
    ```env
    DISCORD_TOKEN=YOUR_DISCORD_BOT_TOKEN
    GUILD_ID=YOUR_TESTING_GUILD_ID
    ```

### Features
*   **Color Role Management:**
    *   `/color set [code/name]`: Creates and assigns a custom color role to the user. Supports Hex codes (e.g., `ff0000`) and CSS3 color names (e.g., `hotpink`). automatically handles cleanup of unused roles.
    *   `/color clear`: Removes the custom color role from the user.
*   **Utility:**
    *   `/about`: Displays information about the bot.

### Structure
*   **`main.py`**: Entry point. Initializes the bot, loads environment variables, and loads cogs from the `cogs/` directory.
*   **`cogs/`**: Contains bot extensions (plugins).
    *   `color.py`: Handles color role logic, including validation and role management.
    *   `sfx.py`: Handles sound effect (sub)commands and audio playback.

### Running the Bot
1.  Start the bot:
    ```bash
    python main.py
    ```

## Development Conventions
*   **Lantern Classic:**
    *   Uses `discord.js` v14.
    *   Command handlers are located in `handlers/`.
    *   `deploy_slash_commands.js` handles command registration.
*   **Lantern Radio:**
    *   Uses `nextcord` and `mafic`.
    *   Uses `pyradios` for radio station lookup.
*   **Lantern Neo:**
    *   Uses `py-cord` and modular `cogs/` system for extensions.
    *   Configuration managed via `.env`.
    *   Uses `webcolors` for robust color parsing.
