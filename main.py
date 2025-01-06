import aiohttp
import asyncio
import traceback
import os
import copy
import random
import json
import my_queue
import user_hidden_score

from khl import Bot,Cert, Message,requester,Event,EventTypes
from khl.card import Card,CardMessage,Types,Module,Element
from aiohttp import client_exceptions
from datetime import datetime,timedelta
from itertools import combinations

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

# Command: Start a new queue
@bot.command(name='queue', case_sensitive=False)
async def start_queue(msg: Message, name: str, duration: int, max_users: int = 8):
    try: 
        log_msg(msg)

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

        # 权限检查 - 只有管理员用户可以使用此命令
        if msg.author_id not in config['admin_user']:
            await msg.reply("❌ 你没有权限执行此操作！")
            return

        # 检查队列是否存在
        if queue_id not in my_queue.queue_data:
            await msg.reply(f"❌ 接龙 #{queue_id} 不存在！")
            return

        queue = my_queue.queue_data[queue_id]

        # 检查队列是否已经结束
        if queue['end_time'] <= datetime.now():
            await msg.reply(f"❌ 接龙 #{queue_id} 已结束，无法加入！")
            return

        # 检查队列是否已满
        if len(queue['users']) >= queue['max_users']:
            await msg.reply(f"❌ 接龙 #{queue_id} 已满，无法加入！")
            return

        # 检查用户是否已在队列中
        if username in queue['users']:
            await msg.reply(f"❌ 用户 {username} 已在接龙 #{queue_id} 中！")
            return

        # 管理员代替用户加入队列
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

# 开机任务
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