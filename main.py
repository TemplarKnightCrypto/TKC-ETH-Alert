import os
import json
import logging
import requests
import pandas as pd
import pytz
from datetime import datetime
from discord.ext import tasks
import discord
from ta.trend import ema_indicator
from ta.momentum import rsi, stochrsi, tsi
from ta.volatility import average_true_range
from ta.volume import on_balance_volume

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
TOKEN = os.getenv('DISCORD_BOT_TOKEN')
CHANNEL_FILE = 'channels.json'
CENTRAL_TZ = pytz.timezone('US/Central')

# --- Channel Persistence ---
def load_channels():
    if os.path.isfile(CHANNEL_FILE):
        with open(CHANNEL_FILE, 'r') as f:
            return json.load(f)
    return {'alert': None, 'status': None}

def save_channels(channels):
    with open(CHANNEL_FILE, 'w') as f:
        json.dump(channels, f)

channels = load_channels()

# --- Data Fetching & Indicators ---
def fetch_ohlc(interval: str = '5', limit: int = 200) -> pd.DataFrame:
    url = f"https://api.kraken.com/0/public/OHLC?pair=XETHZUSD&interval={interval}&since=0"
    resp = requests.get(url)
    resp.raise_for_status()
    data = resp.json().get('result', {})
    key = next(k for k in data if k != 'last')
    df = pd.DataFrame(data[key], columns=["time","open","high","low","close","vwap","volume","count"]).astype(float)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    df = df.set_index('time')
    return df


def apply_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df['ema50'] = ema_indicator(df['close'], window=50)
    df['rsi'] = rsi(df['close'], window=14)
    df['atr'] = average_true_range(df['high'], df['low'], df['close'], window=14)
    exp1 = df['close'].ewm(span=12, adjust=False).mean()
    exp2 = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = exp1 - exp2
    df['signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    df['macd_hist_flip'] = (df['macd'] - df['signal']).diff() > 0
    df['stochrsi'] = stochrsi(df['close'], window=14)
    df['tsi'] = tsi(df['close'])
    df['obv'] = on_balance_volume(df['close'], df['volume'])
    df['volume_spike'] = df['volume'] > df['volume'].rolling(20).mean() * 1.5
    # Supertrend approximation
    df['supertrend_bull'] = df['close'] > df['high'].rolling(10).mean()
    df['supertrend_bear'] = df['close'] < df['low'].rolling(10).mean()
    # Alligator
    df['jaw'] = df['close'].rolling(13).mean()
    df['teeth'] = df['close'].rolling(8).mean()
    df['lips'] = df['close'].rolling(5).mean()
    df['alligator_bull'] = (df['lips'] > df['teeth']) & (df['teeth'] > df['jaw'])
    df['alligator_bear'] = (df['lips'] < df['teeth']) & (df['teeth'] < df['jaw'])
    # Ichimoku
    high9, low9 = df['high'].rolling(9).max(), df['low'].rolling(9).min()
    high26, low26 = df['high'].rolling(26).max(), df['low'].rolling(26).min()
    tenkan = (high9 + low9) / 2
    kijun = (high26 + low26) / 2
    df['span_a'] = ((tenkan + kijun) / 2).shift(26)
    span52_h, span52_l = df['high'].rolling(52).max(), df['low'].rolling(52).min()
    df['span_b'] = ((span52_h + span52_l) / 2).shift(26)
    df['ichimoku_bull'] = (df['close'] > df['span_a']) & (df['close'] > df['span_b'])
    df['ichimoku_bear'] = (df['close'] < df['span_a']) & (df['close'] < df['span_b'])
    df['ichimoku_twist'] = ((df['span_a'] - df['span_b']).abs().diff().rolling(2).mean()) < 1e-3
    latest = df.iloc[-1]
    df['market_bias'] = '🟢 Bullish' if latest['ichimoku_bull'] else '🔴 Bearish'
    return df

# --- Signal Detection & Backtest ---
class SignalDetector:
    def __init__(self, df): self.df = df
    def breakout_long(self):
        l = self.df.iloc[-1]
        res = self.df['high'].rolling(20).max().iloc[-2]
        if l['close'] > res and l['macd_hist_flip'] and l['volume_spike']:
            return self._build('Breakout Long', l)
    def pullback_long(self):
        l = self.df.iloc[-1]
        sup = self.df['low'].rolling(20).min().iloc[-2]
        if sup < l['close'] < sup + l['atr'] and l['rsi'] < 40:
            return self._build('Pullback Long', l)
    def breakdown_short(self):
        l = self.df.iloc[-1]
        sup = self.df['low'].rolling(20).min().iloc[-2]
        if l['close'] < sup and not l['macd_hist_flip'] and l['volume_spike']:
            return self._build('Breakdown Short', l)
    def detect(self):
        return self.breakout_long() or self.pullback_long() or self.breakdown_short()
    def _build(self, ttype, l):
        atr, price = l['atr'], l['close']
        if 'Long' in ttype:
            stop = price - atr
            tp1, tp2 = price + atr*1.5, price + atr*2.5
        else:
            stop = price + atr
            tp1, tp2 = price - atr*1.5, price - atr*2.5
        rr = abs((tp1 - price) / (price - stop))
        return {'type': ttype, 'entry': price, 'stop': stop, 'tp1': tp1, 'tp2': tp2, 'rr': rr}

def backtest(df):
    trades = []
    for i in range(20, len(df)-1):
        sig = SignalDetector(df.iloc[:i+1]).detect()
        if sig:
            e = df['close'].iloc[i]
            x = df['close'].iloc[i+1]
            pnl = (x - e) if 'Long' in sig['type'] else (e - x)
            trades.append(pnl)
    wins = [p for p in trades if p > 0]
    return {'total': len(trades), 'win_rate': len(wins)/len(trades)*100 if trades else 0}

# --- Bot Setup with discord.Bot ---
intents = discord.Intents.default()
bot = discord.Bot(intents=intents)

@bot.event
async def on_ready():
    logging.info(f"Logged in as {bot.user}")
    scan_loop.start()

@tasks.loop(minutes=30)
async def scan_loop():
    df = apply_indicators(fetch_ohlc())
    sig = SignalDetector(df).detect()
    # Trade Alert
    if sig and channels['alert']:
        embed = discord.Embed(
            title=f"🚨 {sig['type']} – ETH/USDT",
            description="Confirmed by MACD flip + volume spike",
            color=0x00FF00 if 'Long' in sig['type'] else 0xFF0000,
            timestamp=datetime.utcnow()
        )
        embed.set_author(name="ETHTradeBot")
        embed.add_field(name="Entry", value=f"${sig['entry']:.2f}", inline=True)
        embed.add_field(name="Stop", value=f"${sig['stop']:.2f}", inline=True)
        embed.add_field(name="TP1", value=f"${sig['tp1']:.2f}", inline=True)
        embed.add_field(name="TP2", value=f"${sig['tp2']:.2f}", inline=True)
        embed.add_field(name="R/R", value=f"{sig['rr']:.2f}×", inline=True)
        embed.set_footer(text="Updated every 30 min")
        await bot.get_channel(channels['alert']).send(embed=embed)
    # Status Report
    if channels['status']:
        l = df.iloc[-1]
        p = df.iloc[-2]
        pct = (l['close'] - p['close']) / p['close'] * 100
        ts = l.name.astimezone(CENTRAL_TZ).strftime('%Y-%m-%d %I:%M %p')
        st = discord.Embed(
            title=f"📊 ETH Strategy Status at {ts}",
            color=0x0099FF,
            timestamp=datetime.utcnow()
        )
        st.add_field(name="Price", value=f"${l['close']:.2f} ({pct:+.2f}%)", inline=True)
        st.add_field(name="RSI", value=f"{l['rsi']:.2f}", inline=True)
        st.add_field(name="MACD", value=f"{l['macd']:.4f} | {l['signal']:.4f}", inline=True)
        st.add_field(name="Stoch RSI", value=f"{l['stochrsi']:.2f}", inline=True)
        st.add_field(name="EMA50", value=f"${l['ema50']:.2f}", inline=True)
        st.add_field(name="OBV", value=f"{int(l['obv']):,}", inline=True)
        st.add_field(name="Supertrend", value="🟢 Bullish" if l['supertrend_bull'] else "🔴 Bearish", inline=True)
        st.add_field(name="Alligator", value="🟢 Bullish" if l['alligator_bull'] else "🔴 Bearish" if l['alligator_bear'] else "⚪ Neutral", inline=True)
        st.add_field(name="Ichimoku", value="🟢 Bullish" if l['ichimoku_bull'] else "🔴 Bearish" if l['ichimoku_bear'] else "⚪ Neutral", inline=True)
        st.add_field(name="Twist Alert", value="⚠️ Detected" if l['ichimoku_twist'] else "✅ Stable", inline=True)
        st.add_field(name="Market Bias", value=l['market_bias'], inline=True)
        st.set_footer(text="Metrics updated every 30 min")
        await bot.get_channel(channels['status']).send(embed=st)

# --- Slash Commands ---
@bot.slash_command(description="Get an on-demand trade signal")
async def trade(ctx):
    df = apply_indicators(fetch_ohlc())
    sig = SignalDetector(df).detect()
    if not sig:
        return await ctx.respond("🕵️ No active trade.")
    embed = discord.Embed(
        title=f"💡 {sig['type']} – ETH/USDT",
        color=0x00FF00 if 'Long' in sig['type'] else 0xFF0000,
        timestamp=datetime.utcnow()
    )
    for k in ['entry','stop','tp1','tp2','rr']:
        name = k.capitalize() if k != 'rr' else 'R/R'
        value = f"${sig[k]:.2f}" if k != 'rr' else f"{sig[k]:.2f}×"
        embed.add_field(name=name, value=value, inline=True)
    embed.set_footer(text="Requested by user")
    await ctx.respond(embed=embed)

@bot.slash_command(description="Run a quick backtest of your strategy")
async def backtest_cmd(ctx, candles: int = 200):
    df = apply_indicators(fetch_ohlc(limit=candles))
    stats = backtest(df)
    await ctx.respond(f"Backtest: {stats['total']} trades, win rate {stats['win_rate']:.1f}%")

@bot.slash_command(description="Set alert channel")
async def set_alert_channel(ctx):
    channels['alert'] = ctx.channel.id
    save_channels(channels)
    await ctx.respond(f"✅ Alert channel set to {ctx.channel.mention}")

@bot.slash_command(description="Set status channel")
async def set_status_channel(ctx):
    channels['status'] = ctx.channel.id
    save_channels(channels)
    await ctx.respond(f"✅ Status channel set to {ctx.channel.mention}")

@bot.slash_command(description="Check Alligator indicator status")
async def alligator(ctx):
    df = apply_indicators(fetch_ohlc())
    l = df.iloc[-1]
    status = "🐊 Bullish" if l['alligator_bull'] else "🐊 Bearish" if l['alligator_bear'] else "🐊 Neutral"
    embed = discord.Embed(title="Alligator Indicator", description=status, color=0x00FFFF, timestamp=datetime.utcnow())
    await ctx.respond(embed=embed)

@bot.slash_command(description="Get Camarilla levels and status")
async def camarilla(ctx):
    df = fetch_ohlc()
    l = df.iloc[-1]
    h3, l3, price = l['high']*1.015, l['low']*0.985, l['close']
    status = "Breakout (Above H3)" if price > h3 else "Breakdown (Below L3)" if price < l3 else "Range bound"
    embed = discord.Embed(title="Camarilla Levels", color=0xFFA500, timestamp=datetime.utcnow())
    embed.add_field(name="H3", value=f"${h3:.2f}", inline=True)
    embed.add_field(name="L3", value=f"${l3:.2f}", inline=True)
    embed.add_field(name="Price", value=f"${price:.2f}", inline=True)
    embed.set_footer(text=status)
    await ctx.respond(embed=embed)

@bot.slash_command(description="Check Ichimoku Cloud state")
async def cloud(ctx):
    df = apply_indicators(fetch_ohlc())
    cur, prev = df.iloc[-1], df.iloc[-2]
    cur_cloud = "Green" if cur['ichimoku_bull'] else "Red" if cur['ichimoku_bear'] else "Neutral"
    prev_cloud = "Green" if prev['ichimoku_bull'] else "Red" if prev['ichimoku_bear'] else "Neutral"
    embed = discord.Embed(title="Ichimoku Cloud", color=0x800080, timestamp=datetime.utcnow())
    embed.add_field(name="Previous", value=prev_cloud, inline=True)
    embed.add_field(name="Current", value=cur_cloud, inline=True)
    await ctx.respond(embed=embed)

@bot.slash_command(description="Summarize 1% moves over 4hr candles")
async def ethmoves(ctx):
    df = fetch_ohlc(interval='240', limit=42)
    up = down = 0
    for i in range(len(df)-1):
        e = df['close'].iloc[i]
        tu, td = e*1.01, e*0.99
        for j in range(i+1, len(df)):
            if df['high'].iloc[j] >= tu: up += 1; break
            if df['low'].iloc[j] <= td: down += 1; break
    total = up + down
    embed = discord.Embed(title="1% Move Summary (4hr)", color=0x0000FF, timestamp=datetime.utcnow())
    embed.add_field(name="Up Moves", value=f"{up} ({up/total*100:.1f}%)", inline=True)
    embed.add_field(name="Down Moves", value=f"{down} ({down/total*100:.1f}%)", inline=True)
    embed.add_field(name="Total", value=str(total), inline=True)
    await ctx.respond(embed=embed)

if __name__ == '__main__':
    bot.run(TOKEN)```
