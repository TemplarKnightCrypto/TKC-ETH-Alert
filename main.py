import discord
from discord.ext import commands
import requests
import os

# === Get ETH Price from Binance ===
def get_eth_price_binance():
    url = "https://api.binance.com/api/v3/ticker/price?symbol=ETHUSDT"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        return float(data['price'])
    except requests.exceptions.RequestException as e:
        print(f"Error fetching price: {e}")
        return None

# === Bot Setup ===
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")

# === !price Command ===
@bot.command()
async def price(ctx):
    eth_price = get_eth_price_binance()
    if eth_price:
        await ctx.send(f"📈 **ETH/USDT Price:** ${eth_price:,.2f}")
    else:
        await ctx.send("⚠️ Couldn't fetch ETH price from Binance.")

# === Run Bot ===
import os
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not DISCORD_BOT_TOKEN:
    raise ValueError("⚠️ DISCORD_BOT_TOKEN is not set in environment variables.")

bot.run(DISCORD_BOT_TOKEN)
