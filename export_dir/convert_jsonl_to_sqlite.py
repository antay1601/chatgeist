#!/usr/bin/env python3
"""
Convert Telegram messages JSONL to SQLite database
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
import sys
from typing import Dict, List, Any, Optional

def parse_iso_to_timestamp(iso_date: str) -> int:
    """Convert ISO date string to Unix timestamp"""
    dt = datetime.fromisoformat(iso_date.replace('+00:00', '+00:00'))
    return int(dt.timestamp())

def extract_sender_info(message: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """Extract sender username and display name from reactors if available"""
    username = None
    display_name = None

    # Try to get info from reactors list
    if 'reactors' in message and message['reactors']:
        sender_id = message.get('sender_id')
        if sender_id:
            for reactor in message['reactors']:
                if reactor.get('peer_id') == sender_id:
                    username = reactor.get('username')
                    display_name = reactor.get('display_name')
                    break

    return username, display_name

def create_database(db_path: str) -> sqlite3.Connection:
    """Create database with schema"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Drop existing tables if they exist
    cursor.execute("DROP TABLE IF EXISTS reactions")
    cursor.execute("DROP TABLE IF EXISTS messages")
    cursor.execute("DROP VIEW IF EXISTS messages_view")

    # Create messages table
    cursor.execute("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY,
            timestamp INTEGER NOT NULL,
            date_iso TEXT NOT NULL,
            message TEXT,
            is_service BOOLEAN DEFAULT FALSE,
            reply_to_msg_id INTEGER,
            sender_id INTEGER,
            sender_username TEXT,
            sender_display_name TEXT,
            has_media BOOLEAN DEFAULT FALSE,
            reactions_count INTEGER DEFAULT 0,
            reactions_json TEXT,
            views INTEGER,
            forwards INTEGER,
            post_author TEXT,
            from_scheduled BOOLEAN,
            via_bot_id INTEGER,
            mentions TEXT,
            permalink TEXT NOT NULL,
            FOREIGN KEY (reply_to_msg_id) REFERENCES messages(id)
        )
    """)

    # Create reactions table
    cursor.execute("""
        CREATE TABLE reactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT,
            display_name TEXT,
            reaction TEXT NOT NULL,
            peer_type TEXT DEFAULT 'user',
            FOREIGN KEY (message_id) REFERENCES messages(id),
            UNIQUE(message_id, user_id, reaction)
        )
    """)

    # Create indexes
    cursor.execute("CREATE INDEX idx_messages_timestamp ON messages(timestamp)")
    cursor.execute("CREATE INDEX idx_messages_date ON messages(date_iso)")
    cursor.execute("CREATE INDEX idx_messages_sender ON messages(sender_id)")
    cursor.execute("CREATE INDEX idx_messages_reply ON messages(reply_to_msg_id)")
    cursor.execute("CREATE INDEX idx_messages_has_media ON messages(has_media)")
    cursor.execute("CREATE INDEX idx_reactions_message ON reactions(message_id)")
    cursor.execute("CREATE INDEX idx_reactions_user ON reactions(user_id)")
    cursor.execute("CREATE INDEX idx_reactions_type ON reactions(reaction)")

    # Create view for easier querying
    cursor.execute("""
        CREATE VIEW messages_view AS
        SELECT
            id,
            datetime(timestamp, 'unixepoch') as datetime,
            date_iso,
            message,
            is_service,
            reply_to_msg_id,
            sender_id,
            sender_username,
            sender_display_name,
            has_media,
            reactions_count,
            reactions_json,
            permalink
        FROM messages
    """)

    conn.commit()
    return conn

def process_message(message: Dict[str, Any]) -> tuple:
    """Process a single message for insertion"""
    timestamp = parse_iso_to_timestamp(message['date'])
    username, display_name = extract_sender_info(message)

    # Calculate total reactions count
    reactions_count = sum(message.get('reactions_counts', {}).values())

    # Convert reactions_counts to JSON string
    reactions_json = json.dumps(message.get('reactions_counts', {})) if message.get('reactions_counts') else None

    # Convert mentions to JSON string
    mentions_json = json.dumps(message.get('mentions', [])) if message.get('mentions') else None

    return (
        message['id'],
        timestamp,
        message['date'],
        message.get('message', ''),
        message.get('is_service', False),
        message.get('reply_to_msg_id'),
        message.get('sender_id'),
        username,
        display_name,
        message.get('has_media', False),
        reactions_count,
        reactions_json,
        message.get('views'),
        message.get('forwards'),
        message.get('post_author'),
        message.get('from_scheduled'),
        message.get('via_bot_id'),
        mentions_json,
        message['permalink']
    )

def process_reactions(message: Dict[str, Any]) -> List[tuple]:
    """Extract reactions for separate insertion"""
    reactions = []
    if 'reactors' in message and message['reactors']:
        for reactor in message['reactors']:
            reactions.append((
                message['id'],
                reactor['peer_id'],
                reactor.get('username'),
                reactor.get('display_name'),
                reactor['reaction'],
                reactor.get('peer_type', 'user')
            ))
    return reactions

def convert_jsonl_to_sqlite(jsonl_path: str, db_path: str, batch_size: int = 1000):
    """Main conversion function"""
    # Check if input file exists
    if not Path(jsonl_path).exists():
        print(f"Error: Input file '{jsonl_path}' not found")
        sys.exit(1)

    print(f"Converting {jsonl_path} to {db_path}...")

    # Create database
    conn = create_database(db_path)
    cursor = conn.cursor()

    # Process messages
    messages_batch = []
    reactions_batch = []
    total_messages = 0
    total_reactions = 0

    with open(jsonl_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            if line.strip():
                try:
                    message = json.loads(line)

                    # Process message
                    messages_batch.append(process_message(message))

                    # Process reactions
                    reactions = process_reactions(message)
                    reactions_batch.extend(reactions)

                    # Insert in batches
                    if len(messages_batch) >= batch_size:
                        cursor.executemany(
                            """INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            messages_batch
                        )

                        if reactions_batch:
                            cursor.executemany(
                                """INSERT OR IGNORE INTO reactions (message_id, user_id, username, display_name, reaction, peer_type)
                                   VALUES (?,?,?,?,?,?)""",
                                reactions_batch
                            )
                            total_reactions += len(reactions_batch)

                        conn.commit()
                        total_messages += len(messages_batch)
                        print(f"Processed {total_messages} messages, {total_reactions} reactions...")

                        messages_batch = []
                        reactions_batch = []

                except json.JSONDecodeError as e:
                    print(f"Error parsing JSON on line {line_num}: {e}")
                except Exception as e:
                    print(f"Error processing line {line_num}: {e}")

    # Insert remaining messages
    if messages_batch:
        cursor.executemany(
            """INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            messages_batch
        )

        if reactions_batch:
            cursor.executemany(
                """INSERT OR IGNORE INTO reactions (message_id, user_id, username, display_name, reaction, peer_type)
                   VALUES (?,?,?,?,?,?)""",
                reactions_batch
            )
            total_reactions += len(reactions_batch)

        conn.commit()
        total_messages += len(messages_batch)

    # Print statistics
    cursor.execute("SELECT COUNT(*) FROM messages")
    message_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM reactions")
    reaction_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT sender_id) FROM messages WHERE sender_id IS NOT NULL")
    unique_senders = cursor.fetchone()[0]

    cursor.execute("SELECT MIN(datetime(timestamp, 'unixepoch')), MAX(datetime(timestamp, 'unixepoch')) FROM messages")
    date_range = cursor.fetchone()

    print("\n" + "="*50)
    print("Conversion completed successfully!")
    print(f"Total messages: {message_count:,}")
    print(f"Total reactions: {reaction_count:,}")
    print(f"Unique senders: {unique_senders:,}")
    print(f"Date range: {date_range[0]} to {date_range[1]}")
    print(f"Database saved to: {db_path}")
    print("="*50)

    # Example queries
    print("\nExample queries you can run:")
    print("1. Messages by date: SELECT * FROM messages WHERE timestamp >= strftime('%s', '2024-01-01') AND timestamp < strftime('%s', '2024-02-01')")
    print("2. Most active users: SELECT sender_id, sender_display_name, COUNT(*) as msg_count FROM messages WHERE NOT is_service GROUP BY sender_id ORDER BY msg_count DESC LIMIT 10")
    print("3. Most reacted messages: SELECT id, message, reactions_count FROM messages WHERE reactions_count > 0 ORDER BY reactions_count DESC LIMIT 10")
    print("4. Messages with media: SELECT * FROM messages_view WHERE has_media = 1")

    conn.close()

if __name__ == "__main__":
    # Configuration
    JSONL_FILE = "export_dir/messages.jsonl"
    SQLITE_DB = "telegram_messages.db"

    # Allow command line arguments
    if len(sys.argv) > 1:
        JSONL_FILE = sys.argv[1]
    if len(sys.argv) > 2:
        SQLITE_DB = sys.argv[2]

    convert_jsonl_to_sqlite(JSONL_FILE, SQLITE_DB)