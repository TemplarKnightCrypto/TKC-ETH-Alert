import os
import discord
import asyncio
import json
import websockets
import pandas as pd
import numpy as np
from ta.trend import EMAIndicator
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange
from flask import Flask
from threading import Thread
from dotenv import load_dotenv
from datetime import datetime

# === Load ENV Variables ===
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "0"))
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))

# === Flask Uptime ===
app = Flask(__name__)
@app.route("/")
def home():
    return "Bot is live!"
Thread(target=app.run, kwargs={"host": "0.0.0.0", "port": 8000}).start()

# === Discord Setup ===
intents = discord.Intents.default()
intents.message_content = True
bot = discord.Bot(intents=intents)

# === Trade State ===
monitoring = False
price_data = pd.DataFrame(columns=["time", "price"])

# === Technical Logic ===
def calculate_indicators(df):
    df["EMA20"] = EMAIndicator(df["price"], window=20).ema_indicator()
    df["RSI"] = RSIIndicator(df["price"], window=14).rsi()
    df["ATR"] = AverageTrueRange(df["price"], df["price"], df["price"], window=14).average_true_range()
    df["Trend"] = np.where(df["EMA20"] > df["EMA20"].shift(1), "Up", "Down")
    return df

async def monitor_eth(channel):
    uri = "wss://stream.binance.com:9443/ws/ethusdt@trade"
    async with websockets.connect(uri) as websocket:
        global price_data
        async for message in websocket:
            data = json.loads(message)
            price = float(data["p"])
            timestamp = datetime.utcnow()

            price_data = pd.concat([price_data, pd.DataFrame([{"time": timestamp, "price": price}])])
            if len(price_data) > 100:
                price_data = price_data.iloc[-100:]

            if len(price_data) >= 20:
                price_data = calculate_indicators(price_data)
                latest = price_data.iloc[-1]
                if latest["RSI"] < 30 and latest["Trend"] == "Up":
                    await channel.send(f"🟢 **Long Signal (RSI < 30 + Uptrend)**\nPrice: ${price:.2f} | RSI: {latest['RSI']:.2f}")

# === Discord Commands ===
@bot.command()
async def trade(ctx):
    global monitoring
    if monitoring:
        await ctx.respond("Trade monitoring is already active.")
        return
    monitoring = True
    await ctx.respond("📊 Trade monitoring activated with real-time indicators.")
    channel = bot.get_channel(CHANNEL_ID)
    await monitor_eth(channel)

# === Run Bot ===
bot.run(TOKEN)
