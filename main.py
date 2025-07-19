import discord
from discord.ext import commands, tasks
import requests
import os
import threading
from flask import Flask
import datetime
import numpy as np
import pandas as pd

# === Flask App for Uptime Pings ===
app = Flask(__name__)

@app.route("/")
def home():
    return "ETH Bot is live!"

def run_flask():
    app.run(host="0.0.0.0", port=8000)

threading.Thread(target=run_flask).start()

# === Discord Bot Setup ===
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# === Strategy State ===
strategy_active = False
alert_sent = False
entry_price = None
strategy_levels = {}

# === Utility: Fetch ETH Price from CoinGecko ===
def fetch_eth_price():
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd"
        response = requests.get(url)
        return response.json()["ethereum"]["usd"]
    except Exception as e:
        print("Price fetch error:", e)
        return None

# === Indicator Calculation (Mock Data + Trend) ===
def get_indicators():
    try:
        url = "https://api.coingecko.com/api/v3/coins/ethereum/market_chart?vs_currency=usd&days=1&interval=hourly"
        response = requests.get(url)
        prices = [x[1] for x in response.json()["prices"]]
        df = pd.DataFrame(prices, columns=["close"])
        df["ema20"] = df["close"].ewm(span=20).mean()
        df["rsi"] = 100 - (100 / (1 + df["close"].pct_change().rolling(14).mean() / df["close"].pct_change().rolling(14).std()))
        df["trend"] = np.where(df["ema20"].iloc[-1] > df["ema20"].iloc[-2], "up", "down")
        return {
            "ema": round(df["ema20"].iloc[-1], 2),
            "rsi": round(df["rsi"].iloc[-1], 2),
            "trend": df["trend"].iloc[-1]
        }
    except Exception as e:
        print("Indicator error:", e)
        return None

# === Dynamic Strategy Generator ===
def generate_strategy(price):
    return {
        "Breakout Entry": round(price * 0.994, 2),
        "Breakout SL": round(price * 0.9685, 2),
        "Breakout TP1": round(price * 1.0105, 2),
        "Breakout TP2": round(price * 1.033, 2),
        "Pullback Buy Zone": f"{round(price * 0.9685, 2)} – {round(price * 0.975, 2)}",
        "Pullback SL": round(price * 0.947, 2),
        "Pullback TP1": round(price * 1.01, 2),
        "Pullback TP2": round(price * 1.03, 2)
    }

# === Command: !trade ===
@bot.command()
async def trade(ctx):
    global strategy_active, alert_sent, entry_price, strategy_levels
    entry_price = fetch_eth_price()
    indicators = get_indicators()

    if not entry_price or not indicators:
        await ctx.send("⚠️ Error fetching price or indicators.")
        return

    strategy_levels = generate_strategy(entry_price)
    strategy_active = True
    alert_sent = False

    strategy_msg = (
        f"**ETH Trade Strategies (Live)**\n\n"
        f"📈 Price: ${entry_price}\n"
        f"📊 Trend: {indicators['trend']} | EMA: {indicators['ema']} | RSI: {indicators['rsi']}\n\n"
        f"✅ **Breakout Long**\n"
        f"• Entry: Above ${strategy_levels['Breakout Entry']}\n"
        f"• Stop: Below ${strategy_levels['Breakout SL']}\n"
        f"• TP1: ${strategy_levels['Breakout TP1']} | TP2: ${strategy_levels['Breakout TP2']}\n\n"
        f"✅ **Pullback Long**\n"
        f"• Buy Zone: {strategy_levels['Pullback Buy Zone']}\n"
        f"• Stop: Below ${strategy_levels['Pullback SL']}\n"
        f"• TP1: ${strategy_levels['Pullback TP1']} | TP2: ${strategy_levels['Pullback TP2']}\n\n"
        f"⏱️ Strategy monitoring is active."
    )
    await ctx.send(strategy_msg)

# === Command: !forecast ===
@bot.command()
async def forecast(ctx):
    price = fetch_eth_price()
    indicators = get_indicators()
    if not price or not indicators:
        await ctx.send("⚠️ Could not fetch forecast data.")
        return
    await ctx.send(
        f"📉 ETH Forecast\n\n"
        f"Price: ${price}\n"
        f"Trend: {indicators['trend']} | EMA: {indicators['ema']} | RSI: {indicators['rsi']}"
    )

# === Bot Startup ===
@bot.event
async def on_ready():
    print(f"✅ Bot connected as {bot.user}")

# === Launch Bot ===
bot.run(os.getenv("DISCORD_TOKEN"))

