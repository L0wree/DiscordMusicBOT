import pystray
from PIL import Image, ImageDraw
import threading
import sys
import os
import asyncio
import subprocess
import updater
import main

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, 'bot.log')

# Function to create a simple icon image
def create_image():
    width = 64
    height = 64
    color_bg = (50, 50, 50)
    color_fg = (100, 200, 100)
    image = Image.new('RGB', (width, height), color_bg)
    dc = ImageDraw.Draw(image)
    # Draw a play symbol (triangle)
    dc.polygon([(16, 16), (16, 48), (48, 32)], fill=color_fg)
    return image

def run_bot():
    """Runs the Discord bot in a separate thread."""
    try:
        asyncio.run(main.main())
    except Exception as e:
        print(f"Bot error: {e}")

def on_exit(icon, item):
    """Callback for the 'Exit' menu item."""
    icon.stop()

class FileLogger:
    def __init__(self, filename):
        self.filename = filename
        self.file = open(filename, 'a', encoding='utf-8')

    def write(self, message):
        self.file.write(message)
        self.file.flush()

    def flush(self):
        self.file.flush()

def on_show_console(icon, item):
    # Pops up a PowerShell window that live-tails the log file.
    script = f'Get-Content "{LOG_PATH}" -Wait -Tail 100'
    subprocess.Popen(['cmd.exe', '/c', 'start', 'powershell', '-NoProfile', '-Command', script])

def on_check_updates(icon, item):
    threading.Thread(target=updater.check_and_apply_updates, kwargs={"auto_apply": True}, daemon=True).start()

def main_app():
    # Redirect all print outputs to the log file
    sys.stdout = FileLogger(LOG_PATH)
    sys.stderr = sys.stdout

    print("\n\n--- Bot Started ---")

    # Check for package updates in background on startup
    threading.Thread(target=updater.check_and_apply_updates, kwargs={"auto_apply": True}, daemon=True).start()

    # Start the bot in a daemon thread
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    # Create and run the tray icon
    image = create_image()
    icon = pystray.Icon("DiscordMusicBot", image, "Music Bot (Discord)", menu=pystray.Menu(
        pystray.MenuItem("Open Live Console", on_show_console),
        pystray.MenuItem("Check for Updates", on_check_updates),
        pystray.MenuItem("Exit", on_exit)
    ))
    
    # icon.run() blocks until icon.stop() is called
    icon.run()

if __name__ == "__main__":
    main_app()

