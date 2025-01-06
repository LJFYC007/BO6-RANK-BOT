import json
import sqlite3 as sql
from datetime import datetime, timedelta

# Database connection
DB_PATH = 'data\\queue_data.db'

# Initialize database
def init_db():
    conn = sql.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS queues (
                        queue_id INTEGER PRIMARY KEY,
                        name TEXT,
                        max_users INTEGER,
                        end_time TEXT,
                        users TEXT,
                        message_id TEXT,
                        processed INTEGER DEFAULT 0,
                        groups TEXT
                      )''')
    conn.commit()
    conn.close()

    global queue_data
    global queue_counter
    queue_data = load_queues_from_db()
    queue_counter = max(queue_data.keys(), default=0)

# Save queue to database
def save_queue_to_db(queue_id, queue_info):
    conn = sql.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''INSERT OR REPLACE INTO queues 
                      (queue_id, name, max_users, end_time, users, message_id, processed, groups)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                   (queue_id,
                    queue_info['name'],
                    queue_info['max_users'],
                    queue_info['end_time'].isoformat(),
                    json.dumps(queue_info['users']),
                    queue_info.get('message_id', ''),
                    queue_info.get('processed', 0),
                    json.dumps(queue_info.get('groups', []))))
    conn.commit()
    conn.close()

# Load queues from database
def load_queues_from_db():
    conn = sql.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM queues')
    rows = cursor.fetchall()
    conn.close()

    queue_data = {}
    for row in rows:
        queue_id, name, max_users, end_time, users, message_id, processed, groups = row
        queue_data[queue_id] = {
            'name': name,
            'max_users': max_users,
            'end_time': datetime.fromisoformat(end_time),
            'users': json.loads(users),
            'message_id': message_id,
            'processed': processed,
            'groups': json.loads(groups) if groups else []
        }
    return queue_data

# Function to start a new queue
def start_queue(name, duration, max_users=8, message_id=''):
    global queue_counter

    queue_counter += 1
    queue_id = queue_counter

    queue_info = {
        'name': name,
        'max_users': max_users,
        'end_time': datetime.now() + timedelta(minutes=duration),
        'users': [],
        'message_id': message_id
    }

    queue_data[queue_id] = queue_info
    save_queue_to_db(queue_id, queue_info)
    print(f"Queue started: {name} with ID #{queue_id}")

# Function to join a queue
def join_queue(queue_id, user_name):
    queue = queue_data[queue_id]
    queue['users'].append(user_name)
    save_queue_to_db(queue_id, queue)
    print(f"User {user_name} joined Queue #{queue_id}.")

if __name__ == '__main__':
    init_db()