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

# === Flask Uptime Server ===
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

# === Binance API Endpoints ===
BINANCE_KLINES = "https://api.binance.com/api/v3/klines?symbol=ETHUSDT&interval=5m&limit=100"
BINANCE_PRICE = "https://api.binance.com/api/v3/ticker/price?symbol=ETHUSDT"

# === Strategy State ===
alert_sent = False
alert_channel_id = None

# === Indicator Calculation ===
def get_eth_data():
    res = requests.get(BINANCE_KLINES)
    data = res.json()
    df = pd.DataFrame(data, columns=[
        'time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'trades',
        'taker_buy_base', 'taker_buy_quote', 'ignore'
    ])
    df['time'] = pd.to_datetime(df['time'], unit='ms')
    df = df[['time', 'open', 'high', 'low', 'close', 'volume']].astype(float)
    df['ema50'] = ema_indicator(df['close'], window=50)
    df['rsi'] = rsi(df['close'], window=14)
    df['atr'] = average_true_range(df['high'], df['low'], df['close'], window=14)
    return df

def get_current_price():
    try:
        res = requests.get(BINANCE_PRICE)
        return float(res.json()['price'])
    except:
        return None

# === Strategy Logic ===
def analyze_strategy(df):
    global alert_sent
    current_price = df['close'].iloc[-1]
    ema50 = df['ema50'].iloc[-1]
    rsi_val = df['rsi'].iloc[-1]
    atr_val = df['atr'].iloc[-1]
    atr_change = df['atr'].iloc[-1] - df['atr'].iloc[-5]

    swing_high = df['high'].iloc[-20:].max()
    swing_low = df['low'].iloc[-20:].min()
    fib_0_382 = swing_low + 0.382 * (swing_high - swing_low)
    fib_0_618 = swing_low + 0.618 * (swing_high - swing_low)

    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # === Long: Breakout ===
    if current_price > ema50 and rsi_val > 60 and atr_change > 0 and not alert_sent:
        stop = round(current_price - 1.5 * atr_val, 2)
        tp = round(current_price + 2.0 * atr_val, 2)
        rr = round((tp - current_price) / (current_price - stop), 2)
        alert_sent = True
        return f"""🟢 **ETH Breakout Long**

💰 Price: ${current_price:,.2f}
📈 EMA50: ${ema50:,.2f}
📊 RSI: {rsi_val:.2f}
📶 ATR Increasing: ✅

🛑 Stop Loss: ${stop}
🎯 Take Profit: ${tp}
📏 Risk/Reward: {rr}:1

🕒 {now}"""

    # === Long: Pullback ===
    elif fib_0_382 <= current_price <= fib_0_618 and current_price > ema50 and rsi_val < 40 and not alert_sent:
        stop = round(current_price - 1.2 * atr_val, 2)
        tp = round(current_price + 1.8 * atr_val, 2)
        rr = round((tp - current_price) / (current_price - stop), 2)
        alert_sent = True
        return f"""🟢 **ETH Pullback Long**

💰 Price: ${current_price:,.2f}
📉 Fib Zone: ${fib_0_382:,.2f} – ${fib_0_618:,.2f}
📊 RSI: {rsi_val:.2f}
📈 Trend: Above EMA50 ✅

🛑 Stop Loss: ${stop}
🎯 Take Profit: ${tp}
📏 Risk/Reward: {rr}:1

🕒 {now}"""

    # === Short: Breakdown ===
    elif current_price < ema50 and rsi_val < 40 and atr_change > 0 and not alert_sent:
        stop = round(current_price + 1.5 * atr_val, 2)
        tp = round(current_price - 2.0 * atr_val, 2)
        rr = round((current_price - tp) / (stop - current_price), 2)
        alert_sent = True
        return f"""🔴 **ETH Breakdown Short**

💰 Price: ${current_price:,.2f}
📉 EMA50: ${ema50:,.2f}
📊 RSI: {rsi_val:.2f}
📶 ATR Increasing: ✅

🛑 Stop Loss: ${stop}
🎯 Take Profit: ${tp}
📏 Risk/Reward: {rr}:1

🕒 {now}"""

    # === Short: Pullback ===
    elif fib_0_382 <= current_price <= fib_0_618 and current_price < ema50 and rsi_val > 60 and not alert_sent:
        stop = round(current_price + 1.2 * atr_val, 2)
        tp = round(current_price - 1.8 * atr_val, 2)
        rr = round((current_price - tp) / (stop - current_price), 2)
        alert_sent = True
        return f"""🔴 **ETH Pullback Short**

💰 Price: ${current_price:,.2f}
📈 Fib Zone: ${fib_0_382:,.2f} – ${fib_0_618:,.2f}
📊 RSI: {rsi_val:.2f}
📉 Trend: Below EMA50 ✅

🛑 Stop Loss: ${stop}
🎯 Take Profit: ${tp}
📏 Risk/Reward: {rr}:1

🕒 {now}"""

    return None

# === Monitoring Task ===
@tasks.loop(seconds=60)
async def monitor_price():
    global alert_channel_id
    if not alert_channel_id:
        return
    try:
        df = get_eth_data()
        signal = analyze_strategy(df)
        if signal:
            channel = bot.get_channel(alert_channel_id)
            price = get_current_price()
            if price:
                await channel.send(f"📡 **ETH Price:** ${price:,.2f}")
            await channel.send(signal)
    except Exception as e:
        print("Monitor error:", e)

# === Bot Events and Commands ===
@bot.event
async def on_ready():
    print(f"✅ Bot is online as {bot.user}")
    monitor_price.start()

@bot.command(name="setchannel")
async def setchannel(ctx):
    global alert_channel_id
    alert_channel_id = ctx.channel.id
    await ctx.send("📡 This channel is now set for live ETH alerts.")

@bot.command(name="price")
async def price(ctx):
    price = get_current_price()
    if price:
        await ctx.send(f"💰 ETH Price: ${price:,.2f}")
    else:
        await ctx.send("⚠️ Couldn't fetch ETH price.")

@bot.command(name="strategy")
async def strategy(ctx):
    try:
        df = get_eth_data()
        # Temporarily allow alert to bypass alert_sent check
        global alert_sent
        original_state = alert_sent
        alert_sent = False  # Force it to generate signal
        signal = analyze_strategy(df)
        alert_sent = original_state  # Restore original state

        if signal:
            price = get_current_price()
            if price:
                await ctx.send(f"📡 ETH Price: ${price:,.2f}")
            await ctx.send(f"📌 **Manual Strategy Check Result:**\n{signal}")
        else:
            await ctx.send("ℹ️ No valid trade signal found at this moment.")
    except Exception as e:
        await ctx.send(f"⚠️ Error fetching strategy: {e}")

@bot.command(name="reset")
async def reset(ctx):
    global alert_sent
    alert_sent = False
    await ctx.send("🔄 Alert state reset. New alerts will now trigger.")

# === Run Bot ===
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
bot.run(DISCORD_TOKEN)
