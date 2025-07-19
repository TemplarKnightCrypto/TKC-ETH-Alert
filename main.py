import discord
from discord.ext import commands, tasks
import requests
import threading
from flask import Flask
import datetime
import numpy as np
import time

# === CONFIG ===
DISCORD_TOKEN = "YOUR_DISCORD_BOT_TOKEN"
CHANNEL_ID = YOUR_CHANNEL_ID  # Replace with your channel ID
COOLDOWN_SECONDS = 900  # 15 minutes

# === FLASK FOR UPTIME ===
app = Flask(__name__)
@app.route("/")
def home():
    return "Bot is live!"
def run_flask():
    app.run(host="0.0.0.0", port=8000)

threading.Thread(target=run_flask).start()

# === DISCORD BOT SETUP ===
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# === COOLDOWN TRACKING ===
last_alert_time = {}

# === INDICATOR CALCULATION ===
def get_ohlcv(symbol="ETHUSDT", interval="1m", limit=100):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    data = requests.get(url).json()
    closes = [float(candle[4]) for candle in data]
    highs = [float(candle[2]) for candle in data]
    lows = [float(candle[3]) for candle in data]
    return closes, highs, lows

def calculate_indicators():
    closes, highs, lows = get_ohlcv()
    if len(closes) < 50:
        return None

    close = np.array(closes)
    high = np.array(highs)
    low = np.array(lows)

    ema20 = np.mean(close[-20:])
    rsi = compute_rsi(close)
    atr = np.mean(high[-14:] - low[-14:])

    fib_low = np.min(close[-50:])
    fib_high = np.max(close[-50:])
    fib_618 = fib_high - (fib_high - fib_low) * 0.618
    fib_382 = fib_high - (fib_high - fib_low) * 0.382

    trend_up = close[-1] > ema20
    trend_down = close[-1] < ema20

    return {
        "price": close[-1],
        "ema": ema20,
        "rsi": rsi,
        "atr": atr,
        "fib_618": fib_618,
        "fib_382": fib_382,
        "trend_up": trend_up,
        "trend_down": trend_down,
    }

def compute_rsi(data, period=14):
    delta = np.diff(data)
    up = delta.clip(min=0)
    down = -1 * delta.clip(max=0)
    ma_up = np.mean(up[-period:])
    ma_down = np.mean(down[-period:])
    rs = ma_up / ma_down if ma_down != 0 else 0
    return 100 - (100 / (1 + rs))

# === STRATEGY LOGIC ===
def check_signals(ind):
    signals = []

    # Breakout Long
    if ind["trend_up"] and ind["rsi"] > 55:
        entry = ind["price"]
        sl = entry - 1.5 * ind["atr"]
        tp = entry + 2.0 * ind["atr"]
        signals.append(("Breakout Long", entry, sl, tp))

    # Pullback Long
    if ind["trend_up"] and ind["price"] >= ind["fib_618"] and ind["price"] <= ind["fib_382"] and ind["rsi"] < 50:
        entry = ind["price"]
        sl = entry - 1.5 * ind["atr"]
        tp = entry + 2.0 * ind["atr"]
        signals.append(("Pullback Long", entry, sl, tp))

    # Breakdown Short
    if ind["trend_down"] and ind["rsi"] < 45:
        entry = ind["price"]
        sl = entry + 1.5 * ind["atr"]
        tp = entry - 2.0 * ind["atr"]
        signals.append(("Breakdown Short", entry, sl, tp))

    # Pullback Short
    if ind["trend_down"] and ind["price"] <= ind["fib_382"] and ind["price"] >= ind["fib_618"] and ind["rsi"] > 50:
        entry = ind["price"]
        sl = entry + 1.25 * ind["atr"]
        tp = entry - 2.0 * ind["atr"]
        signals.append(("Pullback Short", entry, sl, tp))

    return signals

# === ALERT COOLDOWN ===
def should_alert(signal_key):
    now = time.time()
    if signal_key not in last_alert_time or now - last_alert_time[signal_key] > COOLDOWN_SECONDS:
        last_alert_time[signal_key] = now
        return True
    return False

# === DISCORD ALERT ===
async def send_alert(signal_name, entry, sl, tp):
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        print("Channel not found.")
        return

    embed = discord.Embed(
        title=f"📢 ETH Trade Alert: {signal_name}",
        description=f"📈 **Entry**: ${entry:,.2f}\n🛑 **Stop Loss**: ${sl:,.2f}\n💰 **Take Profit**: ${tp:,.2f}",
        color=discord.Color.green() if "Long" in signal_name else discord.Color.red(),
        timestamp=datetime.datetime.utcnow()
    )
    await channel.send(embed=embed)

# === TP/SL WATCHER ===
active_trades = []

def update_active_trades(price):
    to_remove = []
    for trade in active_trades:
        name, entry, sl, tp = trade
        if ("Long" in name and (price <= sl or price >= tp)) or ("Short" in name and (price >= sl or price <= tp)):
            hit = "🛑 SL HIT" if (("Long" in name and price <= sl) or ("Short" in name and price >= sl)) else "💰 TP HIT"
            alert = f"{hit} for {name} | Price: ${price:,.2f}"
            bot.loop.create_task(send_simple_alert(alert))
            to_remove.append(trade)
    for trade in to_remove:
        active_trades.remove(trade)

async def send_simple_alert(text):
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send(f"**{text}**")

# === MONITOR TASK ===
@tasks.loop(seconds=60)
async def monitor_eth():
    ind = calculate_indicators()
    if not ind:
        return

    price = ind["price"]
    update_active_trades(price)

    signals = check_signals(ind)
    for name, entry, sl, tp in signals:
        if should_alert(name):
            await send_alert(name, entry, sl, tp)
            active_trades.append((name, entry, sl, tp))

# === BOT COMMANDS ===
@bot.event
async def on_ready():
    print(f"{bot.user} is live.")
    monitor_eth.start()

@bot.command()
async def trade(ctx):
    ind = calculate_indicators()
    if not ind:
        await ctx.send("Couldn't fetch indicators.")
        return

    signals = check_signals(ind)
    if not signals:
        await ctx.send("No trade signals at this time.")
        return

    for name, entry, sl, tp in signals:
        await send_alert(name, entry, sl, tp)
        active_trades.append((name, entry, sl, tp))

# === START BOT ===
bot.run(DISCORD_TOKEN)
