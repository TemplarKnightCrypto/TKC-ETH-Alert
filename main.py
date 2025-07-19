import discord
from discord.ext import commands
import requests
import os
from flask import Flask
import threading
from datetime import datetime
import asyncio

# === Flask for Uptime Monitoring (Locked) ===
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is live!"

def run_flask():
    app.run(host="0.0.0.0", port=8000)

# === Discord Bot Setup (Locked) ===
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# === Global State ===
alert_sent = {
    "trade": False,
    "breakout_triggered": False,
    "pullback_triggered": False
}
strategy_conditions = {}

# === Price Fetcher (Locked) ===
def fetch_eth_price():
    url = "https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd"
    response = requests.get(url)
    if response.status_code == 200:
        return response.json()["ethereum"]["usd"]
    return None

# === Monitor Strategy in Background ===
async def monitor_conditions(ctx):
    global strategy_conditions, alert_sent
    await asyncio.sleep(5)

    while not alert_sent["breakout_triggered"] or not alert_sent["pullback_triggered"]:
        price = fetch_eth_price()
        if price is None:
            await ctx.send("⚠️ Error fetching ETH price during monitoring.")
            break

        if not alert_sent["breakout_triggered"] and price > strategy_conditions["breakout_entry"]:
            alert_sent["breakout_triggered"] = True
            await ctx.send(f"📢 **Breakout Entry Triggered!**\n✅ ETH has moved above ${strategy_conditions['breakout_entry']}.")

        if not alert_sent["pullback_triggered"] and strategy_conditions["pullback_low"] <= price <= strategy_conditions["pullback_high"]:
            alert_sent["pullback_triggered"] = True
            await ctx.send(f"📢 **Pullback Buy Zone Reached!**\n✅ ETH entered zone ${strategy_conditions['pullback_low']} – ${strategy_conditions['pullback_high']}.")

        await asyncio.sleep(30)

# === !trade Command ===
@bot.command()
async def trade(ctx):
    global alert_sent, strategy_conditions
    if alert_sent["trade"]:
        await ctx.send("✅ Trade conditions already posted. Use `!reset` to refresh.")
        return

    price = fetch_eth_price()
    if price is None:
        await ctx.send("⚠️ Unable to fetch ETH price.")
        return

    # === Strategy Calculation (Locked) ===
    breakout_entry = round(price * 0.994, 2)
    stop_loss_breakout = round(price * 0.9685, 2)
    tp1_breakout = round(price * 1.0105, 2)
    tp2_breakout = round(price * 1.033, 2)

    buy_zone_low = round(price * 0.9685, 2)
    buy_zone_high = round(price * 0.975, 2)
    stop_loss_pullback = round(price * 0.947, 2)
    tp1_pullback = round(price * 1.01, 2)
    tp2_pullback = round(price * 1.03, 2)

    strategy_conditions = {
        "breakout_entry": breakout_entry,
        "pullback_low": buy_zone_low,
        "pullback_high": buy_zone_high
    }
    alert_sent.update({
        "trade": True,
        "breakout_triggered": False,
        "pullback_triggered": False
    })

    # === ALERT EMBED ONLY (Locked Section) ===
    embed = discord.Embed(
        title="ETH Trade Strategies (On-Demand)",
        color=discord.Color.blue(),
        timestamp=datetime.utcnow()
    )
    embed.add_field(
        name="📈 Breakout Strategy",
        value=(
            f"• Entry: Close above ${breakout_entry}\n"
            f"• Stop Loss: Below ${stop_loss_breakout}\n"
            f"• TP1: ${tp1_breakout}\n"
            f"• TP2: ${tp2_breakout}"
        ),
        inline=False
    )
    embed.add_field(
        name="📉 Pullback Strategy",
        value=(
            f"• Buy Zone: ${buy_zone_low} – ${buy_zone_high}\n"
            f"• Stop Loss: Below ${stop_loss_pullback}\n"
            f"• TP1: ${tp1_pullback}\n"
            f"• TP2: ${tp2_pullback}"
        ),
        inline=False
    )
    embed.set_footer(text="Generated on")
    await ctx.send(embed=embed)

    bot.loop.create_task(monitor_conditions(ctx))

# === !reset Command ===
@bot.command()
async def reset(ctx):
    global alert_sent, strategy_conditions
    alert_sent = {
        "trade": False,
        "breakout_triggered": False,
        "pullback_triggered": False
    }
    strategy_conditions.clear()
    await ctx.send("♻️ Trade alerts have been reset. You can now use `!trade` again.")

# === Start Flask Thread (Locked) ===
flask_thread = threading.Thread(target=run_flask)
flask_thread.start()

# === Run Bot ===
bot.run(os.getenv("DISCORD_TOKEN"))

