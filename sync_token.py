#!/usr/bin/env python3
"""
Синхронизирует OAuth токен из macOS Keychain в .env для Docker.
Запускать перед стартом бота или по cron.

1. Делает ping Claude CLI чтобы триггернуть обновление токена
2. Копирует свежий токен из Keychain в .env
3. Опционально перезапускает Docker
"""

import subprocess
import json
import sys
import time


def ping_claude():
    """Пингует Claude CLI чтобы триггернуть обновление токена если нужно."""
    try:
        subprocess.run(
            ['claude', '--print', 'ping'],
            capture_output=True, text=True, timeout=60
        )
        print("✅ Claude CLI ping OK")
    except Exception as e:
        print(f"⚠️ Claude ping failed: {e}")


def get_token_from_keychain():
    """Получает токен из macOS Keychain."""
    result = subprocess.run(
        ['security', 'find-generic-password', '-s', 'Claude Code-credentials', '-w'],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return None

    try:
        data = json.loads(result.stdout)
        return data.get('claudeAiOauth', {}).get('accessToken')
    except json.JSONDecodeError:
        return None


def update_env_file(token):
    """Обновляет токен в .env файле."""
    try:
        with open('.env', 'r') as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []

    found = False
    new_lines = []
    for line in lines:
        if line.startswith('CLAUDE_CODE_OAUTH_TOKEN='):
            new_lines.append(f'CLAUDE_CODE_OAUTH_TOKEN={token}\n')
            found = True
        else:
            new_lines.append(line)

    if not found:
        new_lines.append(f'CLAUDE_CODE_OAUTH_TOKEN={token}\n')

    with open('.env', 'w') as f:
        f.writelines(new_lines)


def restart_docker():
    """Перезапускает Docker контейнер (down + up чтобы перечитать .env)."""
    subprocess.run(['docker', 'compose', 'down'], capture_output=True)
    subprocess.run(['docker', 'compose', 'up', '-d'], capture_output=True)


def main():
    # Сначала пингуем Claude чтобы обновить токен если нужно
    ping_claude()

    # Небольшая пауза чтобы Keychain обновился
    time.sleep(2)

    token = get_token_from_keychain()

    if not token:
        print("❌ Токен не найден в Keychain")
        sys.exit(1)

    update_env_file(token)
    print("✅ Токен синхронизирован в .env")

    # Перезапуск Docker если передан флаг
    if '--restart' in sys.argv:
        restart_docker()
        print("✅ Docker контейнер перезапущен")


if __name__ == "__main__":
    main()
