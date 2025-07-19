import os
import discord
import requests
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix='/', intents=intents)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')

@bot.command(name='eth', help='Get the current ETH price')
async def eth(ctx):
    url = 'https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd'
    try:
        response = requests.get(url)
        data = response.json()
        price = data['ethereum']['usd']
        await ctx.send(f'🪙 Ethereum (ETH) Price: ${price:,}')
    except Exception as e:
        await ctx.send("Failed to fetch price.")
        print(e)

bot.run(TOKEN)
