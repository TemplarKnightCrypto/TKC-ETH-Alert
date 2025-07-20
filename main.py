import os
import discord
import requests
import datetime
import pandas as pd
import numpy as np
from discord.ext import commands, tasks
from ta.trend import ema_indicator
from ta.momentum import rsi, stochrsi
from ta.volatility import bollinger_hband, bollinger_lband, average_true_range
from ta.volume import on_balance_volume
from dotenv import load_dotenv

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
alert_channel_id = None

# === Primary (Binance) → Fallback (Bybit) ===
def get_eth_data():
    try:
        url = 'https://api.binance.com/api/v3/klines?symbol=ETHUSDT&interval=5m&limit=200'
        response = requests.get(url)
        print(f"Binance API status: {response.status_code}")
        if response.status_code == 200:
            return parse_binance_data(response.json())
        raise Exception("Binance unavailable")
    except:
        try:
            url = "https://api.bybit.com/v5/market/kline"
            params = {"category": "linear", "symbol": "ETHUSDT", "interval": "5", "limit": 200}
            headers = {"User-Agent": "Mozilla/5.0"}
            response = requests.get(url, params=params, headers=headers)
            print(f"Bybit API status: {response.status_code}")
            if response.status_code == 200:
                return parse_bybit_data(response.json()['result']['list'])
            raise Exception("Bybit unavailable")
        except Exception as e:
            print("Error fetching data:", e)
            return None

def parse_binance_data(data):
    df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume',
                                     'close_time', 'quote_asset_volume', 'num_trades',
                                     'taker_buy_base_volume', 'taker_buy_quote_volume', 'ignore'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df['close'] = df['close'].astype(float)
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    df['volume'] = df['volume'].astype(float)
    return add_indicators(df)

def parse_bybit_data(data):
    df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df['close'] = df['close'].astype(float)
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    df['volume'] = df['volume'].astype(float)
    return add_indicators(df)

def add_indicators(df):
    df['ema50'] = ema_indicator(df['close'], window=50)
    df['rsi'] = rsi(df['close'], window=14)
    df['macd'] = df['close'].ewm(span=12).mean() - df['close'].ewm(span=26).mean()
    df['macd_signal'] = df['macd'].ewm(span=9).mean()
    df['obv'] = on_balance_volume(df['close'], df['volume'].fillna(0))
    df['atr'] = average_true_range(df['high'], df['low'], df['close'], window=14)
    df['vwap'] = (df['volume'].fillna(0) * (df['high'] + df['low'] + df['close']) / 3).cumsum() / df['volume'].fillna(1).cumsum()
    df['stoch_rsi'] = stochrsi(df['close'], window=14, smooth1=3, smooth2=3)
    df['bb_upper'] = bollinger_hband(df['close'], window=20, window_dev=2)
    df['bb_lower'] = bollinger_lband(df['close'], window=20, window_dev=2)
    df['donchian_low'] = df['low'].rolling(window=20).min()
    df['donchian_high'] = df['high'].rolling(window=20).max()
    df['bb_width'] = df['bb_upper'] - df['bb_lower']
    df['macd_cross'] = np.where(df['macd'] > df['macd_signal'], 1, 0)
    df['rsi_overbought'] = df['rsi'] > 70
    df['rsi_oversold'] = df['rsi'] < 30
    df['stochrsi_cross_up'] = (df['stoch_rsi'] > 0.2) & (df['stoch_rsi'].shift(1) <= 0.2)
    df['stochrsi_cross_down'] = (df['stoch_rsi'] < 0.8) & (df['stoch_rsi'].shift(1) >= 0.8)
    return df

# === MONITORING ===
@tasks.loop(minutes=5)
async def monitor_price():
    await check_strategy_alert()

@tasks.loop(minutes=30)
async def periodic_status():
    if alert_channel_id:
        channel = bot.get_channel(alert_channel_id)
        await send_status(channel)

async def check_strategy_alert():
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
        bb_width = df['bb_width'].iloc[-1]

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

        elif df['rsi_overbought'].iloc[-1]:
            message = f"🔺 **RSI Overbought Alert**\nRSI: {rsi_val:.2f}"

        elif df['rsi_oversold'].iloc[-1]:
            message = f"🔻 **RSI Oversold Alert**\nRSI: {rsi_val:.2f}"

        elif df['stochrsi_cross_up'].iloc[-1]:
            message = f"🔄 **Stoch RSI Upturn Alert**\nStoch RSI: {stoch_rsi_val:.2f}"

        elif df['stochrsi_cross_down'].iloc[-1]:
            message = f"⏬ **Stoch RSI Downturn Alert**\nStoch RSI: {stoch_rsi_val:.2f}"

        elif bb_width < df['bb_width'].rolling(window=20).mean().iloc[-1] * 0.5:
            message = f"📉 **Bollinger Band Squeeze Alert**\nLow volatility detected."

        if message:
            channel = bot.get_channel(alert_channel_id)
            await channel.send(message)

    except Exception as e:
        print("Monitoring error:", e)

# === STATUS COMMAND AND SCHEDULER ===
async def send_status(channel):
    df = get_eth_data()
    if df is None or len(df) < 20:
        await channel.send("⚠️ Could not generate status update.")
        return

    price = df['close'].iloc[-1]
    rsi_val = df['rsi'].iloc[-1]
    macd = df['macd'].iloc[-1]
    macd_signal = df['macd_signal'].iloc[-1]
    stoch_rsi = df['stoch_rsi'].iloc[-1]
    ema50 = df['ema50'].iloc[-1]
    vwap = df['vwap'].iloc[-1]
    obv_trend = df['obv'].iloc[-1] - df['obv'].iloc[-5] if len(df) > 5 else 0

    summary = ""
    if price > ema50 and macd > macd_signal and rsi_val > 50:
        summary = "📈 Market Bias: Bullish"
    elif price < ema50 and macd < macd_signal and rsi_val < 40:
        summary = "📉 Market Bias: Bearish"
    else:
        summary = "⚖️ Market Bias: Neutral"

    await channel.send(f"""
📊 **ETH Strategy Status**
Price: ${price:,.2f}
RSI: {rsi_val:.2f}
MACD: {macd:.4f}
Signal: {macd_signal:.4f}
Stoch RSI: {stoch_rsi:.2f}
EMA50: ${ema50:,.2f}
VWAP: ${vwap:,.2f}
OBV Trend: {'⬆️' if obv_trend > 0 else '⬇️'}
{summary}
""")

# === DISCORD COMMANDS ===
@bot.event
async def on_ready():
    print(f"✅ Bot is online as {bot.user}")
    monitor_price.start()
    periodic_status.start()

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
    await send_status(ctx.channel)

@bot.command(name="commands")
async def commands(ctx):
    await ctx.send("""📜 **Available Commands**
`!setchannel` — Set this channel to receive alerts  
`!price` — Show current ETH price  
`!status` — Manual ETH strategy status update  
`!commands` — Show this command list  
""")

bot.run(os.getenv("DISCORD_BOT_TOKEN"))
