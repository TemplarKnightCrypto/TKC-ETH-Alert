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

# Load .env if available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

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
    def try_binance():
        url = 'https://api.binance.com/api/v3/klines?symbol=ETHUSDT&interval=5m&limit=200'
        response = requests.get(url)
        print(f"Binance API status: {response.status_code}")
        if response.status_code != 200:
            raise Exception(f"Binance Non-200 response: {response.status_code}")
        return response.json()

    def try_bybit():
        url = "https://api.bybit.com/v5/market/kline"
        params = {"category": "linear", "symbol": "ETHUSDT", "interval": "5", "limit": 200}
        response = requests.get(url, params=params)
        print(f"Bybit API status: {response.status_code}")
        if response.status_code != 200:
            raise Exception(f"Bybit Non-200 response: {response.status_code}")
        return [[
            int(item['start']), item['open'], item['high'], item['low'], item['close'], item['volume']
        ] for item in response.json()['result']['list']]

    def try_coingecko():
        url = "https://api.coingecko.com/api/v3/coins/ethereum/market_chart"
        params = {"vs_currency": "usd", "days": "1", "interval": "minutely"}
        response = requests.get(url, params=params)
        print(f"CoinGecko API status: {response.status_code}")
        if response.status_code != 200:
            raise Exception("CoinGecko API error")
        prices = response.json()['prices']
        return [[t, p, p, p, p, 0] for t, p in prices]

    try:
        try:
            data = try_binance()
        except:
            try:
                data = try_bybit()
            except:
                data = try_coingecko()

        if not data or len(data) < 20:
            raise Exception("Insufficient data")

        df = pd.DataFrame(data, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].astype(float)

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
    except Exception as e:
        print("Error fetching data:", e)
        return None

# === TRADE MONITORING ===
@tasks.loop(minutes=5)
async def monitor_price():
    global alert_channel_id
    if not alert_channel_id:
        return
    try:
        df = get_eth_data()
        if df is None or len(df) < 20:
            return

        current_price = df['close'].iloc[-1]
        ema50 = df['ema50'].iloc[-1]
        rsi_val = df['rsi'].iloc[-1]
        macd = df['macd'].iloc[-1]
        macd_signal_val = df['macd_signal'].iloc[-1]
        obv_slope = df['obv'].iloc[-1] - df['obv'].iloc[-5] if len(df) > 5 else 0
        atr_now = df['atr'].iloc[-1]
        atr_prev = df['atr'].iloc[-5] if len(df) > 5 else df['atr'].iloc[-1]
        vwap_val = df['vwap'].iloc[-1]
        stoch_rsi_val = df['stoch_rsi'].iloc[-1]
        bb_upper = df['bb_upper'].iloc[-1]
        bb_lower = df['bb_lower'].iloc[-1]
        donchian_low = df['donchian_low'].iloc[-1]

        message = None

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

        elif (
            current_price < ema50 and
            rsi_val < 40 and
            macd < macd_signal_val and
            obv_slope < 0 and
            (current_price < donchian_low or current_price < bb_lower) and
            atr_now > atr_prev
        ):
            message = f"⚠️ **ETH Bearish Breakdown Detected**\nPrice: ${current_price:,.2f}"

        if message:
            channel = bot.get_channel(alert_channel_id)
            await channel.send(message)

    except Exception as e:
        print("Monitoring error:", e)

# === STARTUP ===
@bot.event
async def on_ready():
    print(f"✅ Bot is online as {bot.user}")
    monitor_price.start()

# === COMMANDS ===
@bot.command(name="setchannel")
async def setchannel(ctx):
    global alert_channel_id
    alert_channel_id = ctx.channel.id
    await ctx.send("📡 Alerts will be sent to this channel.")

@bot.command(name="price")
async def price(ctx):
    df = get_eth_data()
    if df is None:
        await ctx.send("⚠️ Could not fetch ETH price.")
        return
    price = df['close'].iloc[-1]
    await ctx.send(f"💰 ETH Price: ${price:,.2f}")

@bot.command(name="status")
async def status(ctx):
    df = get_eth_data()
    if df is None or len(df) < 20:
        await ctx.send("⚠️ Could not generate status update.")
        return
    price = df['close'].iloc[-1]
    rsi_val = df['rsi'].iloc[-1]
    macd = df['macd'].iloc[-1]
    macd_signal = df['macd_signal'].iloc[-1]
    stoch_rsi = df['stoch_rsi'].iloc[-1]
    await ctx.send(f"""📊 **ETH Strategy Status**
Price: ${price:,.2f}
RSI: {rsi_val:.2f}
MACD: {macd:.4f}
Signal: {macd_signal:.4f}
Stoch RSI: {stoch_rsi:.2f}
""")

@bot.command(name="commands")
async def commands(ctx):
    await ctx.send("""📜 **Available Commands**
`!setchannel` — Set this channel to receive alerts  
`!price` — Show current ETH price  
`!status` — Manual ETH strategy status update  
`!commands` — Show this command list  
""")

bot.run(os.getenv("DISCORD_BOT_TOKEN"))
