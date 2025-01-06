import json
import sqlite3 as sql
from datetime import datetime

# 数据库路径
DB_PATH = 'data\\user_hidden_score.db'

# 初始化数据库
def init_db():
    conn = sql.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE,
                        hidden_score INTEGER DEFAULT 1000
                      )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS match_history (
                        match_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        map_name TEXT,
                        category TEXT,
                        result TEXT,
                        score TEXT,
                        kd_ratio REAL,
                        match_date TEXT,
                        FOREIGN KEY(user_id) REFERENCES users(user_id)
                      )''')
    conn.commit()
    conn.close()

# 添加用户
def add_user(username):
    conn = sql.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO users (username) VALUES (?)", (username,))
        conn.commit()
        print(f"User {username} added.")
    except sql.IntegrityError:
        print(f"User {username} already exists.")
    conn.close()

# 更新用户隐藏分
def update_hidden_score(username, new_score):
    conn = sql.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET hidden_score = ? WHERE username = ?", (new_score, username))
    conn.commit()
    conn.close()
    print(f"Hidden score for {username} updated to {new_score}.")

# 记录用户的历史战绩
def add_match_history(username, map_name, category, result, score, kd_ratio):
    conn = sql.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE username = ?", (username,))
    user = cursor.fetchone()
    if not user:
        print(f"User {username} not found.")
        conn.close()
        return

    user_id = user[0]
    match_date = datetime.now().isoformat()
    cursor.execute('''INSERT INTO match_history 
                      (user_id, map_name, category, result, score, kd_ratio, match_date)
                      VALUES (?, ?, ?, ?, ?, ?, ?)''',
                   (user_id, map_name, category, result, score, kd_ratio, match_date))
    conn.commit()
    conn.close()
    print(f"Match history added for {username}.")

# 获取用户的隐藏分
def get_hidden_score(username):
    conn = sql.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT hidden_score FROM users WHERE username = ?", (username,))
    score = cursor.fetchone()
    conn.close()
    if score:
        return score[0]
    else:
        add_user(username)
        return 1000

# 获取用户的历史战绩
def get_match_history(username):
    conn = sql.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE username = ?", (username,))
    user = cursor.fetchone()
    if not user:
        print(f"User {username} not found.")
        conn.close()
        return []

    user_id = user[0]
    cursor.execute('''SELECT map_name, category, result, score, kd_ratio, match_date 
                      FROM match_history WHERE user_id = ?''', (user_id,))
    matches = cursor.fetchall()
    conn.close()
    return matches

# 示例：添加用户、更新隐藏分、记录历史战绩、获取历史战绩
if __name__ == '__main__':
    init_db()
    
    # 添加用户
    add_user("JinfanLu")

    # 更新用户隐藏分
    update_hidden_score("JinfanLu", 1200)

    # 添加历史战绩
    add_match_history("JinfanLu", "Dust2", "Ranked", "Win", "16:8", 1.5)
    add_match_history("JinfanLu", "Mirage", "Casual", "Loss", "10:16", 0.9)

    # 获取用户隐藏分
    score = get_hidden_score("JinfanLu")
    print(f"JinfanLu's hidden score: {score}")

    # 获取用户的历史战绩
    history = get_match_history("JinfanLu")
    print("Match History:")
    for match in history:
        print(match)
