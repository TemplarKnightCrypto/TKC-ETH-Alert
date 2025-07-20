import os
import discord
import requests
import datetime
import pytz
import pandas as pd
import numpy as np
from discord.ext import commands, tasks
from flask import Flask
import threading
from ta.trend import ema_indicator
from ta.momentum import rsi, stochrsi, tsi
from ta.volatility import average_true_range
from ta.volume import on_balance_volume
from dotenv import load_dotenv

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
alert_channel_id = None
ALERT_SENSITIVITY = 3
CENTRAL_TZ = pytz.timezone("US/Central")

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is live!"

def run_flask():
    app.run(host="0.0.0.0", port=8000)

threading.Thread(target=run_flask).start()

@bot.event
async def on_ready():
    print(f"✅ Bot is online as {bot.user}")
    daily_eth_report.start()

@tasks.loop(minutes=60)
async def daily_eth_report():
    now = datetime.datetime.now(CENTRAL_TZ)
    if now.hour == 7:
        channel = bot.get_channel(alert_channel_id)
        df = get_eth_data()
        if df is not None:
            await channel.send(format_alerts(df))
            await channel.send(generate_summary(df, "Daily"))

@bot.command()
async def setchannel(ctx):
    global alert_channel_id
    alert_channel_id = ctx.channel.id
    await ctx.send("✅ Alerts will be sent to this channel.")

@bot.command()
async def price(ctx):
    df = get_eth_data()
    if df is not None:
        await ctx.send(format_alerts(df))
    else:
        await ctx.send("⚠️ Could not fetch ETH data.")

@bot.command()
async def status(ctx):
    df = get_eth_data()
    if df is not None:
        await ctx.send(format_alerts(df))
    else:
        await ctx.send("⚠️ Could not fetch ETH data.")

@bot.command()
async def summary(ctx, timeframe="daily"):
    df = get_eth_data()
    if df is not None:
        await ctx.send(generate_summary(df, timeframe.capitalize()))
    else:
        await ctx.send("⚠️ Could not fetch ETH data.")

@bot.command()
async def commands(ctx):
    await ctx.send("""
📘 **Available Commands**:
!setchannel — Set this channel for alerts
!price — Current ETH strategy snapshot
!status — Full strategy indicator report
!summary [daily|weekly] — Summary report
!ethmoves — 1% move tracker
!sensitivity [1–5] — Adjust alert sensitivity
    """)

@bot.command()
async def sensitivity(ctx, level: int):
    global ALERT_SENSITIVITY
    if 1 <= level <= 5:
        ALERT_SENSITIVITY = level
        await ctx.send(f"🔧 Sensitivity set to level {level}.")
    else:
        await ctx.send("⚠️ Please choose a level between 1 (high sensitivity) and 5 (low sensitivity).")

@bot.command()
async def ethmoves(ctx):
    df = get_eth_data(interval='240', limit=42)  # 4hr interval
    if df is None:
        await ctx.send("⚠️ Could not fetch ETH data.")
        return
    moves = []
    for i in range(len(df)):
        entry_price = df.iloc[i]['close']
        entry_time = df.iloc[i]['time'].astimezone(CENTRAL_TZ)
        target_up = entry_price * 1.01
        target_down = entry_price * 0.99
        for j in range(i+1, len(df)):
            high = df.iloc[j]['high']
            low = df.iloc[j]['low']
            exit_time = df.iloc[j]['time'].astimezone(CENTRAL_TZ)
            if high >= target_up:
                moves.append((entry_time, entry_price, exit_time, high, "Up"))
                break
            elif low <= target_down:
                moves.append((entry_time, entry_price, exit_time, low, "Down"))
                break
    if not moves:
        await ctx.send("📉 No 1% ETH moves detected in the last 24 hours.")
        return
    message = "**📊 ETH 1% Move Summary (4hr candles)**\n"
    for m in moves:
        entry_time, entry_price, exit_time, exit_price, direction = m
        message += (
            f"\n🔹 Direction: {direction}\n"
            f"• Entry: ${entry_price:.2f} at {entry_time.strftime('%b %d %I:%M %p')}\n"
            f"• Exit: ${exit_price:.2f} at {exit_time.strftime('%b %d %I:%M %p')}\n"
            f"• Δ: {exit_price - entry_price:+.2f} ({(exit_price - entry_price) / entry_price * 100:.2f}%)\n"
        )
    await ctx.send(message)

def get_eth_data(interval='5', limit=200):
    try:
        url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol=ETHUSDT&interval={interval}&limit={limit}"
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json().get("result", {}).get("list", [])
            if data:
                df = pd.DataFrame(data, columns=["time", "open", "high", "low", "close", "volume", "turnover"])
                df['time'] = pd.to_datetime(df['time'].astype(float), unit='s')
                df[['open','high','low','close','volume']] = df[['open','high','low','close','volume']].astype(float)
                df = df[['time','open','high','low','close','volume']]
                return apply_indicators(df)
            else:
                print("⚠️ Bybit returned empty data.")
        else:
            print(f"❌ Bybit API Error {response.status_code}: {response.text}")

        # FALLBACK: Kraken
        symbol_map = {'ETHUSDT': 'XETHZUSD'}
        pair = symbol_map.get('ETHUSDT', 'XETHZUSD')
        url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={interval}"
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json().get("result", {})
            key = next(k for k in data if k != 'last')
            candles = data[key]
            df = pd.DataFrame(candles, columns=["time", "open", "high", "low", "close", "vwap", "volume", "count"])
            df['time'] = pd.to_datetime(df['time'].astype(float), unit='s')
            df[['open','high','low','close','volume']] = df[['open','high','low','close','volume']].astype(float)
            df = df[['time','open','high','low','close','volume']]
            return apply_indicators(df)
        else:
            print(f"❌ Kraken API Error {response.status_code}: {response.text}")

    except Exception as e:
        print("❌ Exception while fetching ETH data:", e)

    return None

def apply_indicators(df):
    df['ema50'] = ema_indicator(df['close'], window=50)
    df['rsi'] = rsi(df['close'], window=14)
    df['stochrsi'] = stochrsi(df['close'], window=14)
    df['tsi'] = tsi(df['close'])
    df['obv'] = on_balance_volume(df['close'], df['volume'])
    df['atr'] = average_true_range(df['high'], df['low'], df['close'], window=14)
    exp1 = df['close'].ewm(span=12, adjust=False).mean()
    exp2 = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = exp1 - exp2
    df['signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['signal']
    df['macd_hist_flip'] = df['macd_hist'].diff().apply(lambda x: x > 0)
    df['rsi_overbought'] = df['rsi'] > 70
    df['rsi_oversold'] = df['rsi'] < 30
    df['ema_cross_up'] = df['close'] > df['ema50']
    df['ema_cross_down'] = df['close'] < df['ema50']
    df['volume_spike'] = df['volume'] > df['volume'].rolling(20).mean() * 1.5
    df['stochrsi_cross_up'] = df['stochrsi'].diff() > 0.1
    df['stochrsi_cross_down'] = df['stochrsi'].diff() < -0.1
    df['tsi_bullish'] = df['tsi'] > 0
    df['tsi_bearish'] = df['tsi'] < 0
    df['supertrend_bull'] = df['close'] > df['high'].rolling(10).mean()
    df['supertrend_bear'] = df['close'] < df['low'].rolling(10).mean()
    df['jaw'] = df['close'].rolling(13).mean()
    df['teeth'] = df['close'].rolling(8).mean()
    df['lips'] = df['close'].rolling(5).mean()
    df['alligator_bullish'] = (df['lips'] > df['teeth']) & (df['teeth'] > df['jaw'])
    df['alligator_bearish'] = (df['lips'] < df['teeth']) & (df['teeth'] < df['jaw'])
    period9_high = df['high'].rolling(window=9).max()
    period9_low = df['low'].rolling(window=9).min()
    tenkan_sen = (period9_high + period9_low) / 2
    period26_high = df['high'].rolling(window=26).max()
    period26_low = df['low'].rolling(window=26).min()
    kijun_sen = (period26_high + period26_low) / 2
    senkou_span_a = ((tenkan_sen + kijun_sen) / 2).shift(26)
    period52_high = df['high'].rolling(52).max()
    period52_low = df['low'].rolling(52).min()
    senkou_span_b = ((period52_high + period52_low) / 2).shift(26)
    df['ichimoku_bullish'] = (df['close'] > senkou_span_a) & (df['close'] > senkou_span_b)
    df['ichimoku_bearish'] = (df['close'] < senkou_span_a) & (df['close'] < senkou_span_b)
    df['ichimoku_twist'] = (senkou_span_a - senkou_span_b).abs().diff().rolling(2).mean() < 1e-3
    return df

def format_alerts(df):
    latest = df.iloc[-1]
    msg = f"""
📊 **ETH Strategy Status**

💰 Price: ${latest['close']:,.2f}
📈 RSI: {latest['rsi']:.2f}
📉 MACD: {latest['macd']:.4f} | Signal: {latest['signal']:.4f}
📊 Stoch RSI: {latest['stochrsi']:.2f}
📊 EMA50: ${latest['ema50']:,.2f}
📶 OBV: {'📈 Bullish' if latest['tsi_bullish'] else '📉 Bearish'}

🧠 Supertrend: {'🟢 Bullish' if latest['supertrend_bull'] else '🔴 Bearish' if latest['supertrend_bear'] else '⚪ Neutral'}
🐊 Alligator: {'🟢 Bullish' if latest['alligator_bullish'] else '🔴 Bearish' if latest['alligator_bearish'] else '⚪ Neutral'}
☁️ Ichimoku: {'🟢 Bullish' if latest['ichimoku_bullish'] else '🔴 Bearish' if latest['ichimoku_bearish'] else '⚪ Neutral'}
🌪️ Twist Alert: {'⚠️ Twist detected' if latest['ichimoku_twist'] else '✅ Stable'}

Market Bias: {'🟢 Bullish' if latest['ema_cross_up'] else '🔴 Bearish'}
"""
    return msg

def generate_summary(df, timeframe='Daily'):
    latest = df.iloc[-1]
    highest = df['high'].max()
    lowest = df['low'].min()
    msg = f"\n**{timeframe} ETH Summary**\nClose: ${latest['close']:.2f}\nHigh: ${highest:.2f}\nLow: ${lowest:.2f}\n"
    msg += f"Support: ${lowest:.2f}\nResistance: ${highest:.2f}\n"
    msg += f"RSI: {latest['rsi']:.2f}, MACD: {latest['macd']:.2f}\n"
    return msg

if __name__ == "__main__":
    bot.run(os.getenv("DISCORD_BOT_TOKEN"))
