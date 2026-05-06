# Project Overview: Lantern Bot

Lantern is a utility Discord bot designed for private servers. It is built using the **Pycord** library (a fork of discord.py) and follows a modular "cog" architecture.

### Main Technologies
- **Python 3.x**
- **Pycord (py-cord)**: For Discord API interaction.
- **python-dotenv**: For managing environment variables.
- **webcolors**: For handling color name to hex conversions in the color module.
- **FFmpeg**: Required for the SFX module to play audio.

## Project Structure
- `src/main.py`: The entry point of the bot. It initializes the bot, loads extensions (cogs), and defines global events and commands.
- `src/cogs/`: Contains modular command groups (extensions).
    - `color.py`: Handles custom role creation and color assignment for users.
    - `sfx.py`: Manages sound effect playback in voice channels.
- `src/requirements.txt`: Lists Python dependencies.
- `src/sfx/`: (Implicit) Directory where sound effect files (.wav, .mp3, .ogg) should be stored for the SFX module.

## Building and Running

### Prerequisites
- Python 3.8 or higher.
- FFmpeg installed and in the system path (for audio playback).

### Installation
1. Install dependencies:
   ```bash
   pip install -r src/requirements.txt
   ```
2. Create a `.env` file in the root directory (or `src/`) with the following variables:
   ```env
   DISCORD_TOKEN=your_bot_token_here
   GUILD_ID=your_target_guild_id_here
   ```

### Running the Bot
```bash
python src/main.py
```

## Development Conventions
- **Modular Cogs**: New functionality should be implemented as a new Cog in the `src/cogs/` directory.
- **Slash Commands**: The bot primarily uses slash commands (`discord.SlashCommandGroup` and `@bot.slash_command`).
- **Environment Variables**: Use `.env` for sensitive configurations like tokens and guild IDs.
- **Error Handling**: Follow the pattern of deferring responses for operations that might take time (e.g., role creation or connecting to voice).
