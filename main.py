import os
import discord
import threading
import asyncio
import json
import websockets
import numpy as np
import pandas as pd
from discord.ext import commands
from ta.trend import EMAIndicator
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange
from flask import Flask
from datetime import datetime
from dotenv import load_dotenv

# === Load .env variables ===
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

# === Discord Bot Setup ===
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# === Flask Uptime Ping ===
app = Flask(__name__)
@app.route("/")
def home():
    return "Bot is live!"

def run_flask():
    app.run(host="0.0.0.0", port=8000)

flask_thread = threading.Thread(target=run_flask)
flask_thread.start()

# === Globals ===
monitoring = False
price_data = []
max_candles = 100
interval_seconds = 60  # 1-minute candle
last_alert = None

# === Strategy Functions ===
def calculate_indicators(df):
    df['ema20'] = EMAIndicator(df['close'], window=20).ema_indicator()
    df['ema50'] = EMAIndicator(df['close'], window=50).ema_indicator()
    df['rsi'] = RSIIndicator(df['close'], window=14).rsi()
    df['atr'] = AverageTrueRange(df['high'], df['low'], df['close'], window=14).average_true_range()
    return df

def detect_trend(df):
    if df['ema20'].iloc[-1] > df['ema50'].iloc[-1]:
        return "uptrend"
    elif df['ema20'].iloc[-1] < df['ema50'].iloc[-1]:
        return "downtrend"
    else:
        return "sideways"

def calculate_fibonacci_levels(price):
    return {
        "0.0": price,
        "0.382": price * 0.9618,
        "0.5": price * 0.95,
        "0.618": price * 0.9382,
        "1.0": price * 0.9
    }

def generate_strategy(df):
    last_price = df['close'].iloc[-1]
    trend = detect_trend(df)
    fib = calculate_fibonacci_levels(last_price)
    rsi = df['rsi'].iloc[-1]
    atr = df['atr'].iloc[-1]

    strategy_text = f"""📊 **ETH Strategy Setup**
Price: `${last_price:.2f}`  
Trend: `{trend}`  
RSI: `{rsi:.2f}`  
ATR: `{atr:.2f}`  

📈 **Fib Levels**
• 0.382: `${fib['0.382']:.2f}`  
• 0.5: `${fib['0.5']:.2f}`  
• 0.618: `${fib['0.618']:.2f}`  
• 1.0: `${fib['1.0']:.2f}`
"""
    return strategy_text

# === WebSocket Handler ===
async def price_listener():
    global price_data, monitoring, last_alert
    uri = "wss://stream.binance.com:9443/ws/ethusdt@trade"
    async with websockets.connect(uri) as websocket:
        while True:
            if not monitoring:
                await asyncio.sleep(1)
                continue

            msg = await websocket.recv()
            data = json.loads(msg)
            price = float(data['p'])
            ts = int(data['T']) // 1000
            dt = datetime.utcfromtimestamp(ts)

            if not price_data or dt.second < price_data[-1]['timestamp'].second:
                # Create new 1-minute candle
                if len(price_data) >= max_candles:
                    price_data.pop(0)
                price_data.append({
                    'timestamp': dt,
                    'open': price,
                    'high': price,
                    'low': price,
                    'close': price
                })
            else:
                # Update current candle
                price_data[-1]['high'] = max(price_data[-1]['high'], price)
                price_data[-1]['low'] = min(price_data[-1]['low'], price)
                price_data[-1]['close'] = price

            if len(price_data) >= 20:
                df = pd.DataFrame(price_data)
                df = calculate_indicators(df)
                strategy = generate_strategy(df)

                # Alert every 60 seconds only if new
                if strategy != last_alert:
                    channel = bot.get_channel(CHANNEL_ID)
                    if channel:
                        await channel.send(strategy)
                        last_alert = strategy

# === Discord Bot Commands ===
@bot.event
async def on_ready():
    print(f"✅ Bot is live as {bot.user}")

@bot.command()
async def trade(ctx):
    global monitoring
    monitoring = True
    await ctx.send("📡 Trade monitoring activated with real-time indicators.")

# === Launch All ===
def start_all():
    loop = asyncio.get_event_loop()
    loop.create_task(price_listener())
    bot.run(DISCORD_TOKEN)

start_all()
