import discord
from discord.ext import commands, tasks
import requests
import os
import datetime
import threading
import pandas as pd
import numpy as np
from flask import Flask

# === Flask Setup for Uptime ===
flask_app = Flask(__name__)
@flask_app.route("/")
def home():
    return "ETH Indicator Bot is live"
threading.Thread(target=lambda: flask_app.run(host="0.0.0.0", port=8000)).start()

# === Discord Bot Setup ===
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

alert_sent = {"breakout": False, "pullback": False}

# === Binance OHLCV Fetch ===
def get_binance_ohlcv(symbol="ETHUSDT", interval="1h", limit=100):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        data = requests.get(url).json()
        df = pd.DataFrame(data, columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "_", "_", "_", "_", "_", "_"
        ])
        df["close"] = df["close"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        return df
    except Exception as e:
        print("OHLCV fetch error:", e)
        return None

# === Indicator Calculations ===
def calculate_indicators(df):
    try:
        df["EMA20"] = df["close"].ewm(span=20).mean()
        df["EMA50"] = df["close"].ewm(span=50).mean()
        delta = df["close"].diff()
        gain = np.where(delta > 0, delta, 0)
        loss = np.where(delta < 0, -delta, 0)
        avg_gain = pd.Series(gain).rolling(window=14).mean()
        avg_loss = pd.Series(loss).rolling(window=14).mean()
        rs = avg_gain / avg_loss
        df["RSI"] = 100 - (100 / (1 + rs))
        df["TR"] = np.maximum(
            df["high"] - df["low"],
            np.maximum(abs(df["high"] - df["close"].shift()), abs(df["low"] - df["close"].shift()))
        )
        df["ATR"] = df["TR"].rolling(window=14).mean()
        return df
    except Exception as e:
        print("Indicator error:", e)
        return None

# === Price Fetcher ===
def get_eth_price():
    try:
        response = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd")
        return response.json()["ethereum"]["usd"]
    except:
        return None

# === Strategy Generator ===
def generate_strategy(df, current_price):
    last = df.iloc[-1]
    ema_trend = "uptrend" if last["EMA20"] > last["EMA50"] else "downtrend"
    breakout_entry = round(last["EMA20"] + (0.5 * last["ATR"]), 2)
    breakout_stop = round(last["EMA20"] - (1.5 * last["ATR"]), 2)
    breakout_tp1 = round(breakout_entry + (1.5 * last["ATR"]), 2)
    breakout_tp2 = round(breakout_entry + (3.0 * last["ATR"]), 2)

    pb_zone_low = round(last["EMA50"] - (1.0 * last["ATR"]), 2)
    pb_zone_high = round(last["EMA50"], 2)
    pb_stop = round(pb_zone_low - (1.5 * last["ATR"]), 2)
    pb_tp1 = round(last["EMA50"] + (1.5 * last["ATR"]), 2)
    pb_tp2 = round(last["EMA50"] + (3.0 * last["ATR"]), 2)

    return {
        "trend": ema_trend,
        "breakout": {
            "entry": breakout_entry,
            "stop": breakout_stop,
            "tp1": breakout_tp1,
            "tp2": breakout_tp2
        },
        "pullback": {
            "zone_low": pb_zone_low,
            "zone_high": pb_zone_high,
            "stop": pb_stop,
            "tp1": pb_tp1,
            "tp2": pb_tp2
        }
    }

# === Trade Monitor ===
@tasks.loop(seconds=60)
async def check_trade():
    channel = discord.utils.get(bot.get_all_channels(), name="eth-alerts")
    if not channel:
        return

    df = get_binance_ohlcv()
    if df is None:
        print("Couldn't fetch OHLCV")
        return

    df = calculate_indicators(df)
    if df is None:
        print("Couldn't compute indicators")
        return

    current_price = get_eth_price()
    if current_price is None:
        print("Couldn't fetch ETH price")
        return

    strat = generate_strategy(df, current_price)

    # Breakout logic
    if current_price > strat["breakout"]["entry"] and not alert_sent["breakout"]:
        alert_sent["breakout"] = True
        embed = discord.Embed(title="📈 ETH Breakout Strategy Triggered", color=discord.Color.green(), timestamp=datetime.datetime.utcnow())
        embed.add_field(name="Trend", value=strat["trend"], inline=False)
        embed.add_field(name="Entry", value=f"${strat['breakout']['entry']}", inline=False)
        embed.add_field(name="Stop Loss", value=f"${strat['breakout']['stop']}", inline=False)
        embed.add_field(name="TP1", value=f"${strat['breakout']['tp1']}", inline=True)
        embed.add_field(name="TP2", value=f"${strat['breakout']['tp2']}", inline=True)
        embed.set_footer(text="ETH Indicator Bot")
        await channel.send("@everyone", embed=embed)

    # Pullback logic
    if strat["pullback"]["zone_low"] <= current_price <= strat["pullback"]["zone_high"] and not alert_sent["pullback"]:
        alert_sent["pullback"] = True
        embed = discord.Embed(title="📉 ETH Pullback Strategy Triggered", color=discord.Color.blue(), timestamp=datetime.datetime.utcnow())
        embed.add_field(name="Buy Zone", value=f"${strat['pullback']['zone_low']} – ${strat['pullback']['zone_high']}", inline=False)
        embed.add_field(name="Stop Loss", value=f"${strat['pullback']['stop']}", inline=False)
        embed.add_field(name="TP1", value=f"${strat['pullback']['tp1']}", inline=True)
        embed.add_field(name="TP2", value=f"${strat['pullback']['tp2']}", inline=True)
        embed.set_footer(text="ETH Indicator Bot")
        await channel.send("@everyone", embed=embed)

# === !trade Command ===
@bot.command()
async def trade(ctx):
    alert_sent["breakout"] = False
    alert_sent["pullback"] = False
    await ctx.send("🔄 Trade conditions reset. New alerts will be sent when conditions are met.")

    df = get_binance_ohlcv()
    if df is None:
        await ctx.send("⚠️ Couldn't fetch OHLCV data.")
        return

    df = calculate_indicators(df)
    if df is None:
        await ctx.send("⚠️ Couldn't compute indicators.")
        return

    current_price = get_eth_price()
    if current_price is None:
        await ctx.send("⚠️ Couldn't fetch ETH price.")
        return

    strat = generate_strategy(df, current_price)

    embed = discord.Embed(title="📊 ETH Trade Strategies (Dynamic)", color=discord.Color.gold(), timestamp=datetime.datetime.utcnow())
    embed.add_field(name="📈 Breakout Strategy", value=(
        f"• Entry: **${strat['breakout']['entry']}**\n"
        f"• Stop: **${strat['breakout']['stop']}**\n"
        f"• TP1: ${strat['breakout']['tp1']}\n"
        f"• TP2: ${strat['breakout']['tp2']}"
    ), inline=False)
    embed.add_field(name="📉 Pullback Strategy", value=(
        f"• Zone: ${strat['pullback']['zone_low']} – ${strat['pullback']['zone_high']}\n"
        f"• Stop: ${strat['pullback']['stop']}\n"
        f"• TP1: ${strat['pullback']['tp1']}\n"
        f"• TP2: ${strat['pullback']['tp2']}"
    ), inline=False)
    embed.set_footer(text="ETH Indicator Bot")
    await ctx.send(embed=embed)

# === Bot Ready ===
@bot.event
async def on_ready():
    print(f"Bot is live as {bot.user}")
    check_trade.start()

bot.run(os.getenv("DISCORD_TOKEN"))
