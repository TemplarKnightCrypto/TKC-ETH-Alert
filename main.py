import discord
from discord.ext import commands, tasks
import requests
import os
import datetime
import threading
import pandas as pd
from flask import Flask
from ta.trend import EMAIndicator
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange

# === Flask for Uptime ===
flask_app = Flask(__name__)
@flask_app.route("/")
def home():
    return "ETH Indicator Bot Live"
threading.Thread(target=lambda: flask_app.run(host="0.0.0.0", port=8000)).start()

# === Discord Bot ===
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

alert_sent = {"breakout": False, "pullback": False}

# === Fetch OHLC Data for ETH ===
def fetch_ohlc():
    try:
        url = "https://api.coingecko.com/api/v3/coins/ethereum/ohlc?vs_currency=usd&days=1"
        r = requests.get(url)
        data = r.json()
        df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df
    except Exception as e:
        print("OHLC Fetch Error:", e)
        return None

# === Calculate Strategy Levels ===
def calculate_indicators():
    df = fetch_ohlc()
    if df is None or df.empty:
        return None

    close = df["close"]
    high = df["high"]
    low = df["low"]

    df["ema20"] = EMAIndicator(close, window=20).ema_indicator()
    df["rsi"] = RSIIndicator(close, window=14).rsi()
    df["atr"] = AverageTrueRange(high, low, close, window=14).average_true_range()

    latest = df.iloc[-1]
    strategy = {}

    strategy["price"] = round(latest["close"], 2)
    strategy["ema20"] = round(latest["ema20"], 2)
    strategy["rsi"] = round(latest["rsi"], 2)
    strategy["atr"] = round(latest["atr"], 2)

    # Dynamic Strategy (example rules)
    strategy["breakout_entry"] = round(latest["ema20"] + latest["atr"] * 0.5, 2)
    strategy["breakout_sl"] = round(latest["ema20"] - latest["atr"], 2)
    strategy["breakout_tp1"] = round(latest["ema20"] + latest["atr"] * 1.5, 2)
    strategy["breakout_tp2"] = round(latest["ema20"] + latest["atr"] * 2.5, 2)

    strategy["pullback_zone_low"] = round(latest["ema20"] - latest["atr"] * 0.5, 2)
    strategy["pullback_zone_high"] = round(latest["ema20"], 2)
    strategy["pullback_sl"] = round(latest["ema20"] - latest["atr"] * 1.5, 2)
    strategy["pullback_tp1"] = round(latest["ema20"] + latest["atr"] * 1, 2)
    strategy["pullback_tp2"] = round(latest["ema20"] + latest["atr"] * 2, 2)

    return strategy

# === Alert Loop ===
@tasks.loop(seconds=60)
async def check_trade():
    channel = discord.utils.get(bot.get_all_channels(), name="eth-alerts")
    if not channel:
        print("Channel not found.")
        return

    strategy = calculate_indicators()
    if strategy is None:
        return

    price = strategy["price"]
    now = datetime.datetime.utcnow()

    # Breakout Logic
    if price > strategy["breakout_entry"] and not alert_sent["breakout"]:
        alert_sent["breakout"] = True
        embed = discord.Embed(
            title="🚀 ETH Breakout Triggered",
            color=discord.Color.green(),
            timestamp=now
        )
        embed.add_field(name="Entry", value=f"> **${strategy['breakout_entry']}**", inline=False)
        embed.add_field(name="Stop Loss", value=f"< **${strategy['breakout_sl']}**", inline=False)
        embed.add_field(name="TP1", value=f"${strategy['breakout_tp1']}", inline=True)
        embed.add_field(name="TP2", value=f"${strategy['breakout_tp2']}", inline=True)
        embed.set_footer(text="ETH Trade Bot")
        await channel.send("@everyone", embed=embed)

    # Pullback Zone Logic
    if (strategy["pullback_zone_low"] <= price <= strategy["pullback_zone_high"]
            and not alert_sent["pullback"]):
        alert_sent["pullback"] = True
        embed = discord.Embed(
            title="📉 ETH Pullback Zone Entered",
            color=discord.Color.blue(),
            timestamp=now
        )
        embed.add_field(name="Buy Zone", value=f"${strategy['pullback_zone_low']} – ${strategy['pullback_zone_high']}", inline=False)
        embed.add_field(name="Stop Loss", value=f"< **${strategy['pullback_sl']}**", inline=False)
        embed.add_field(name="TP1", value=f"${strategy['pullback_tp1']}", inline=True)
        embed.add_field(name="TP2", value=f"${strategy['pullback_tp2']}", inline=True)
        embed.set_footer(text="ETH Trade Bot")
        await channel.send("@everyone", embed=embed)

# === !trade Command ===
@bot.command()
async def trade(ctx):
    alert_sent["breakout"] = False
    alert_sent["pullback"] = False
    await ctx.send("🔄 Trade conditions reset. New alerts will be sent when conditions are met.")

    strategy = calculate_indicators()
    if strategy is None:
        await ctx.send("⚠️ Couldn't fetch indicators.")
        return

    embed = discord.Embed(
        title="📊 ETH Trade Strategy (Live)",
        color=discord.Color.gold(),
        timestamp=datetime.datetime.utcnow()
    )
    embed.add_field(
        name="📈 Breakout Strategy",
        value=(f"• Entry: > ${strategy['breakout_entry']}\n"
               f"• Stop Loss: < ${strategy['breakout_sl']}\n"
               f"• TP1: ${strategy['breakout_tp1']}\n"
               f"• TP2: ${strategy['breakout_tp2']}"),
        inline=False
    )
    embed.add_field(
        name="📉 Pullback Strategy",
        value=(f"• Buy Zone: ${strategy['pullback_zone_low']} – ${strategy['pullback_zone_high']}\n"
               f"• Stop Loss: < ${strategy['pullback_sl']}\n"
               f"• TP1: ${strategy['pullback_tp1']}\n"
               f"• TP2: ${strategy['pullback_tp2']}"),
        inline=False
    )
    embed.set_footer(text="ETH Trade Bot")
    await ctx.send(embed=embed)

# === Startup ===
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    check_trade.start()

bot.run(os.getenv("DISCORD_TOKEN"))

