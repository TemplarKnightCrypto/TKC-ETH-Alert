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
last_trade_hash = None
alert_channel_id = None
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
    eth_scan_30min.start()

@tasks.loop(minutes=30)
async def eth_scan_30min():
    global last_trade_hash
    df = get_eth_data()
    if df is not None:
        trade = detect_breakout_trade(df) or detect_pullback_trade(df) or detect_short_trade(df) or detect_camarilla_trade(df)
        channel = bot.get_channel(alert_channel_id)
        if channel:
            if trade:
                trade_hash = hash(frozenset(trade.items()))
                if trade_hash != last_trade_hash:
                    last_trade_hash = trade_hash
                    trade['confidence'] = trade_confidence_score(df, trade)
                    await channel.send(format_trade_alert(trade))
                else:
                    print("Duplicate trade detected, skipping alert.")
            await channel.send(format_alerts(df))
            await channel.send(alligator_water_status(df))
            await channel.send(ichimoku_cloud_status(df))
        else:
            print("⚠️ Alert channel is not set. Run !setchannel in Discord.")

@bot.command()
async def setchannel(ctx):
    global alert_channel_id
    alert_channel_id = ctx.channel.id
    await ctx.send("✅ Alerts will be sent to this channel.")

@bot.command()
async def cloud(ctx):
    df = get_eth_data()
    if df is not None:
        await ctx.send(ichimoku_cloud_status(df))
    else:
        await ctx.send("⚠️ Could not fetch ETH data.")

@bot.command()
async def alligator(ctx):
    df = get_eth_data()
    if df is not None:
        await ctx.send(alligator_water_status(df))
    else:
        await ctx.send("⚠️ Could not fetch ETH data.")

def ichimoku_cloud_status(df):
    current = df.iloc[-1]
    previous = df.iloc[-2]

    current_cloud = '🟢 Green Cloud' if current['ichimoku_bullish'] else '🔴 Red Cloud' if current['ichimoku_bearish'] else '⚪ Neutral'
    previous_cloud = '🟢 Green Cloud' if previous['ichimoku_bullish'] else '🔴 Red Cloud' if previous['ichimoku_bearish'] else '⚪ Neutral'

    if current_cloud != previous_cloud:
        return f"☁️ Ichimoku Cloud switched from {previous_cloud} to {current_cloud}"
    else:
        return f"☁️ Ichimoku Cloud is still {current_cloud}"

def alligator_water_status(df):
    latest = df.iloc[-1]
    if latest['alligator_bullish']:
        return "🐊 Alligator is **above water** (bullish)."
    elif latest['alligator_bearish']:
        return "🐊 Alligator is **below water** (bearish)."
    else:
        return "🐊 Alligator is **neutral** (closed jaws or indecision)."

def get_eth_data(interval='5', limit=200):
    try:
        url = f"https://api.kraken.com/0/public/OHLC?pair=ETHUSD&interval={interval}"
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json().get("result", {})
            key = next(k for k in data if k != 'last')
            candles = data[key]
            df = pd.DataFrame(candles, columns=["time", "open", "high", "low", "close", "vwap", "volume", "count"])
            df['time'] = pd.to_datetime(df['time'].astype(float), unit='s', utc=True)
            df[['open','high','low','close','volume']] = df[['open','high','low','close','volume']].astype(float)
            df = df[['time','open','high','low','close','volume']]
            return apply_indicators(df)
    except Exception as e:
        print("Error fetching data:", e)
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

def detect_breakout_trade(df):
    latest = df.iloc[-1]
    if (
        latest['close'] > latest['ema50']
        and latest['macd'] > latest['signal']
        and latest['rsi'] > 50
        and latest['supertrend_bull']
    ):
        return {
            'type': 'Breakout Long',
            'entry': latest['close'],
            'stop': latest['low'] - latest['atr'],
            'tp1': latest['close'] + latest['atr'] * 2,
            'tp2': latest['close'] + latest['atr'] * 4
        }
    return None

def detect_pullback_trade(df):
    latest = df.iloc[-1]
    if (
        latest['ema_cross_up']
        and latest['rsi_oversold']
        and latest['macd'] > latest['signal']
        and latest['stochrsi_cross_up']
    ):
        return {
            'type': 'Pullback Long',
            'entry': latest['close'],
            'stop': latest['low'] - latest['atr'],
            'tp1': latest['close'] + latest['atr'] * 1.5,
            'tp2': latest['close'] + latest['atr'] * 3
        }
    return None

def detect_short_trade(df):
    latest = df.iloc[-1]
    if (
        latest['close'] < latest['ema50']
        and latest['macd'] < latest['signal']
        and latest['rsi'] < 50
        and latest['supertrend_bear']
    ):
        return {
            'type': 'Breakdown Short',
            'entry': latest['close'],
            'stop': latest['high'] + latest['atr'],
            'tp1': latest['close'] - latest['atr'] * 2,
            'tp2': latest['close'] - latest['atr'] * 4
        }
    return None

def detect_camarilla_trade(df):
    latest = df.iloc[-1]
    h3 = latest['high'] * 1.015
    l3 = latest['low'] * 0.985
    if latest['close'] > h3:
        return {
            'type': 'Camarilla Breakout Long',
            'entry': latest['close'],
            'stop': h3 - latest['atr'],
            'tp1': latest['close'] + latest['atr'] * 2,
            'tp2': latest['close'] + latest['atr'] * 3
        }
    elif latest['close'] < l3:
        return {
            'type': 'Camarilla Breakdown Short',
            'entry': latest['close'],
            'stop': l3 + latest['atr'],
            'tp1': latest['close'] - latest['atr'] * 2,
            'tp2': latest['close'] - latest['atr'] * 3
        }
    return None

def trade_confidence_score(df, trade):
    latest = df.iloc[-1]
    score = 0

    # EMA trend confirmation
    if (trade['type'].endswith('Long') and latest['ema_cross_up']) or (trade['type'].endswith('Short') and latest['ema_cross_down']):
        score += 1

    # MACD direction
    if (trade['type'].endswith('Long') and latest['macd'] > latest['signal']) or (trade['type'].endswith('Short') and latest['macd'] < latest['signal']):
        score += 1

    # RSI alignment
    if (trade['type'].endswith('Long') and latest['rsi'] > 50) or (trade['type'].endswith('Short') and latest['rsi'] < 50):
        score += 1

    # Supertrend support
    if (trade['type'].endswith('Long') and latest['supertrend_bull']) or (trade['type'].endswith('Short') and latest['supertrend_bear']):
        score += 1

    # Volume confirmation
    if latest['volume_spike']:
        score += 1

    stars = '⭐' * score + '✩' * (5 - score)
    percentage = f"{int(score / 5 * 100)}%"
    return f"{percentage} {stars}"

def format_trade_alert(trade):
    confidence_str = trade.get('confidence', 'N/A')
    percent = confidence_str.split()[0].replace('%','')
    if percent.isdigit() and int(percent) < 60:
        return None  # Do not trigger alert below 60% confidence

    direction_emoji = "🟢" if trade['type'].endswith("Long") else "🔴"
    msg = f"""
🚨 **{direction_emoji} {trade['type']} Trade Alert**

💰 Entry: ${trade['entry']:.2f}
📉 Stop Loss: ${trade['stop']:.2f}
🎯 Take Profit 1: ${trade['tp1']:.2f}
🎯 Take Profit 2: ${trade['tp2']:.2f}

📊 Confidence Score: {confidence_str}
    """
    return msg

def format_alerts(df):
    latest = df.iloc[-1]
    previous = df.iloc[-2]
    direction = "⬆️" if latest['close'] > previous['close'] else "⬇️" if latest['close'] < previous['close'] else "➖"
    change_pct = ((latest['close'] - previous['close']) / previous['close']) * 100
    timestamp = latest['time'].astimezone(CENTRAL_TZ).strftime('%Y-%m-%d %I:%M %p')

    msg = f"""
📊 ETH Strategy Status {direction} ({change_pct:+.2f}%) at {timestamp}

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


if __name__ == "__main__":
    bot.run(os.getenv("DISCORD_BOT_TOKEN"))

