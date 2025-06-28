import discord
from discord.ext import commands, tasks
import datetime, pytz
import asyncio
from collections import defaultdict
import os
from dotenv import load_dotenv
import asyncio
import uvicorn
from fastapi import FastAPI
import logging
import json

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.members = True
intents.guilds = True

voice_start_times = {}
voice_durations = defaultdict(datetime.timedelta)  # 累積時間記録用

TARGET_CHANNEL_NAME = "記録用"  # 通知するチャンネル名

BACKUP_FILE = "voice_backup.json"

bot = commands.Bot(command_prefix="!", intents=intents)
app = FastAPI()

# FastAPI
async def start_fastapi():
    config = uvicorn.Config(app=app, host="0.0.0.0", port=8000, access_log=False)
    server = uvicorn.Server(config)
    await server.serve()

@app.get("/")
@app.head("/")
def read_root():
    return {"status": "ok"}

# 関数の登録
def handle_vc_join(member, before, after):
    if before.channel is None and after.channel is not None:
        logger.info(f"{datetime.datetime.now().strftime('%Y/%m/%d %H:%M')}: {member.display_name} がVCに参加しました。")
        return True
    return False

def handle_vc_leave(member, before, after):
    if before.channel is not None and after.channel is None:
        logger.info(f"{datetime.datetime.now().strftime('%Y/%m/%d %H:%M')}: {member.display_name} がVCから退出しました。")
        return True
    return False

def get_now_jst():
    return datetime.datetime.now(pytz.timezone('Asia/Tokyo'))

def duration_start(member, now): 
    logger.info(f"{datetime.datetime.now().strftime('%Y/%m/%d %H:%M')}: {member.display_name} の学習時間の計測を開始しました。")
    voice_start_times[member.id] = now

def duration_end(member, now, start): 
    logger.info(f"{datetime.datetime.now().strftime('%Y/%m/%d %H:%M')}: {member.display_name} の学習時間の計測を終了しました。")
    duration = now - start
    voice_durations[member.id] += duration

def get_all_voice_member_ids(guild):
    member_ids = []
    for vc in guild.voice_channels:
        for member in vc.members:
            member_ids.append(member.id)
    return member_ids

def handle_vc_joining(): 
    # Botが参加中のチャンネルを取得
    for guild in bot.guilds:
        now = get_now_jst()
        for member_id in get_all_voice_member_ids(guild): 
            voice_start_times[member_id] = now

# イベントハンドラの登録

# 起動時
@bot.event
async def on_ready():
    logger.info(f"{datetime.datetime.now().strftime('%Y/%m/%d %H:%M')}: {bot.user} 起動完了")
    load_backup() # バックアップからdurationを読み込み
    handle_vc_joining() # VC参加中のメンバーがいたらvoice_start_timeに記録
    daily_report_task.start() # 起動時に定期タスクを開始
    backup_task.start() # 起動時にバックアップタスクを開始

# 通話状態変化時
@bot.event
async def on_voice_state_update(member, before, after):
    now = get_now_jst()

    if handle_vc_join(member, before, after):
        duration_start(member, now)
    elif handle_vc_leave(member, before, after):
        start = voice_start_times.pop(member.id, None)
        duration_end(member, now, start)

# 24時間に1回実行
@tasks.loop(hours=24)
async def daily_report_task():
    await bot.wait_until_ready()
    now = get_now_jst()
    today_3am = now.replace(hour=3, minute=0, second=0, microsecond=0)

    if now < today_3am:
        next_3am = today_3am
    else:
        next_3am = (now + datetime.timedelta(days=1)).replace(hour=3, minute=0, second=0, microsecond=0)

    # 待機時間
    wait_seconds = (next_3am - now).total_seconds()

    logger.info(f"{datetime.datetime.now().strftime('%Y/%m/%d %H:%M')}: 3時になるまで{wait_seconds}秒待機中...")

    await asyncio.sleep(wait_seconds)

    logger.info(f"{datetime.datetime.now().strftime('%Y/%m/%d %H:%M')}: 3時になりました。")
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
            # logger.info(content) # デバッグ用
            await text_channel.send(content) # 本番用

    # 通話時間のリセット
    voice_durations.clear()
    reset_backup()

# 10秒に1回実行
@tasks.loop(seconds=10)
async def backup_task():
    save_backup()

# バックアップメソッド
def save_backup():
    try:
        # durationの計算
        for member_id, member_voice_start_time in voice_start_times.items(): 
            now = get_now_jst()
            voice_durations[member_id] += now - member_voice_start_time
            voice_start_times[member_id] = now

        # データをバックアップ
        data = {
            "durations": {str(k): d.total_seconds() for k, d in voice_durations.items()}
        }
        with open(BACKUP_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.warning(f"{datetime.datetime.now().strftime('%Y/%m/%d %H:%M')}: バックアップ保存失敗: {e}")

def load_backup():
    if not os.path.exists(BACKUP_FILE):
        return
    try:
        with open(BACKUP_FILE, "r") as f:
            data = json.load(f)
        for member_id, member_voice_durations in data.get("durations", {}).items():
            voice_durations[int(member_id)] = datetime.timedelta(seconds=member_voice_durations)
        logger.info(f"{datetime.datetime.now().strftime('%Y/%m/%d %H:%M')}: バックアップを復元しました")
    except Exception as e:
        logger.warning(f"{datetime.datetime.now().strftime('%Y/%m/%d %H:%M')}: バックアップ復元失敗: {e}")

def reset_backup():
    with open("voice_backup.json", "w") as f:
        json.dump({"durations": {}}, f)
    logger.info(f"{datetime.datetime.now().strftime('%Y/%m/%d %H:%M')}: voice_backup.json を初期化しました")

# main
async def main():
    load_dotenv()
    
    try:
        await asyncio.gather(
            start_fastapi(),
            bot.start(os.getenv("DISCORD_TOKEN"))
        )
    except Exception as e:
        logger.info(f"{datetime.datetime.now().strftime('%Y/%m/%d %H:%M')}: 起動中にエラーが発生しました: {e}")

if __name__ == "__main__":
    asyncio.run(main())
