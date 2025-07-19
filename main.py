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

# === Trade Monitor Loop ===
@tasks.loop(seconds=60)
async def check_trade():
    channel = discord.utils.get(bot.get_all_channels(), name="eth-alerts")
    if not channel:
        print("Alert channel not found.")
        return

    price = get_eth_price()
    if not price:
        return

    now = datetime.datetime.utcnow()

    # Strategy calculations
    entry_long = round(price * 0.9945, 2)
    stop_long  = round(price * 0.969, 2)
    tp1_long   = round(price * 1.0114, 2)
    tp2_long   = round(price * 1.0339, 2)

    pb_zone_low  = round(price * 0.9693, 2)
    pb_zone_high = round(price * 0.9777, 2)
    pb_stop      = round(price * 0.9583, 2)
    pb_tp1       = round(price * 0.9946, 2)
    pb_tp2       = round(price * 1.0114, 2)

    # === Breakout Long Alert ===
    if price > entry_long and not alert_sent["breakout"]:
        alert_sent["breakout"] = True
        embed = discord.Embed(
            title="📢 ETH Breakout Strategy Triggered",
            color=discord.Color.green(),
            timestamp=now
        )
        embed.add_field(name="Entry", value=f"Close above **${entry_long}**", inline=False)
        embed.add_field(name="Stop Loss", value=f"Below **${stop_long}**", inline=False)
        embed.add_field(name="TP1", value=f"${tp1_long}", inline=True)
        embed.add_field(name="TP2", value=f"${tp2_long}", inline=True)
        embed.set_footer(text="ETH Trade Bot")
        await channel.send("@everyone", embed=embed)

    # === Pullback Long Alert ===
    if pb_zone_low <= price <= pb_zone_high and not alert_sent["pullback"]:
        alert_sent["pullback"] = True
        embed = discord.Embed(
            title="📉 ETH Pullback Strategy Triggered",
            color=discord.Color.blue(),
            timestamp=now
        )
        embed.add_field(name="Buy Zone", value=f"${pb_zone_low} – ${pb_zone_high}", inline=False)
        embed.add_field(name="Stop Loss", value=f"Below **${pb_stop}**", inline=False)
        embed.add_field(name="TP1", value=f"${pb_tp1}", inline=True)
        embed.add_field(name="TP2", value=f"${pb_tp2}", inline=True)
        embed.set_footer(text="ETH Trade Bot")
        await channel.send("@everyone", embed=embed)

# === !trade Command ===
@bot.command()
async def trade(ctx):
    # Reset flags only once
    if alert_sent["breakout"] or alert_sent["pullback"]:
        alert_sent["breakout"] = False
        alert_sent["pullback"] = False
        await ctx.send("🔄 Trade conditions reset. New alerts will be sent when conditions are met.")

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

    embed = discord.Embed(
        title="📊 ETH Trade Strategies (On-Demand)",
        color=discord.Color.gold(),
        timestamp=datetime.datetime.utcnow()
    )
    embed.add_field(
        name="📈 Breakout Strategy",
        value=(
            f"• Entry: Close above **${entry_long}**\n"
            f"• Stop Loss: Below **${stop_long}**\n"
            f"• TP1: ${tp1_long}\n"
            f"• TP2: ${tp2_long}"
        ),
        inline=False
    )
    embed.add_field(
        name="📉 Pullback Strategy",
        value=(
            f"• Buy Zone: ${pb_zone_low} – ${pb_zone_high}\n"
            f"• Stop Loss: Below **${pb_stop}**\n"
            f"• TP1: ${pb_tp1}\n"
            f"• TP2: ${pb_tp2}"
        ),
        inline=False
    )
    embed.set_footer(text="ETH Trade Bot")
    await ctx.send(embed=embed)

# === Startup ===
@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")
    check_trade.start()

bot.run(os.getenv("DISCORD_TOKEN"))
