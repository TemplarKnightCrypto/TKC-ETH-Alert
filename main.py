import os
import requests
import threading
from flask import Flask
import discord
from discord.ext import commands

# === Flask App for Uptime Pings ===
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ ETH Discord bot is live!"

def run_flask():
    app.run(host='0.0.0.0', port=8000)

flask_thread = threading.Thread(target=run_flask)
flask_thread.start()

# === Function to Get ETH Price from Binance ===
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

# === Discord Bot Setup ===
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")

@bot.command()
async def price(ctx):
    eth_price = get_eth_price_binance()
    if eth_price:
        await ctx.send(f"📈 **ETH/USDT Price:** ${eth_price:,.2f}")
    else:
        await ctx.send("⚠️ Couldn't fetch ETH price from Binance.")

# === Run the Bot ===
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not DISCORD_BOT_TOKEN:
    raise ValueError("⚠️ DISCORD_BOT_TOKEN is not set in environment variables.")

bot.run(DISCORD_BOT_TOKEN)

