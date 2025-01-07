import aiohttp
import asyncio
import traceback
import os
import copy
import math
import random
import json
import my_queue
import user_hidden_score

from khl import Bot,Cert, Message,requester,Event,EventTypes
from khl.card import Card,CardMessage,Types,Module,Element
from aiohttp import client_exceptions
from datetime import datetime,timedelta
from itertools import combinations
from asyncio import Queue

from utils.files import config,RollLog,StartTime,write_roll_log
from utils.myLog import get_time,get_time_str_from_stamp,log_msg,_log
from utils.argsCheck import get_card_msg,roll_args_check,upd_card,msg_view

# 用读取来的 config 初始化 bot
bot = Bot(token=config['token']) # websocket
if not config["ws"]: # webhook
    _log.info(f"[BOT] using webhook at {config['webhook_port']}")
    bot = Bot(cert=Cert(token=config['token'], verify_token=config['verify_token'],encrypt_key=config['encrypt']),
              port=config["webhook_port"])
# 配置kook头链接
kook_base_url = "https://www.kookapp.cn"
kook_headers = {f'Authorization': f"Bot {config['token']}"}
CmdLock = asyncio.Lock()
"""配置命令上锁"""
user_message_queue = Queue()
waiting_for_input = {}

# 按隐藏分平均分组
def split_into_groups(users):
    user_scores = [(user, user_hidden_score.get_hidden_score(user)) for user in users]
    total_score = sum(score for _, score in user_scores)
    target_score = total_score // 2
    
    n = len(user_scores) // 2
    best_diff = float('inf')
    best_combination = None
    
    for group1_users in combinations(user_scores, n):
        group1_score = sum(score for _, score in group1_users)
        group2_score = total_score - group1_score
        
        diff = abs(group1_score - group2_score)
        
        if diff < best_diff:
            best_diff = diff
            best_combination = group1_users
    
    group1 = [user for user, _ in best_combination]
    group2 = [user for user, _ in user_scores if user not in group1]
    return group1, group2

# 查看bot状态
@bot.command(name='alive', case_sensitive=False)
async def alive_check(msg: Message, *args):
    try:
        log_msg(msg)

        # 准备回复内容
        statuses = [
            "🤖 I'm alive and kicking!",
            "✅ Bot is fully operational!",
            "📡 Bot online and ready!",
            "💻 System check complete. All systems go!"
        ]
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 随机选择一条状态回复
        status_msg = random.choice(statuses)
        extra_info = f"\n🕒 **Time**: {current_time}"

        # 回复消息
        await msg.reply(f"{status_msg}{extra_info}")
    except Exception as result:
        _log.exception("Err in alive")

async def help_card(help_text=""):
    text = ""
    
    if "notice" in config:
        text += f"【公告】\n{config['notice']}\n\n"
    
    text += "🛠️ **可用命令**\n"
    text += "・**/alive** - 查看 bot 是否在线\n"
    text += "・**/queue <队列名字> <持续时间（分钟）> <最大用户数（默认为8）>** - 创建队列，人满后自动分组\n"
    text += "・**/join <队列编号>** - 加入指定队列\n"
    text += "・**/queueinfo <队列编号>** - 查询队列分组信息\n"
    text += "・**/recordmatch <队列编号> <地图> <模式> <1队分数> <2队分数>** - 记录比赛结果\n"
    text += "・**/myhistory** - 查询个人历史战绩\n"
    
    if help_text != "":
        text += f"\n📄 **详细信息**\n{help_text}"
    
    # 小字部分
    sub_text = f"开机于：{StartTime}  |  开源仓库：[Github](https://github.com/LJFYC007/BO6-RANK-BOT)\n"
    
    # 返回格式化后的帮助卡片信息
    return await get_card_msg(text, sub_text, header_text="BO6-RANK-BOT 帮助命令")


BOT_USER_ID = ""
@bot.on_message()
async def at_help_cmd(msg:Message):
    try:
        if msg.author_id == "3989343843": return
        if len(msg.content) > 22: return
        global BOT_USER_ID
        if BOT_USER_ID == "":
            cur_bot = await bot.client.fetch_me()
            BOT_USER_ID = cur_bot.id
        if f"(met){BOT_USER_ID}(met)" in msg.content:
            log_msg(msg)
            await msg.reply(await help_card())
            _log.info(f"Au:{msg.author_id} | at_help reply")
    except Exception as result:
        _log.exception(f"Err in at_help")

# Command: Start a new queue
@bot.command(name='queue', case_sensitive=False)
async def start_queue(msg: Message, name: str, duration: int, max_users: int = 8):
    try: 
        log_msg(msg)
        if msg.author_id not in config['admin_user']:
            await msg.reply("❌ 你没有权限执行此操作！")
            return

        queue_id = my_queue.queue_counter + 1
        card = Card(
            Module.Header(f"🎉 接龙 #{queue_id} 开始啦！{name}"),
            Module.Section(Element.Text(f"👥 当前人数：0/{max_users}", Types.Text.KMD)),
            Module.Countdown(datetime.now() + timedelta(minutes=duration), mode=Types.CountdownMode.SECOND),
            Module.Context(Element.Text(f"💬 输入 /join {queue_id} 加入该接龙！"))
        )
        cm = CardMessage(card)
        sent_msg = await msg.reply(cm, use_quote=False)
        my_queue.start_queue(name, duration, max_users, sent_msg['msg_id'])
        _log.info(f"Queue started: {name} with ID #{queue_id}")

    except Exception as e:
        _log.exception(f"Error in start_queue | {e}")
        await msg.reply(await get_card_msg(f"ERR! [{get_time()}] queue", err_card=True))

# Command: Join a queue
@bot.command(name='join', case_sensitive=False)
async def join_queue(msg: Message, queue_id: int):
    log_msg(msg)

    if queue_id not in my_queue.queue_data:
        await msg.reply(f"❌ 接龙 #{queue_id} 不存在！")
        return

    queue = my_queue.queue_data[queue_id]
    if queue['end_time'] <= datetime.now():
        await msg.reply(f"❌ 接龙 #{queue_id} 已结束，无法加入！")
        return
    if len(queue['users']) >= queue['max_users']:
        await msg.reply(f"❌ 接龙 #{queue_id} 已满，无法加入！")
        return
    if msg.author.nickname in queue['users']:
        await msg.reply(f"❌ 你已经加入了接龙 #{queue_id}！")
        return

    my_queue.join_queue(queue_id, msg.author.nickname)
    card = Card(
        Module.Header(f"🎉 接龙 #{queue_id} 进行中！{queue['name']}"),
        Module.Section(Element.Text(f"👥 当前人数：{len(queue['users'])}/{queue['max_users']}", Types.Text.KMD)),
        Module.Countdown(queue['end_time'], mode=Types.CountdownMode.SECOND),
        Module.Context(Element.Text(f"💬 输入 /join {queue_id} 加入该接龙！"))
    )
    cm = CardMessage(card)

    await bot.client.gate.request(
        'POST',
        'message/update',
        data={"msg_id": queue['message_id'], "content": json.dumps(list(cm))}
    )

# Command: Admin join a queue on behalf of another user
@bot.command(name='adminjoin', case_sensitive=False)
async def admin_join_queue(msg: Message, queue_id: int, username: str):
    try:
        log_msg(msg)

        if msg.author_id not in config['admin_user']:
            await msg.reply("❌ 你没有权限执行此操作！")
            return

        if queue_id not in my_queue.queue_data:
            await msg.reply(f"❌ 接龙 #{queue_id} 不存在！")
            return

        queue = my_queue.queue_data[queue_id]
        if queue['end_time'] <= datetime.now():
            await msg.reply(f"❌ 接龙 #{queue_id} 已结束，无法加入！")
            return
        if len(queue['users']) >= queue['max_users']:
            await msg.reply(f"❌ 接龙 #{queue_id} 已满，无法加入！")
            return
        if username in queue['users']:
            await msg.reply(f"❌ 用户 {username} 已在接龙 #{queue_id} 中！")
            return

        my_queue.join_queue(queue_id, username)
        card = Card(
            Module.Header(f"🎉 接龙 #{queue_id} 进行中！{queue['name']}"),
            Module.Section(Element.Text(f"👥 当前人数：{len(queue['users'])}/{queue['max_users']}", Types.Text.KMD)),
            Module.Countdown(queue['end_time'], mode=Types.CountdownMode.SECOND),
            Module.Context(Element.Text(f"💬 输入 /join {queue_id} 加入该接龙！"))
        )
        cm = CardMessage(card)

        await bot.client.gate.request(
            'POST',
            'message/update',
            data={"msg_id": queue['message_id'], "content": json.dumps(list(cm))}
        )

        await msg.reply(f"✅ 已成功将用户 {username} 加入接龙 #{queue_id}！")
        _log.info(f"Admin {msg.author.nickname} added {username} to queue #{queue_id}")

    except Exception as e:
        _log.exception(f"Error in admin_join_queue | {e}")
        await msg.reply(f"err\n```\n{traceback.format_exc()}\n```")

# Command: Output group information
@bot.task.add_interval(seconds=10)
async def check_queues():
    current_time = datetime.now()
    
    for qid, data in my_queue.queue_data.items():
        users = data['users']
        max_users = data['max_users']
        user_list = "\n".join(users) if users else "无用户加入"

        # 检查是否已到截止时间或队列已满
        if (data['end_time'] <= current_time or len(users) >= max_users) and not data.get('processed', False):
            # 分组
            groups = split_into_groups(users)
            group_text = "\n\n".join(
                [f"组 {i + 1}:\n" + "\n".join([f"{user} ({user_hidden_score.get_hidden_score(user)})" for user in group])
                 for i, group in enumerate(groups)]
            )

            # 发送通知
            await debug_ch.send(CardMessage(Card(
                Module.Header(f"⚠️ 接龙 #{qid} 已关闭！"),
                Module.Section(Element.Text(f"🎉 已加入的用户:\n{user_list}", Types.Text.KMD)),
                Module.Section(Element.Text(f"📋 分组结果:\n{group_text}", Types.Text.KMD))
            )))

            # 保存分组信息到数据库
            data['processed'] = True
            data['groups'] = groups
            my_queue.save_queue_to_db(qid, data)
            _log.info(f"Queue #{qid} processed and group info sent.")

# Command: Query group information for a closed queue
@bot.command(name='queueinfo', case_sensitive=False)
async def query_queue_info(msg: Message, queue_id: int):
    try:
        log_msg(msg)

        if queue_id not in my_queue.queue_data:
            await msg.reply(f"❌ 接龙 #{queue_id} 不存在！")
            return

        queue = my_queue.queue_data[queue_id]
        if not queue.get('processed', False):
            await msg.reply(f"❌ 接龙 #{queue_id} 尚未截止，无法查询分组信息！")
            return

        groups = queue.get('groups', [])
        if not groups:
            await msg.reply(f"❌ 接龙 #{queue_id} 没有分组数据！")
            return

        group_text = "\n\n".join([f"组 {i + 1}:\n" + "\n".join([f"{user} ({user_hidden_score.get_hidden_score(user)})" for user in group]) for i, group in enumerate(groups)])
        await msg.reply(f"📋 接龙 #{queue_id} 的分组信息:\n{group_text}")
        _log.info(f"Queue info queried for #{queue_id}")

    except Exception as e:
        _log.exception(f"Error in query_queue_info | {e}")
        await msg.reply(f"err\n```\n{traceback.format_exc()}\n```")

# Command: Admin set group manually
@bot.command(name='setgroup', case_sensitive=False)
async def set_group(msg: Message, queue_id: int, *user_ids):
    try:
        log_msg(msg)

        if msg.author_id not in config['admin_user']:
            await msg.reply("❌ 你没有权限执行此操作！")
            return

        if len(user_ids) != 8:
            await msg.reply("❌ 请提供 8 个用户 ID，前 4 个为第 1 组，后 4 个为第 2 组！")
            return

        if queue_id not in my_queue.queue_data:
            await msg.reply(f"❌ 接龙 #{queue_id} 不存在！")
            return

        queue = my_queue.queue_data[queue_id]
        group1 = list(user_ids[:4])
        group2 = list(user_ids[4:])
        queue['groups'] = [group1, group2]
        queue['processed'] = True

        my_queue.save_queue_to_db(queue_id, queue)
        group_text = f"组 1:\n" + "\n".join([f"{user} ({user_hidden_score.get_hidden_score(user)})" for user in group1])
        group_text += f"\n\n组 2:\n" + "\n".join([f"{user} ({user_hidden_score.get_hidden_score(user)})" for user in group2])

        # 发送分组结果通知
        await debug_ch.send(CardMessage(Card(
            Module.Header(f"📋 接龙 #{queue_id} 的分组已被管理员手动修改"),
            Module.Section(Element.Text(f"🎉 手动分组结果:\n{group_text}", Types.Text.KMD))
        )))

        await msg.reply(f"✅ 接龙 #{queue_id} 的分组信息已成功修改！")
        _log.info(f"Admin {msg.author.nickname} manually set groups for queue #{queue_id}")

    except Exception as e:
        _log.exception(f"Error in set_group | {e}")
        await msg.reply(f"err\n```\n{traceback.format_exc()}\n```")

# Command: Record match result and update hidden scores
@bot.command(name='recordmatch', case_sensitive=False)
async def record_match(msg: Message, queue_id: int, map: str, mode: str, score_group0: int, score_group1: int):
    try:
        log_msg(msg)
        if msg.author_id not in config['admin_user']:
            await msg.reply("❌ 你没有权限执行此操作！")
            return
        if queue_id not in my_queue.queue_data:
            await msg.reply(f"❌ 接龙 #{queue_id} 不存在！")
            return

        queue = my_queue.queue_data[queue_id]
        groups = queue.get('groups', [])
        if len(groups) != 2:
            await msg.reply(f"❌ 接龙 #{queue_id} 的分组数据不完整！")
            return

        if score_group0 > score_group1:
            winning_group = groups[0]
            losing_group = groups[1]
        else:
            winning_group = groups[1]
            losing_group = groups[0]
        await msg.reply(f"✅ 接龙 #{queue_id} 的比赛结果已成功录入！\n"
                        f"⚔️ 比分: {score_group0} - {score_group1}\n"
                        f"接下来，请依次输入每个选手的战绩信息。", use_quote=False)

        info = {}
        async def get_kd_kills_for_user(user: str):
            await msg.reply(f"请输入 {user} 的战绩（格式：击杀数 死亡数）：", use_quote=False)
            waiting_for_input[msg.author_id] = True
            while waiting_for_input[msg.author_id]:
                await asyncio.sleep(0.5)
            user_input = await user_message_queue.get()
            try:
                kills, deaths = user_input.split()
                info[user] = {"kd": round(float(kills) / float(deaths), 2), "kills": int(kills)}
            except ValueError:
                await msg.reply(f"输入格式错误，请重新输入！")
                await get_kd_kills_for_user(user)

        for user in winning_group + losing_group:
            await get_kd_kills_for_user(user)

        hidden_score_updates = []
        for user in winning_group:
            kd = info[user]["kd"]
            kills = info[user]["kills"]
            old_score, new_score, score_change = calculate_hidden_score(user, map, mode, "Win", score_group0, score_group1, kd, kills)
            hidden_score_updates.append(f"{user}: {old_score} ➡️ {new_score} (+{score_change})  KD: {kd}, Kills: {kills}")
        for user in losing_group:
            kd = info[user]["kd"]
            kills = info[user]["kills"]
            old_score, new_score, score_change = calculate_hidden_score(user, map, mode, "Loss", score_group0, score_group1, kd, kills)
            hidden_score_updates.append(f"{user}: {old_score} ➡️ {new_score} (-{-score_change})  KD: {kd}, Kills: {kills}")

        hidden_scores_text = "\n".join(hidden_score_updates)
        await debug_ch.send(CardMessage(Card(
            Module.Header(f"🎮 接龙 #{queue_id} 比赛结果已录入！"),
            Module.Section(Element.Text(f"📋 地图: {map}\n🎮 模式: {mode}", Types.Text.KMD)),
            Module.Section(Element.Text(f"⚔️ 比分: {score_group0} - {score_group1}", Types.Text.KMD)),
            Module.Section(Element.Text(f"📊 隐藏分更新:\n{hidden_scores_text}", Types.Text.KMD))
        )))

    except Exception as e:
        _log.exception(f"Error in record_match | {e}")
        await msg.reply(f"err\n\n{traceback.format_exc()}\n")

@bot.on_message()
async def handle_user_message(msg: Message):
    if msg.author_id in waiting_for_input and waiting_for_input[msg.author_id]:
        await user_message_queue.put(msg.content)
        waiting_for_input[msg.author_id] = False

# Command: Query user's match history
@bot.command(name='myhistory', case_sensitive=False)
async def my_history(msg: Message):
    try:
        log_msg(msg)
        user = msg.author.nickname
        history = user_hidden_score.get_match_history(user)
        if not history:
            await msg.reply(f"📋 {user} 的历史战绩为空！")
            return
        history_text = "\n".join([
            f"日期: {datetime.fromisoformat(match[6]).strftime('%Y-%m-%d')} | 地图: {match[0]} | 模式: {match[1]} | 结果: {match[2]} | ️比分: {match[3]} | KD: {match[4]} | Kills: {match[5]}"
            for match in history
        ])
        await msg.reply(f"📋 **{user} 的历史战绩**\n{history_text}", use_quote=False)
        _log.info(f"History queried for user {user}")
    except Exception as e:
        _log.exception(f"Error in my_history | {e}")
        await msg.reply(f"err\n```\n{traceback.format_exc()}\n```")
        
def sigmoid(x, mid, k=5):
    return 1 / (1 + math.exp(-k * (x - mid)))

# 计算隐藏分
def calculate_hidden_score(user: str, map: str, mode: str, result: str, score_group0: int, score_group1: int, kd: float, kills: int):
    score_diff = abs(score_group0 - score_group1) / max(score_group0, score_group1) 
    score_change = sigmoid(score_diff, 0.5) * 2 - 1
    score_change = score_change * 10 + 30
    score_change = score_change * sigmoid(kd, 1, 1) * 2
   
    score_change = int(score_change)
    if result == "Loss":
        score_change = -score_change
    old_score = user_hidden_score.get_hidden_score(user)
    new_score = old_score + score_change
    
    user_hidden_score.add_match_history(user, map, mode, result, f"{score_group0}:{score_group1}" if result == "Win" else f"{score_group1}:{score_group0}", kd, kills)
    user_hidden_score.update_hidden_score(user, new_score)
    return old_score, new_score, score_change

# 开机任
@bot.on_startup
async def startup_task(b:Bot):
    try:
        global debug_ch
        assert('admin_user' in config)
        # 获取debug频道
        debug_ch = await bot.client.fetch_public_channel(config['debug_ch'])
        _log.info("[BOT.START] fetch debug channel success")

        my_queue.init_db()
        user_hidden_score.init_db()
        _log.info("Database initialized.")
    except:
        _log.exception(f"[BOT.START] ERR!")
        os.abort()

# botmarket通信
@bot.task.add_interval(minutes=25)
async def botmarket_ping_task():
    api = "http://bot.gekj.net/api/v1/online.bot"
    headers = {'uuid': 'a5654f65-bd2e-4983-8448-1ffe78e0d3c1'}
    async with aiohttp.ClientSession() as session:
        await session.post(api, headers=headers)
# 定时写文件
@bot.task.add_interval(minutes=4)
async def save_log_file_task():
    await write_roll_log(log_info="[BOT.TASK]")
# 立即写文件
@bot.command(name='fflush')
async def save_log_file_cmd(msg:Message,*arg):
    try:
        log_msg(msg)
        if msg.author_id not in config['admin_user']:
            return # 非管理员，跳出
        await write_roll_log(log_info="[FFLUSH.CMD]")
        is_kill = '-kill' in arg # 是否需要停止运行？
        text = "写入数据文件成功"
        if is_kill:
            text += "\n收到`kill`命令，机器人退出"
        # 发送提示信息
        await msg.reply(await get_card_msg(text))
        # 如果有kill停止运行
        if is_kill:
            _log.info(f"[KILL] bot exit | Au:{msg.author_id}\n")
            os._exit(0)
    except:
        _log.exception(f'err in fflush | Au:{msg.author_id}')
        await msg.reply(f"err\n```\n{traceback.format_exc()}\n```")
        os.abort() # 该命令执行有问题也需要退出

# 开机 （如果是主文件就开机）
if __name__ == '__main__':
    # 开机的时候打印一次时间，记录开启时间
    _log.info(f"[BOT] Start at {get_time()}")
    bot.run()