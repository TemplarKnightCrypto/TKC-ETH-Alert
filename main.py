import os
import discord
import requests
import datetime
import pandas as pd
import numpy as np
from discord.ext import commands, tasks
from flask import Flask
import threading
from ta.trend import ema_indicator, macd_diff
from ta.momentum import rsi, stochrsi
from ta.volatility import bollinger_hband, bollinger_lband, average_true_range
from ta.volume import on_balance_volume

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
alert_channel_id = None

# === FLASK SETUP TO KEEP ALIVE ===
app = Flask('')
@app.route('/')
def home():
    return "Bot is running!"
def run():
    app.run(host='0.0.0.0', port=8080)
threading.Thread(target=run).start()

# === DATA FETCHING ===
def get_eth_data():
    url = 'https://api.binance.com/api/v3/klines?symbol=ETHUSDT&interval=5m&limit=200'
    response = requests.get(url)
    data = response.json()
    df = pd.DataFrame(data, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'num_trades',
        'taker_buy_base_volume', 'taker_buy_quote_volume', 'ignore'])

    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df['close'] = df['close'].astype(float)
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    df['volume'] = df['volume'].astype(float)

    df['ema50'] = ema_indicator(df['close'], window=50)
    df['rsi'] = rsi(df['close'], window=14)
    df['macd'] = df['close'].ewm(span=12).mean() - df['close'].ewm(span=26).mean()
    df['macd_signal'] = df['macd'].ewm(span=9).mean()
    df['obv'] = on_balance_volume(df['close'], df['volume'])
    df['atr'] = average_true_range(df['high'], df['low'], df['close'], window=14)
    df['vwap'] = (df['volume'] * (df['high'] + df['low'] + df['close']) / 3).cumsum() / df['volume'].cumsum()
    df['stoch_rsi'] = stochrsi(df['close'], window=14, smooth1=3, smooth2=3)
    df['bb_upper'] = bollinger_hband(df['close'], window=20, window_dev=2)
    df['bb_lower'] = bollinger_lband(df['close'], window=20, window_dev=2)
    df['donchian_low'] = df['low'].rolling(window=20).min()
    df['donchian_high'] = df['high'].rolling(window=20).max()
    return df

# === TRADE MONITORING ===
@tasks.loop(minutes=5)
async def monitor_price():
    global alert_channel_id
    if not alert_channel_id:
        return
    try:
        df = get_eth_data()
        current_price = df['close'].iloc[-1]
        ema50 = df['ema50'].iloc[-1]
        rsi_val = df['rsi'].iloc[-1]
        macd = df['macd'].iloc[-1]
        macd_signal_val = df['macd_signal'].iloc[-1]
        obv_slope = df['obv'].iloc[-1] - df['obv'].iloc[-5]
        atr_now = df['atr'].iloc[-1]
        vwap_val = df['vwap'].iloc[-1]
        stoch_rsi_val = df['stoch_rsi'].iloc[-1]
        bb_upper = df['bb_upper'].iloc[-1]
        bb_lower = df['bb_lower'].iloc[-1]
        donchian_low = df['donchian_low'].iloc[-1]
        donchian_high = df['donchian_high'].iloc[-1]

        message = None

        # Bullish Scenario
        if (
            current_price > ema50 and
            current_price > vwap_val and
            macd > macd_signal_val and
            obv_slope > 0 and
            rsi_val > 50 and
            stoch_rsi_val > 0.8 and
            current_price > bb_upper
        ):
            message = f"🚀 **ETH Bullish Breakout Detected**\nPrice: ${current_price:,.2f}"

        # Bearish Scenario
        elif (
            current_price < ema50 and
            rsi_val < 40 and
            macd < macd_signal_val and
            obv_slope < 0 and
            current_price < donchian_low or current_price < bb_lower and
            atr_now > df['atr'].iloc[-5]
        ):
            message = f"⚠️ **ETH Bearish Breakdown Detected**\nPrice: ${current_price:,.2f}"

        if message:
            channel = bot.get_channel(alert_channel_id)
            await channel.send(message)

    except Exception as e:
        print("Monitoring error:", e)

# === STATUS UPDATE ===
@tasks.loop(minutes=30)
async def status_update():
    global alert_channel_id
    if not alert_channel_id:
        return
    try:
        df = get_eth_data()
        current_price = df['close'].iloc[-1]
        ema50 = df['ema50'].iloc[-1]
        rsi_val = df['rsi'].iloc[-1]
        macd = df['macd'].iloc[-1]
        macd_signal_val = df['macd_signal'].iloc[-1]
        obv_slope = df['obv'].iloc[-1] - df['obv'].iloc[-5]
        atr_now = df['atr'].iloc[-1]
        vwap_val = df['vwap'].iloc[-1]
        stoch_rsi_val = df['stoch_rsi'].iloc[-1]
        bb_upper = df['bb_upper'].iloc[-1]
        bb_lower = df['bb_lower'].iloc[-1]
        donchian_low = df['donchian_low'].iloc[-1]
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        channel = bot.get_channel(alert_channel_id)
        await channel.send(f"""📊 **ETH Market Status Update**\n🕒 {now}

💰 **Price:** ${current_price:,.2f}
📈 EMA50: ${ema50:,.2f}
📉 VWAP: ${vwap_val:,.2f}
📊 RSI: {rsi_val:.2f}
📶 MACD: {macd:.4f} | Signal: {macd_signal_val:.4f}
📈 OBV Trend: {'Up' if obv_slope > 0 else 'Down'}
🔄 Stoch RSI: {stoch_rsi_val:.2f}
📊 Donchian Low: ${donchian_low:.2f}
📏 ATR: {atr_now:.2f}

⚠️ *This is not a trade alert — just a current strategy status update.*
""")
    except Exception as e:
        print("Status update error:", e)

# === BOT EVENTS ===
@bot.event
async def on_ready():
    print(f"✅ Bot is online as {bot.user}")
    monitor_price.start()
    status_update.start()

@bot.command()
async def setchannel(ctx):
    global alert_channel_id
    alert_channel_id = ctx.channel.id
    await ctx.send(f"✅ Alerts will be sent to this channel.")

bot.run(os.getenv("DISCORD_BOT_TOKEN"))
