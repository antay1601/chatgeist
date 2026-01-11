#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
tg_dump_with_reactions.py
Экспорт истории чата/канала Telegram с реакциями (и списком реакторов по желанию).

Формат вывода: JSONL (1 сообщение = 1 JSON-объект).
Опции:
  --with-reactors        выгружать полный список пользователей, кто поставил реакцию к каждому сообщению
  --download-media       скачивать медиа (в папку out/media)
  --from-date / --to-date    ISO-даты (YYYY-MM-DD) для фильтра
  --limit                максимум сообщений (если хотите не всё)
  --session              имя файла сессии (по умолчанию tg_export)
Переменные окружения для API ключей (если не передавать через аргументы):
  TELEGRAM_API_ID, TELEGRAM_API_HASH
"""

import argparse
import asyncio
import json
import dotenv
dotenv.load_dotenv()
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Union

from telethon import TelegramClient, errors, functions, types
from telethon.tl.functions.messages import GetMessageReactionsListRequest

def parse_args():
    p = argparse.ArgumentParser(description="Export Telegram chat with reactions (Telethon MTProto).")
    p.add_argument("--api-id", type=int, default=int(os.getenv("TELEGRAM_API_ID", "0")), help="Telegram api_id")
    p.add_argument("--api-hash", type=str, default=os.getenv("TELEGRAM_API_HASH", ""), help="Telegram api_hash")
    p.add_argument("--session", type=str, default="tg_export", help="Session file name")
    p.add_argument("--chat", type=str, required=True, help="@username, t.me/link или numeric id")
    p.add_argument("--out", type=Path, default=Path("export"), help="Выходная папка")
    p.add_argument("--with-reactors", action="store_true", help="Собирать полный список пользователей, кто ставил реакции")
    p.add_argument("--download-media", action="store_true", help="Скачивать медиафайлы")
    p.add_argument("--from-date", type=str, default=None, help="YYYY-MM-DD")
    p.add_argument("--to-date", type=str, default=None, help="YYYY-MM-DD")
    p.add_argument("--limit", type=int, default=None, help="Ограничить число сообщений")
    p.add_argument("--min-id", type=int, default=0, help="Минимальный ID сообщения (для инкрементального обновления)")
    return p.parse_args()

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def reaction_to_str(r: Union[types.ReactionEmoji, types.ReactionCustomEmoji, Any]) -> str:
    # Нормализуем реакцию в читаемую строку
    if isinstance(r, types.ReactionEmoji):
        return r.emoticon  # обычный юникод-эмодзи, например "❤️"
    if isinstance(r, types.ReactionCustomEmoji):
        return f"custom:{r.document_id}"  # кастомный эмодзи (стикер-эмодзи)
    return str(r)

async def fetch_reactors_for_message(client: TelegramClient, peer, msg_id: int) -> List[Dict[str, Any]]:
    """
    Возвращает список: [{peer_type, peer_id, username, display_name, reaction, is_channel, is_user}]
    """
    reactors: List[Dict[str, Any]] = []
    offset = ""
    while True:
        r = await client(GetMessageReactionsListRequest(peer=peer, id=msg_id, limit=200, offset=offset))
        # Соберём быстрые словари для пользователей и чатов из ответа
        users_map: Dict[int, types.User] = {u.id: u for u in r.users}
        chats_map: Dict[int, Any] = {}
        for ch in r.chats:
            # ch может быть Chat или Channel
            try:
                chats_map[ch.id] = ch
            except Exception:
                pass

        for pr in r.reactions:  # types.MessagePeerReaction
            # Кто поставил реакцию
            uname = None
            display = None
            peer_type = None
            peer_id_val: Optional[int] = None

            if isinstance(pr.peer_id, types.PeerUser):
                peer_type = "user"
                peer_id_val = pr.peer_id.user_id
                u = users_map.get(peer_id_val)
                if u:
                    uname = u.username
                    display = (u.first_name or "") + (" " + u.last_name if u.last_name else "")
                    display = display.strip() or uname or str(peer_id_val)
            elif isinstance(pr.peer_id, types.PeerChannel):
                peer_type = "channel"
                peer_id_val = pr.peer_id.channel_id
                ch = chats_map.get(peer_id_val)
                if ch:
                    uname = getattr(ch, "username", None)
                    display = getattr(ch, "title", None) or uname or str(peer_id_val)
            elif isinstance(pr.peer_id, types.PeerChat):
                peer_type = "chat"
                peer_id_val = pr.peer_id.chat_id
                ch = chats_map.get(peer_id_val)
                if ch:
                    uname = getattr(ch, "username", None)
                    display = getattr(ch, "title", None) or uname or str(peer_id_val)

            reactors.append(
                {
                    "peer_type": peer_type,
                    "peer_id": peer_id_val,
                    "username": uname,
                    "display_name": display,
                    "reaction": reaction_to_str(pr.reaction),
                }
            )

        if not r.next_offset:
            break
        offset = r.next_offset
    return reactors

def msg_basic_dict(m: types.Message, sender_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    # Базовые поля сообщения (без тяжёлых сетевых вызовов)
    d: Dict[str, Any] = {
        "id": m.id,
        "date": m.date.astimezone(timezone.utc).isoformat(),
        "message": m.message or "",
        "is_service": m.action is not None,
        "reply_to_msg_id": getattr(m, "reply_to_msg_id", None),
        "views": getattr(m, "views", None),
        "forwards": getattr(m, "forwards", None),
        "post_author": getattr(m, "post_author", None),
        "sender_id": getattr(m, "sender_id", None),
        "sender_username": None,
        "sender_display_name": None,
        "from_scheduled": getattr(m, "from_scheduled", False),
        "via_bot_id": getattr(m, "via_bot_id", None),
        "mentions": [getattr(e, "user_id", None) for e in (m.entities or []) if getattr(e, "user_id", None)],
        "has_media": m.media is not None,
        "reactions_counts": {},   # заполним ниже
    }
    # Добавляем информацию об отправителе если есть
    if sender_info:
        d["sender_username"] = sender_info.get("username")
        d["sender_display_name"] = sender_info.get("display_name")
    # Сводные счётчики реакций из Message.reactions
    if m.reactions and getattr(m.reactions, "results", None):
        rc = {}
        for item in m.reactions.results:  # list[types.ReactionCount]
            rc[reaction_to_str(item.reaction)] = item.count
        d["reactions_counts"] = rc
    return d


async def get_sender_info(client: TelegramClient, sender_id: Optional[int], cache: Dict[int, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Получает информацию об отправителе (username, display_name).
    Использует кэш для избежания повторных запросов.
    """
    if sender_id is None:
        return None

    # Проверяем кэш
    if sender_id in cache:
        return cache[sender_id]

    try:
        entity = await client.get_entity(sender_id)

        if isinstance(entity, types.User):
            username = entity.username
            display_name = (entity.first_name or "") + (" " + entity.last_name if entity.last_name else "")
            display_name = display_name.strip() or username or str(sender_id)
        elif isinstance(entity, (types.Channel, types.Chat)):
            username = getattr(entity, "username", None)
            display_name = getattr(entity, "title", None) or username or str(sender_id)
        else:
            username = None
            display_name = str(sender_id)

        info = {"username": username, "display_name": display_name}
        cache[sender_id] = info
        return info

    except (errors.RPCError, ValueError) as e:
        # Пользователь удалён или недоступен
        info = {"username": None, "display_name": str(sender_id)}
        cache[sender_id] = info
        return info

async def maybe_download_media(client: TelegramClient, msg: types.Message, media_dir: Path) -> Optional[str]:
    if not msg.media:
        return None
    try:
        ensure_dir(media_dir)
        # имя файла: <msgId>_<type>
        fname = await msg.download_media(file=media_dir / f"{msg.id}")
        return str(fname) if fname else None
    except Exception as e:
        return f"ERROR: {e}"

async def dump_chat_history(
    client: TelegramClient,
    chat_target: str,
    out_dir: Path,
    limit: Optional[int] = None,
    min_id: int = 0,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    with_reactors: bool = False,
    download_media: bool = False
) -> Dict[str, Any]:
    """
    Скачивает историю чата и сохраняет в JSONL.

    Эта функция может быть импортирована и вызвана из другого скрипта.

    Args:
        client: Инициализированный TelegramClient
        chat_target: @username, t.me/link или numeric ID чата
        out_dir: Папка для сохранения результатов
        limit: Максимум сообщений (None = все)
        min_id: Минимальный ID сообщения (для инкрементального обновления)
        from_date: Начальная дата в формате YYYY-MM-DD
        to_date: Конечная дата в формате YYYY-MM-DD
        with_reactors: Собирать полный список реакторов (медленно)
        download_media: Скачивать медиафайлы

    Returns:
        dict со статистикой: count, skipped, errors, last_id, jsonl_path
    """
    ensure_dir(out_dir)
    media_dir = out_dir / "media"
    jsonl_path = out_dir / "messages.jsonl"
    meta_path = out_dir / "chat_meta.json"

    from_dt = datetime.fromisoformat(from_date) if from_date else None
    to_dt = datetime.fromisoformat(to_date) if to_date else None

    # Разрешим объект чата/канала
    try:
        peer = await client.get_entity(chat_target)
    except ValueError as e:
        print(f"❌ Чат {chat_target} не найден: {e}")
        return {"error": str(e), "count": 0}

    # Метаданные о чате
    me = await client.get_me()
    chat_title = getattr(peer, "title", None) or getattr(peer, "username", None) or str(getattr(peer, "id", ""))

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "exported_at_utc": datetime.now(timezone.utc).isoformat(),
                "self_user_id": me.id,
                "peer_id": getattr(peer, "id", None),
                "chat_title_or_username": chat_title,
                "input": chat_target,
                "with_reactors": with_reactors,
                "download_media": download_media,
                "from_date": from_date,
                "to_date": to_date,
                "limit": limit,
                "min_id": min_id,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    # Режим записи: append если обновление, иначе перезапись
    file_mode = 'a' if min_id > 0 else 'w'
    if file_mode == 'w' and jsonl_path.exists():
        jsonl_path.unlink()  # Удаляем старый файл при полном дампе

    count = 0
    skipped_by_date = 0
    errors_count = 0
    last_id = 0
    sender_cache: Dict[int, Dict[str, Any]] = {}  # Кэш информации об отправителях

    print(f"📥 Дамп: {chat_target} (min_id={min_id}) -> {jsonl_path}")

    # iter_messages с min_id возвращает сообщения с ID > min_id
    async for msg in client.iter_messages(peer, limit=limit, min_id=min_id, reverse=True):
        # Фильтр по дате
        if from_dt and msg.date < from_dt:
            skipped_by_date += 1
            continue
        if to_dt and msg.date > to_dt:
            skipped_by_date += 1
            continue

        # Получаем информацию об отправителе
        sender_info = await get_sender_info(client, msg.sender_id, sender_cache)

        d = msg_basic_dict(msg, sender_info)

        # Полный список реакторов (дорого по API)
        if with_reactors and msg.reactions:
            try:
                reactors = await fetch_reactors_for_message(client, peer, msg.id)
                d["reactors"] = reactors
            except errors.FloodWaitError as e:
                wait_time = int(getattr(e, "seconds", 5)) + 1
                print(f"   ⏳ FloodWait: ждём {wait_time}s...")
                await asyncio.sleep(wait_time)
                try:
                    reactors = await fetch_reactors_for_message(client, peer, msg.id)
                    d["reactors"] = reactors
                except Exception as e2:
                    d["reactors_error"] = str(e2)
                    errors_count += 1
            except errors.RPCError as e:
                d["reactors_error"] = f"RPCError: {e}"
                errors_count += 1
            except Exception as e:
                d["reactors_error"] = str(e)
                errors_count += 1

        # Скачать медиа
        if download_media and msg.media:
            saved = await maybe_download_media(client, msg, media_dir)
            d["media_path"] = saved

        # Ссылка на сообщение
        username = getattr(peer, "username", None)
        if username:
            d["permalink"] = f"https://t.me/{username}/{msg.id}"

        # Пишем в JSONL
        with open(jsonl_path, "a", encoding="utf-8") as w:
            w.write(json.dumps(d, ensure_ascii=False) + "\n")

        count += 1
        last_id = max(last_id, msg.id)

        if count % 500 == 0:
            print(f"   ... {chat_target}: {count} сообщений")

    result = {
        "count": count,
        "skipped_by_date": skipped_by_date,
        "errors": errors_count,
        "last_id": last_id,
        "jsonl_path": str(jsonl_path),
        "meta_path": str(meta_path),
        "chat_title": chat_title,
    }

    print(f"✅ {chat_target}: выгружено {count}, пропущено {skipped_by_date}, ошибок {errors_count}")
    return result


async def main():
    """CLI-обёртка для dump_chat_history"""
    args = parse_args()
    if not args.api_id or not args.api_hash:
        raise SystemExit("Нужны API_ID и API_HASH (передайте через --api-id/--api-hash или переменные окружения TELEGRAM_API_ID/TELEGRAM_API_HASH).")

    client = TelegramClient(args.session, args.api_id, args.api_hash)

    async with client:
        result = await dump_chat_history(
            client=client,
            chat_target=args.chat,
            out_dir=args.out,
            limit=args.limit,
            min_id=args.min_id,
            from_date=args.from_date,
            to_date=args.to_date,
            with_reactors=args.with_reactors,
            download_media=args.download_media,
        )

        if result.get("error"):
            print(f"❌ Ошибка: {result['error']}")
        else:
            print(f"\n📊 Итого:")
            print(f"   Файлы: {result['jsonl_path']}")
            print(f"   Last ID: {result['last_id']}")
            if args.download_media:
                print(f"   Медиа: {args.out / 'media'}")

if __name__ == "__main__":
    asyncio.run(main())
