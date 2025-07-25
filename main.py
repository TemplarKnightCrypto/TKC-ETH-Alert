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

# Load environment variables
dotenv_path = os.getenv("DOTENV_PATH", None)
if dotenv_path:
    load_dotenv(dotenv_path)
else:
    load_dotenv()

# Discord & timezone setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Global state
last_trade_hash = None
alert_channel_id = None       # For trade alerts
status_channel_id = None      # For 30-minute status reports

# Timezone for timestamps
CENTRAL_TZ = pytz.timezone("US/Central")

# Flask app for uptime
app = Flask(__name__)
@app.route('/')
def home():
    return "Bot is live!"

def run_flask():
    app.run(host="0.0.0.0", port=8000)

threading.Thread(target=run_flask).start()

# On ready: start the 30-minute scanner
@bot.event
async def on_ready():
    print(f"✅ Bot is online as {bot.user}")
    eth_scan_30min.start()

# 30-minute loop: send trade alerts and status updates separately
@tasks.loop(minutes=30)
async def eth_scan_30min():
    global last_trade_hash
    try:
        df = get_eth_data()
        if df is None:
            print("Error fetching data, skipping 30m scan.")
            return

        # Detect potential trades
        trade = detect_breakout_trade(df) or detect_pullback_trade(df) or detect_short_trade(df)

        # Resolve channels
        trade_ch = bot.get_channel(alert_channel_id)
        status_ch = bot.get_channel(status_channel_id)

        # Send trade alerts
        if trade and trade_ch:
            trade_hash = hash(frozenset(trade.items()))
            if trade_hash != last_trade_hash:
                last_trade_hash = trade_hash
                trade['confidence'] = trade_confidence_score(df, trade)
                await trade_ch.send(format_trade_alert(trade))
            else:
                print("Duplicate trade detected, skipping alert.")

        # Send status update
        if status_ch:
            await status_ch.send(format_alerts(df))
        else:
            print("⚠️ Status channel not set. Use !setstatuschannel.")

    except Exception as e:
        print(f"[eth_scan_30min] Caught exception, continuing: {e}")

@eth_scan_30min.error
async def eth_scan_error(error):
    print(f"[eth_scan_30min] Task error handler caught: {error}")

# Command: set trade alert channel
default_doc = "Call this in the channel where you want trade alerts."
@bot.command()
async def setchannel(ctx):
    global alert_channel_id
    alert_channel_id = ctx.channel.id
    await ctx.send("✅ Trade alerts will be sent here.")

# Command: set status report channel
@bot.command()
async def setstatuschannel(ctx):
    global status_channel_id
    status_channel_id = ctx.channel.id
    await ctx.send("✅ 30‑minute status reports will be sent here.")

# On-demand trade command\@bot.command()
async def trade(ctx):
    df = get_eth_data()
    if df is not None:
        trade = detect_breakout_trade(df) or detect_pullback_trade(df) or detect_short_trade(df)
        if trade:
            await ctx.send(format_trade_alert(trade))
        else:
            await ctx.send("🕵️‍♂️ No active trade setup at this moment.")
    else:
        await ctx.send("⚠️ Could not fetch ETH data.")

# On-demand price/status command\@bot.command()
async def price(ctx):
    df = get_eth_data()
    if df is not None:
        await ctx.send(format_alerts(df))
    else:
        await ctx.send("⚠️ Could not fetch ETH data.")

# Trade detection functions
def detect_breakout_trade(df):
    latest = df.iloc[-1]
    resistance = df['high'].rolling(20).max().iloc[-2]
    if latest['close'] > resistance and latest['macd_hist_flip'] and latest['volume_spike']:
        return {
            "type": "Breakout Long",
            "entry": latest['close'],
            "stop": latest['close'] - latest['atr'],
            "tp1": latest['close'] + latest['atr'] * 1.5,
            "tp2": latest['close'] + latest['atr'] * 2.5,
        }
    return None

def detect_pullback_trade(df):
    latest = df.iloc[-1]
    support = df['low'].rolling(20).min().iloc[-2]
    if support < latest['close'] < support + latest['atr'] and latest['rsi'] < 40:
        return {
            "type": "Pullback Long",
            "entry": latest['close'],
            "stop": latest['close'] - latest['atr'],
            "tp1": latest['close'] + latest['atr'] * 1.5,
            "tp2": latest['close'] + latest['atr'] * 2.5,
        }
    return None

def detect_short_trade(df):
    latest = df.iloc[-1]
    support = df['low'].rolling(20).min().iloc[-2]
    if latest['close'] < support and not latest['macd_hist_flip'] and latest['volume_spike']:
        return {
            "type": "Breakdown Short",
            "entry": latest['close'],
            "stop": latest['close'] + latest['atr'],
            "tp1": latest['close'] - latest['atr'] * 1.5,
            "tp2": latest['close'] - latest['atr'] * 2.5,
        }
    return None

def trade_confidence_score(df, trade):
    score = 0
    latest = df.iloc[-1]
    if latest['supertrend_bull'] and 'Long' in trade['type']: score += 1
    if latest['supertrend_bear'] and 'Short' in trade['type']: score += 1
    if latest['alligator_bullish'] and 'Long' in trade['type']: score += 1
    if latest['alligator_bearish'] and 'Short' in trade['type']: score += 1
    if latest['ichimoku_bullish'] and 'Long' in trade['type']: score += 1
    if latest['ichimoku_bearish'] and 'Short' in trade['type']: score += 1
    return score

def format_trade_alert(trade):
    rr = abs((trade['tp1'] - trade['entry']) / (trade['entry'] - trade['stop']))
    return f"""
🚨 **{trade['type']} Trade Alert – ETH/USDT**

💥 Entry: ${trade['entry']:,.2f}
🛑 Stop Loss: ${trade['stop']:,.2f}
🎯 Take Profit 1: ${trade['tp1']:,.2f}
🎯 Take Profit 2: ${trade['tp2']:,.2f}

⚖️ Risk/Reward: {rr:.2f}x
📊 Confidence Score: {trade.get('confidence', 0)}/6
"""

def get_eth_data(interval='5', limit=200):
    try:
        symbol_map = {'ETHUSDT': 'XETHZUSD'}
        pair = symbol_map.get('ETHUSDT', 'XETHZUSD')
        url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={interval}"
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

def format_alerts(df):
    latest = df.iloc[-1]
    prev_close = df['close'].iloc[-2]
    price_change = latest['close'] - prev_close
    price_pct = (price_change / prev_close) * 100
    timestamp = latest['time'].astimezone(CENTRAL_TZ).strftime('%Y-%m-%d %I:%M %p')

    if price_change > 0:
        direction_emoji = '⬆️'
    elif price_change < 0:
        direction_emoji = '⬇️'
    else:
        direction_emoji = '➖'

    msg = f"""
📊 **ETH Strategy Status** {direction_emoji} ({price_pct:+.2f}%) at {timestamp}

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

@bot.command()
async def cloud(ctx):
    df = get_eth_data()
    if df is not None:
        current = df.iloc[-1]
        previous = df.iloc[-2]

        current_cloud = '🟢 Green Cloud' if current['ichimoku_bullish'] else '🔴 Red Cloud' if current['ichimoku_bearish'] else '⚪ Neutral'
        previous_cloud = '🟢 Green Cloud' if previous['ichimoku_bullish'] else '🔴 Red Cloud' if previous['ichimoku_bearish'] else '⚪ Neutral'

        if current_cloud != previous_cloud:
            msg = f"☁️ Ichimoku Cloud switched from {previous_cloud} to {current_cloud}"
        else:
            msg = f"☁️ Ichimoku Cloud is still {current_cloud}"

        await ctx.send(msg)
    else:
        await ctx.send("⚠️ Could not fetch ETH data.")

@bot.command()
async def alligator(ctx):
    df = get_eth_data()
    if df is not None:
        latest = df.iloc[-1]
        if latest['alligator_bullish']:
            msg = "🐊 Alligator is **above water** (bullish)."
        elif latest['alligator_bearish']:
            msg = "🐊 Alligator is **below water** (bearish)."
        else:
            msg = "🐊 Alligator is **neutral** (closed jaws or indecision)."
        await ctx.send(msg)
    else:
        await ctx.send("⚠️ Could not fetch ETH data.")

@bot.command()
async def ethmoves(ctx):
    df = get_eth_data(interval='240', limit=42)  # 4hr candles
    if df is None:
        await ctx.send("⚠️ Could not fetch ETH data.")
        return

    up_moves, down_moves = 0, 0
    for i in range(len(df)):
        entry_price = df.iloc[i]['close']
        target_up = entry_price * 1.01
        target_down = entry_price * 0.99
        for j in range(i+1, len(df)):
            high = df.iloc[j]['high']
            low = df.iloc[j]['low']
            if high >= target_up:
                up_moves += 1
                break
            elif low <= target_down:
                down_moves += 1
                break

    total_moves = up_moves + down_moves
    up_pct = (up_moves / total_moves * 100) if total_moves else 0
    down_pct = (down_moves / total_moves * 100) if total_moves else 0

    await ctx.send(f"""
📈 **1% ETH Move Summary (4hr candles)**

🔼 Up Moves: {up_moves} ({up_pct:.1f}%)
🔽 Down Moves: {down_moves} ({down_pct:.1f}%)
📊 Total Moves: {total_moves}
""")

@bot.command()
async def camarilla(ctx):
    df = get_eth_data()
    if df is not None:
        latest = df.iloc[-1]
        h3 = latest['high'] * 1.015
        l3 = latest['low'] * 0.985
        price = latest['close']

        if price > h3:
            status = "📈 Price is above H3 (Breakout zone)"
        elif price < l3:
            status = "📉 Price is below L3 (Breakdown zone)"
        else:
            status = "⏳ Price is between H3 and L3 (Range bound)"

        await ctx.send(f"""
📏 **Camarilla Levels**

H3: ${h3:.2f}
L3: ${l3:.2f}
Price: ${price:.2f}

{status}
""")
    else:
        await ctx.send("⚠️ Could not fetch ETH data.")

if __name__ == "__main__":
    bot.run(os.getenv("DISCORD_BOT_TOKEN"))

