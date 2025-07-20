import os
import discord
import requests
import datetime
import pandas as pd
import numpy as np
from discord.ext import commands, tasks
from ta.trend import ema_indicator, adx, plus_di, minus_di
from ta.momentum import rsi, stochrsi
from ta.volatility import bollinger_hband, bollinger_lband, average_true_range
from ta.volume import on_balance_volume
from dotenv import load_dotenv

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
alert_channel_id = None
last_alert_time = None  # Cooldown tracking
ALERT_COOLDOWN_MINUTES = 30

@bot.event
async def on_ready():
    print(f"✅ Bot is online as {bot.user}")
    monitor_price.start()

@bot.command()
async def setchannel(ctx):
    global alert_channel_id
    alert_channel_id = ctx.channel.id
    await ctx.send("📡 Alerts will be sent to this channel.")

@bot.command()
async def price(ctx):
    df = get_eth_data()
    if df is not None:
        await ctx.send(f"💰 ETH Price: ${df['close'].iloc[-1]:,.2f}")
    else:
        await ctx.send("⚠️ Could not fetch ETH price.")

@bot.command()
async def status(ctx):
    df = get_eth_data()
    if df is not None:
        msg = format_alerts(df)
        await ctx.send(msg if msg else "✅ No major signals currently.")
    else:
        await ctx.send("⚠️ Could not generate status update.")

@bot.command()
async def commands(ctx):
    await ctx.send("""
📜 Available Commands
!setchannel — Set this channel to receive alerts
!price — Show current ETH price
!status — Manual ETH strategy status update
!commands — Show this command list
""")

@tasks.loop(minutes=30)
async def monitor_price():
    global alert_channel_id
    if not alert_channel_id:
        return
    channel = bot.get_channel(alert_channel_id)
    df = get_eth_data()
    if df is not None and not df.empty:
        signal_msg = format_alerts(df)
        if signal_msg:
            await channel.send(signal_msg)

# =============== Data and Signal Logic ===============
def get_eth_data():
    try:
        url = 'https://api.binance.com/api/v3/klines?symbol=ETHUSDT&interval=5m&limit=200'
        response = requests.get(url)
        print(f"Binance API status: {response.status_code}")
        if response.status_code == 200:
            df = pd.DataFrame(response.json(), columns=[
                'time','open','high','low','close','volume','close_time','qav','trades','tb_base','tb_quote','ignore'])
            df['time'] = pd.to_datetime(df['time'], unit='ms')
            df['open'] = df['open'].astype(float)
            df['high'] = df['high'].astype(float)
            df['low'] = df['low'].astype(float)
            df['close'] = df['close'].astype(float)
            df['volume'] = df['volume'].astype(float)
            df = df[['time','open','high','low','close','volume']]
            return apply_indicators(df)
    except Exception as e:
        print("Error fetching data from Binance:", e)
        return None

def apply_indicators(df):
    df['ema50'] = ema_indicator(df['close'], window=50)
    df['rsi'] = rsi(df['close'], window=14)
    df['stochrsi'] = stochrsi(df['close'], window=14)
    df['obv'] = on_balance_volume(df['close'], df['volume'])
    df['atr'] = average_true_range(df['high'], df['low'], df['close'], window=14)

    # MACD manually
    exp1 = df['close'].ewm(span=12, adjust=False).mean()
    exp2 = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = exp1 - exp2
    df['signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['signal']
    df['macd_hist_flip'] = df['macd_hist'].diff().apply(lambda x: x > 0)

    # Trend conditions
    df['rsi_overbought'] = df['rsi'] > 70
    df['rsi_oversold'] = df['rsi'] < 30
    df['ema_cross_up'] = df['close'] > df['ema50']
    df['ema_cross_down'] = df['close'] < df['ema50']
    df['volume_spike'] = df['volume'] > df['volume'].rolling(20).mean() * 1.5

    df['stochrsi_cross_up'] = df['stochrsi'].diff() > 0.1
    df['stochrsi_cross_down'] = df['stochrsi'].diff() < -0.1

    # ADX + DI
    df['adx'] = adx(df['high'], df['low'], df['close'], window=14)
    df['plus_di'] = plus_di(df['high'], df['low'], df['close'], window=14)
    df['minus_di'] = minus_di(df['high'], df['low'], df['close'], window=14)
    df['adx_trending'] = df['adx'] > 20
    df['adx_bullish'] = df['plus_di'] > df['minus_di']
    df['adx_bearish'] = df['minus_di'] > df['plus_di']

    # Supertrend
    df['supertrend_bull'] = df['close'] > df['high'].rolling(10).mean()
    df['supertrend_bear'] = df['close'] < df['low'].rolling(10).mean()

    # Alligator (using SMAs for simplicity)
    df['jaw'] = df['close'].rolling(13).mean()
    df['teeth'] = df['close'].rolling(8).mean()
    df['lips'] = df['close'].rolling(5).mean()
    df['alligator_bullish'] = (df['lips'] > df['teeth']) & (df['teeth'] > df['jaw'])
    df['alligator_bearish'] = (df['lips'] < df['teeth']) & (df['teeth'] < df['jaw'])

    # Ichimoku Cloud simplified
    period9_high = df['high'].rolling(window=9).max()
    period9_low = df['low'].rolling(window=9).min()
    tenkan_sen = (period9_high + period9_low) / 2
    period26_high = df['high'].rolling(window=26).max()
    period26_low = df['low'].rolling(window=26).min()
    kijun_sen = (period26_high + period26_low) / 2
    senkou_span_a = ((tenkan_sen + kijun_sen) / 2).shift(26)
    period52_high = df['high'].rolling(window=52).max()
    period52_low = df['low'].rolling(window=52).min()
    senkou_span_b = ((period52_high + period52_low) / 2).shift(26)
    df['ichimoku_bullish'] = (df['close'] > senkou_span_a) & (df['close'] > senkou_span_b)
    df['ichimoku_bearish'] = (df['close'] < senkou_span_a) & (df['close'] < senkou_span_b)
    df['ichimoku_twist'] = (senkou_span_a - senkou_span_b).abs().diff().rolling(2).mean() < 1e-3

    return df

# Existing format_alerts logic is used here

