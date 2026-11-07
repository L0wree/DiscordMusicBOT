import discord
from discord.ext import commands
import os
import sys
import asyncio
from dotenv import load_dotenv

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')

@bot.event
async def on_command_completion(ctx):
    print(f"Command executed: {ctx.message.content} by {ctx.author}")

@bot.event
async def on_command_error(ctx, error):
    print(f"Command error by {ctx.author}: {error}")

async def main():
    token = os.getenv('DISCORD_TOKEN')
    if not token or token == 'your_discord_bot_token_here':
        print("ERROR: DISCORD_TOKEN is not set. Please copy .env.example to .env and add your bot token.")
        return

    async with bot:
        try:
            await bot.load_extension('music_cog')
            print(f"Loaded commands safely: {[c.name for c in bot.commands]}")
        except Exception as e:
            print(f"Failed to load music_cog: {e}")
        await bot.start(token)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

