# DiscordMusicBOT

A lightweight Discord music bot built with `discord.py` and `yt-dlp`, featuring a Windows system tray utility (`pystray`) and live local browser playback synchronization (`!followme`).

---

## Features

- **YouTube & Playlist Streaming**: Play any song or playlist via `!play <query or URL>` using `FFmpegOpusAudio`.
- **`!followme` (`!fm`) Browser Sync (Windows)**:
  - Automatically syncs the Discord bot with whatever song you are playing locally in **Brave / Chrome / Edge** (e.g., YouTube Music or YouTube) via Windows Global System Media Transport Controls (GSMTC).
  - Follows **track changes**, **seeks/scrubbing** on the timeline, and **pause/resume** automatically in real time without spamming Discord chat.
- **Silent Windows System Tray Mode**:
  - Runs silently in the background with a system tray icon (`tray_main.py`).
  - Right-click menu to **Open Live Console** (tails `bot.log`), **Check for Updates**, or **Exit**.
- **Automatic Dependency Updater**: Checks PyPI on startup to keep `yt-dlp` and other packages up to date.

---

## Prerequisites

1. **Python 3.10+**
2. **FFmpeg**: Must be installed and available in your system `PATH` (on Windows, `winget install Gyan.FFmpeg`).
3. **Discord Bot Token**:
   - Create an application at the [Discord Developer Portal](https://discord.com/developers/applications).
   - Under **Bot**, enable **Message Content Intent**.

---

## Setup & Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/L0wree/DiscordMusicBot.git
   cd DiscordMusicBot
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure your Bot Token:**
   Copy `.env.example` to `.env` and paste your Discord bot token:
   ```env
   DISCORD_TOKEN=your_discord_bot_token_here
   ```

---

## Running the Bot

- **System Tray Mode (Windows - Silent):**
  Double-click `start_bot.bat` (or `run_silent.vbs`). An icon will appear in your Windows system tray.
- **Stop Background Bot:**
  Right-click the tray icon and click **Exit**, or run `stop_bot.bat`.
- **Standard Console Mode:**
  ```bash
  python main.py
  ```

---

## Bot Commands

| Command | Aliases | Description |
| :--- | :--- | :--- |
| `!play <query/url>` | | Searches YouTube or plays a URL/playlist and adds to the queue |
| `!followme` | `!fm`, `!sync` | Toggles Follow Mode — syncs playback, skips, seeks, and pauses with your local browser (YouTube Music / YouTube) |
| `!unfollow` | `!uf`, `!stopfollow` | Disables Follow Mode |
| `!queue` | `!q`, `!list` | Displays the current song queue |
| `!skip` | | Skips the currently playing song |
| `!stop` | | Stops playback, disables follow mode, and clears the queue |
| `!join` | | Joins your current voice channel |
| `!leave` | | Disconnects the bot from the voice channel |

---

## License

Licensed under the [MIT License](LICENSE).
