# eth_alert_bot with TP1/TP2 alert tracking and !test

import discord
from discord.ext import commands, tasks
import aiohttp
import os
from flask import Flask, jsonify
import threading
import time
from dotenv import load_dotenv

# === Load .env ===
load_dotenv()

# === Flask for Uptime Monitoring ===
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is live!"

@app.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "timestamp": time.time(),
        "alerts_sent": len(triggered_alerts)
    })

def run_flask():
    app.run(host="0.0.0.0", port=8000)

# === Discord Setup ===
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", 0))
LOG_CHANNEL_ID = int(os.getenv("DISCORD_LOG_CHANNEL_ID", 0))

_bot_running = False
current_strategies = {}
triggered_alerts = set()
last_alert_time = {}
alert_cooldown = 300  # seconds
active_trades = {}

# === Price Fetching ===
async def get_price(coin_id: str) -> float:
    try:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as response:
                data = await response.json()
                return data[coin_id]["usd"]
    except Exception as e:
        print(f"❌ Price error: {e}")
        return None

# === Cooldown Logic ===
def can_alert(strategy):
    now = time.time()
    return now - last_alert_time.get(strategy, 0) > alert_cooldown

def mark_alert(strategy):
    last_alert_time[strategy] = time.time()

# === Format Alert Message & Setup Strategies ===
def format_alert(symbol: str, price: float) -> str:
    breakout_entry = round(price * 0.994, 2)
    stop_loss_breakout = round(price * 0.9685, 2)
    tp1_breakout = round(price * 1.0105, 2)
    tp2_breakout = round(price * 1.033, 2)


    buy_zone_low = round(price * 0.9685, 2)
    buy_zone_high = round(price * 0.975, 2)
    stop_loss_pullback = round(price * 0.947, 2)
    tp1_pullback = round(price * 1.01, 2)
    tp2_pullback = round(price * 1.03, 2)

    current_strategies.update({
        "Breakout Long": {
            "entry": breakout_entry,
            "tp1": breakout_tp1,
            "tp2": breakout_tp2
        },
        "Breakdown Short": {
            "entry": breakdown_entry,
            "tp1": breakdown_tp1,
            "tp2": breakdown_tp2
        },
         })

    triggered_alerts.clear()

    return (
        f"**{symbol} Trade Strategies (On-Demand)**\n\n"
        f"📈 Current {symbol} Price: ${price:,.2f}\n\n"
        f"✅ Breakout Long\n• Entry: Above ${breakout_entry:,.2f}\n• Stop: Below ${breakout_stop:,.2f}\n• TP1: ${breakout_tp1:,.2f}\n• TP2: ${breakout_tp2:,.2f}\n\n"
        f"🟩 Pullback Long Zone: ${pullback_zone_low:,.2f} - ${pullback_zone_high:,.2f}\n• Stop: Below ${pullback_stop:,.2f}\n• TP1: ${pullback_tp1:,.2f}\n• TP2: ${pullback_tp2:,.2f}\n\n"
     
    )

# === Bot Events ===
@bot.event
async def on_ready():
    global _bot_running
    if _bot_running:
        return
    _bot_running = True
    print(f"✅ Bot is online as {bot.user}")
    check_conditions.start()

@bot.command()
async def test(ctx):
    await ctx.send("✅ Bot is working!")

@bot.command()
async def trade(ctx):
    user_id = ctx.author.id
    current_time = time.time()

    if not hasattr(bot, '_last_trade_times'):
        bot._last_trade_times = {}

    if user_id in bot._last_trade_times:
        wait = 5 - (current_time - bot._last_trade_times[user_id])
        if wait > 0:
            await ctx.send(f"⏳ Wait {wait:.1f}s before retrying.")
            return

    bot._last_trade_times[user_id] = current_time

    price = await get_price("ethereum")
    if price is None:
        await ctx.send("❌ Failed to fetch price.")
        return

    await ctx.send(format_alert("ETH", price))

# === TP1/TP2 Check Loop ===
@tasks.loop(seconds=30)
async def check_conditions():
    if not CHANNEL_ID or not current_strategies:
        return

    price = await get_price("ethereum")
    if price is None:
        return

    channel = bot.get_channel(CHANNEL_ID)
    log_channel = bot.get_channel(LOG_CHANNEL_ID) if LOG_CHANNEL_ID else None
    if not channel:
        return

    for name, params in current_strategies.items():
        if name not in active_trades and can_alert(name):
            if "entry" in params and (
                ("Long" in name and price > params["entry"]) or
                ("Short" in name and price < params["entry"])
            ):
                await send_alert(channel, log_channel, name, price)
                mark_alert(name)
                active_trades[name] = {
                    "tp1": params["tp1"],
                    "tp2": params["tp2"],
                    "tp1_hit": False,
                    "tp2_hit": False
                }
            elif "zone" in params:
                low, high = params["zone"]
                if low <= price <= high:
                    await send_alert(channel, log_channel, name, price)
                    mark_alert(name)
                    active_trades[name] = {
                        "tp1": params["tp1"],
                        "tp2": params["tp2"],
                        "tp1_hit": False,
                        "tp2_hit": False
                    }

    for name, trade in active_trades.items():
        if "Long" in name:
            if not trade["tp1_hit"] and price >= trade["tp1"]:
                await channel.send(f"🎯 ETH **{name}** TP1 hit at ${price:,.2f}")
                trade["tp1_hit"] = True
            if not trade["tp2_hit"] and price >= trade["tp2"]:
                await channel.send(f"🏁 ETH **{name}** TP2 hit at ${price:,.2f}")
                trade["tp2_hit"] = True
        elif "Short" in name:
            if not trade["tp1_hit"] and price <= trade["tp1"]:
                await channel.send(f"🎯 ETH **{name}** TP1 hit at ${price:,.2f}")
                trade["tp1_hit"] = True
            if not trade["tp2_hit"] and price <= trade["tp2"]:
                await channel.send(f"🏁 ETH **{name}** TP2 hit at ${price:,.2f}")
                trade["tp2_hit"] = True

# === Send Alerts ===
async def send_alert(channel, log_channel, strategy, price):
    await channel.send(f"🚨 @everyone ETH **{strategy}** triggered at ${price:,.2f}")
    if log_channel:
        await log_channel.send(f"🪵 Log: {strategy} triggered at ${price:,.2f}")
    triggered_alerts.add(strategy)

# === Start Bot ===
def main():
    global _bot_running
    if _bot_running:
        return
    threading.Thread(target=run_flask, daemon=True).start()
    time.sleep(2)
    try:
        bot.run(TOKEN)
    except Exception as e:
        print(f"❌ Bot failed: {e}")
        _bot_running = False

if __name__ == "__main__":
    main()