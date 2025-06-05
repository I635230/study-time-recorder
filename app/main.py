import discord
from discord.ext import commands, tasks
import datetime, pytz
import asyncio
from collections import defaultdict
import os
from dotenv import load_dotenv
from server import server_thread

intents = discord.Intents.default()
intents.voice_states = True
intents.members = True
intents.guilds = True

voice_start_times = {}
voice_durations = defaultdict(datetime.timedelta)  # 累積時間記録用

TARGET_CHANNEL_NAME = "記録用"  # 通知するチャンネル名

bot = commands.Bot(command_prefix="!", intents=intents)


# 関数の登録
def handle_vc_join(member, before, after):
    if before.channel is None and after.channel is not None:
        print(f"{member.display_name} がVCに参加しました。")
        return True
    return False

def handle_vc_leave(member, before, after):
    if before.channel is not None and after.channel is None:
        print(f"{member.display_name} がVCから退出しました。")
        return True
    return False

def get_now_jst():
    return datetime.datetime.now(pytz.timezone('Asia/Tokyo'))

def duration_start(member, now): 
    print(f"{member.display_name} の学習時間の計測を開始しました。")
    voice_start_times[member.id] = now

def duration_end(member, now, start): 
    print(f"{member.display_name} の学習時間の計測を終了しました。")
    duration = now - start
    voice_durations[member.id] += duration


# イベントハンドラの登録
@bot.event
async def on_ready():
    print(f"{bot.user} 起動完了")
    daily_report_task.start()  # 起動時に定期タスクを開始

@bot.event
async def on_voice_state_update(member, before, after):
    now = get_now_jst()

    if handle_vc_join(member, before, after):
        duration_start(member, now)
    elif handle_vc_leave(member, before, after):
        start = voice_start_times.pop(member.id, None)
        duration_end(member, now, start)

@tasks.loop(hours=24)
async def daily_report_task():
    await bot.wait_until_ready()
    now = get_now_jst()
    next_midnight = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    wait_seconds = (next_midnight - now).total_seconds()
    print(f"24時になるまで{wait_seconds}秒待機中...")

    await asyncio.sleep(wait_seconds)

    print("24時になりました。")
    now = get_now_jst() # 現在時刻の更新

    # ------------------------------------

    for guild in bot.guilds:
        # テキストチャンネルの取得
        text_channel = discord.utils.get(guild.text_channels, name=TARGET_CHANNEL_NAME)
        if not text_channel:
            continue

        # 今日VCに参加したメンバー一覧を取得
        active_member_ids = list(voice_start_times.keys())
        for member_id in active_member_ids:
            member = guild.get_member(member_id)
            if member:
                # VCが終了していないとき
                start = voice_start_times.pop(member.id, None)
                if start:
                    #終了していない分の学習時間を加算して、通話時間をリセット
                    duration_end(member, now, start)
                    duration_start(member, now)

        # VC記録があるかを判定
        if not voice_durations:
            continue

        # 投稿数から何日目かをカウント
        count = 0
        async for _ in text_channel.history(limit=None):
            count += 1
        report_headers = [f"{count+1}日目"]
        report_headers.append("📊 本日の学習記録：")
        report_lines = [""]

        # 学習記録の投稿
        for member_id, duration in voice_durations.items():
            member = guild.get_member(member_id)
            if member:
                h, remainder = divmod(int(duration.total_seconds()), 3600)
                m, s = divmod(remainder, 60)
                if h > 0 or m > 0:
                    report_lines.append(f"- {member.display_name}: {h}時間{m}分")

        if report_lines != [""]: 
            content = "\n".join(report_headers) + "\n".join(report_lines)
            # print(content) # デバッグ用
            await text_channel.send(content) # 本番用

    voice_durations.clear()

# Koyeb用 サーバー立ち上げ
server_thread()

load_dotenv()
bot.run(os.getenv("DISCORD_TOKEN"))