import discord
from discord.ext import commands, tasks
import requests
import os
import datetime
import threading
from flask import Flask

# === Flask Setup for UptimeRobot ===
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "ETH Trade Bot is running"

def run_flask():
    flask_app.run(host="0.0.0.0", port=8000)

threading.Thread(target=run_flask).start()

# === Discord Bot Setup ===
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# === Global Alert Flags ===
alert_sent = {
    "breakout": False,
    "pullback": False
}

# === ETH Price Fetcher ===
def get_eth_price():
    try:
        response = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd")
        return response.json()["ethereum"]["usd"]
    except Exception as e:
        print("Error fetching ETH price:", e)
        return None

# === Trade Check Task ===
@tasks.loop(seconds=60)
async def check_trade():
    channel = discord.utils.get(bot.get_all_channels(), name="eth-alerts")  # replace with your actual channel name
    if not channel:
        print("Alert channel not found.")
        return

    price = get_eth_price()
    if not price:
        return

    # === Strategy Calculations ===
    entry_long = round(price * 0.9945, 2)
    stop_long  = round(price * 0.969, 2)
    tp1_long   = round(price * 1.0114, 2)
    tp2_long   = round(price * 1.0339, 2)

    pb_zone_low  = round(price * 0.9693, 2)
    pb_zone_high = round(price * 0.9777, 2)
    pb_stop      = round(price * 0.9583, 2)
    pb_tp1       = round(price * 0.9946, 2)
    pb_tp2       = round(price * 1.0114, 2)

    # === Breakout Long Trigger ===
    if price > entry_long and not alert_sent["breakout"]:
        alert_sent["breakout"] = True
        msg = (
            f"📢 **ETH Breakout Strategy Triggered**\n"
            f"Entry: Close above ${entry_long}\n"
            f"Stop Loss: Below ${stop_long}\n"
            f"TP1: ${tp1_long}\n"
            f"TP2: ${tp2_long}"
        )
        await channel.send(f"@everyone\n{msg}")

    # === Pullback Long Trigger ===
    if pb_zone_low <= price <= pb_zone_high and not alert_sent["pullback"]:
        alert_sent["pullback"] = True
        msg = (
            f"📉 **ETH Pullback Strategy Triggered**\n"
            f"Buy Zone: ${pb_zone_low}–${pb_zone_high}\n"
            f"Stop Loss: Below ${pb_stop}\n"
            f"TP1: ${pb_tp1}\n"
            f"TP2: ${pb_tp2}"
        )
        await channel.send(f"@everyone\n{msg}")

# === !trade Command ===
@bot.command()
async def trade(ctx):
    alert_sent["breakout"] = False
    alert_sent["pullback"] = False

    price = get_eth_price()
    if not price:
        await ctx.send("⚠️ Couldn't fetch ETH price.")
        return

    entry_long = round(price * 0.9945, 2)
    stop_long  = round(price * 0.969, 2)
    tp1_long   = round(price * 1.0114, 2)
    tp2_long   = round(price * 1.0339, 2)

    pb_zone_low  = round(price * 0.9693, 2)
    pb_zone_high = round(price * 0.9777, 2)
    pb_stop      = round(price * 0.9583, 2)
    pb_tp1       = round(price * 0.9946, 2)
    pb_tp2       = round(price * 1.0114, 2)

    msg = (
        f"🔄 Trade conditions reset. New alerts will be sent when conditions are met.\n\n"
        f"**ETH Trade Strategies (On-Demand)**\n\n"
        f"📈 **Breakout Strategy**\n"
        f"• Entry: Close above ${entry_long}\n"
        f"• Stop Loss: Below ${stop_long}\n"
        f"• TP1: ${tp1_long}\n"
        f"• TP2: ${tp2_long}\n\n"
        f"📉 **Pullback Strategy**\n"
        f"• Buy Zone: ${pb_zone_low} – ${pb_zone_high}\n"
        f"• Stop Loss: Below ${pb_stop}\n"
        f"• TP1: ${pb_tp1}\n"
        f"• TP2: ${pb_tp2}"
    )

    await ctx.send(msg)

# === Bot Ready ===
@bot.event
async def on_ready():
    print(f"✅ Bot logged in as {bot.user}")
    check_trade.start()

# === Start Bot ===
bot.run(os.getenv("DISCORD_TOKEN"))
