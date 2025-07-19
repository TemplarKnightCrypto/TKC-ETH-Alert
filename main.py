import os
import discord
import requests
import datetime
import pandas as pd
import numpy as np
from discord.ext import commands, tasks
from flask import Flask
import threading
from ta.trend import ema_indicator
from ta.momentum import rsi
from ta.volatility import average_true_range

# === Flask for Uptime Ping ===
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is live!"

def run_flask():
    app.run(host="0.0.0.0", port=8000)

flask_thread = threading.Thread(target=run_flask)
flask_thread.start()

# === Discord Setup ===
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# === Binance API ===
BINANCE_URL = "https://api.binance.com/api/v3/klines?symbol=ETHUSDT&interval=5m&limit=100"
PRICE_URL = "https://api.binance.com/api/v3/ticker/price?symbol=ETHUSDT"

# === Strategy State ===
alert_sent = False
alert_channel_id = None

# === Indicator Calculation ===
def get_eth_data():
    response = requests.get(BINANCE_URL)
    data = response.json()

    df = pd.DataFrame(data, columns=[
        'time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'trades',
        'taker_buy_base', 'taker_buy_quote', 'ignore'
    ])

    df['time'] = pd.to_datetime(df['time'], unit='ms')
    df = df[['time', 'open', 'high', 'low', 'close', 'volume']].astype(float)

    # Indicators
    df['ema20'] = ema_indicator(df['close'], window=20)
    df['ema50'] = ema_indicator(df['close'], window=50)
    df['rsi'] = rsi(df['close'], window=14)
    df['atr'] = average_true_range(df['high'], df['low'], df['close'], window=14)

    return df

def get_current_price():
    try:
        res = requests.get(PRICE_URL)
        data = res.json()
        return float(data['price'])
    except:
        return None

def analyze_strategy(df):
    global alert_sent

    current_price = df['close'].iloc[-1]
    ema50 = df['ema50'].iloc[-1]
    rsi_val = df['rsi'].iloc[-1]
    atr_val = df['atr'].iloc[-1]

    # Fibonacci Levels
    swing_high = df['high'].iloc[-20:].max()
    swing_low = df['low'].iloc[-20:].min()
    fib_0_382 = swing_low + 0.382 * (swing_high - swing_low)
    fib_0_618 = swing_low + 0.618 * (swing_high - swing_low)

    # Breakout Condition
    if current_price > ema50 and rsi_val > 60 and not alert_sent:
        stop = round(current_price - 1.5 * atr_val, 2)
        tp = round(current_price + 2 * atr_val, 2)
        alert_sent = True
        return f"""📈 **ETH Breakout Long Signal**

💰 Current Price: ${current_price:,.2f}
🔹 EMA50: ${ema50:,.2f}
🔹 RSI: {rsi_val:.2f}

🛑 Stop Loss: ${stop}
🎯 Take Profit: ${tp}

🕒 {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""

    # Pullback Condition
    elif fib_0_382 <= current_price <= fib_0_618 and rsi_val < 40 and not alert_sent:
        stop = round(current_price - 1.2 * atr_val, 2)
        tp = round(current_price + 1.8 * atr_val, 2)
        alert_sent = True
        return f"""🔄 **ETH Pullback Buy Zone**

💰 Current Price: ${current_price:,.2f}
📉 Fibonacci Zone: ${fib_0_382:,.2f} - ${fib_0_618:,.2f}
🔹 RSI: {rsi_val:.2f}

🛑 Stop Loss: ${stop}
🎯 Take Profit: ${tp}

🕒 {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""

    return None

# === Background Price Monitor ===
@tasks.loop(seconds=60)
async def monitor_price():
    global alert_channel_id

    if alert_channel_id is None:
        return

    try:
        df = get_eth_data()
        signal = analyze_strategy(df)

        if signal:
            channel = bot.get_channel(alert_channel_id)
            current_price = get_current_price()
            if current_price:
                await channel.send(f"💎 ETH Price Update: ${current_price:,.2f}")
            await channel.send(signal)
    except Exception as e:
        print("Monitor error:", e)

# === Bot Events and Commands ===
@bot.event
async def on_ready():
    print(f'✅ Bot is online as {bot.user}')
    monitor_price.start()

@bot.command(name="reset")
async def reset(ctx):
    global alert_sent
    alert_sent = False
    await ctx.send("✅ Trade alert condition has been reset.")

@bot.command(name="setchannel")
async def setchannel(ctx):
    global alert_channel_id
    alert_channel_id = ctx.channel.id
    await ctx.send("📡 This channel has been set for auto ETH alerts.")

@bot.command(name="price")
async def price(ctx):
    price = get_current_price()
    if price:
        await ctx.send(f"💰 Current ETH Price: ${price:,.2f}")
    else:
        await ctx.send("⚠️ Couldn't fetch ETH price.")

# === Run Bot ===
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
bot.run(DISCORD_TOKEN)


