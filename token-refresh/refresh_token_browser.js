#!/usr/bin/env node
/**
 * Скрипт для автоматического обновления OAuth токена Claude.
 * Использует curl для обхода Cloudflare (следует редиректам).
 *
 * Запускается по cron каждый час.
 */

const fs = require('fs');
const { execSync } = require('child_process');

// Конфигурация
const CREDENTIALS_FILE = process.env.CREDENTIALS_FILE || '/home/node/.claude/.credentials.json';
const CLIENT_ID = '9d1c250a-e61b-44d9-88ed-5944d1962f5e';
const TOKEN_URL = 'https://console.anthropic.com/api/oauth/token';
const REFRESH_THRESHOLD_MS = 30 * 60 * 1000; // 30 минут до истечения access_token
const REFRESH_TOKEN_WARN_DAYS = 25; // Предупреждать за 5 дней до истечения refresh_token (~30 дней)
const REFRESH_TOKEN_MAX_DAYS = 30; // Максимальный срок жизни refresh_token

function log(message) {
    console.log(`[token-refresh] ${new Date().toISOString()} ${message}`);
}

function warn(message) {
    console.warn(`[token-refresh] ${new Date().toISOString()} WARNING: ${message}`);
}

function error(message) {
    console.error(`[token-refresh] ${new Date().toISOString()} ERROR: ${message}`);
}

function loadCredentials() {
    try {
        const content = fs.readFileSync(CREDENTIALS_FILE, 'utf8');
        return JSON.parse(content);
    } catch (err) {
        throw new Error(`Failed to load credentials: ${err.message}`);
    }
}

function saveCredentials(credentials) {
    fs.writeFileSync(CREDENTIALS_FILE, JSON.stringify(credentials, null, 2));
}

function isTokenExpiringSoon(credentials) {
    const expiresAt = credentials.claudeAiOauth?.expiresAt || 0;
    const currentTime = Date.now();
    const timeLeft = expiresAt - currentTime;

    if (timeLeft > REFRESH_THRESHOLD_MS) {
        const minutesLeft = Math.floor(timeLeft / 60000);
        log(`Token still valid for ${minutesLeft} minutes, skipping refresh`);
        return false;
    }

    log(`Token expires soon (${timeLeft}ms left), refreshing...`);
    return true;
}

function checkRefreshTokenAge(credentials) {
    const lastSyncedAt = credentials.claudeAiOauth?.lastSyncedAt;

    if (!lastSyncedAt) {
        warn('lastSyncedAt not set. Run sync_to_server.sh to track refresh_token age.');
        return;
    }

    const daysSinceSync = (Date.now() - lastSyncedAt) / (1000 * 60 * 60 * 24);
    const daysLeft = REFRESH_TOKEN_MAX_DAYS - daysSinceSync;

    if (daysSinceSync >= REFRESH_TOKEN_MAX_DAYS) {
        error(`REFRESH TOKEN EXPIRED! Last synced ${Math.floor(daysSinceSync)} days ago.`);
        error('Run sync_to_server.sh from Mac immediately!');
    } else if (daysSinceSync >= REFRESH_TOKEN_WARN_DAYS) {
        warn(`Refresh token expires in ~${Math.floor(daysLeft)} days (synced ${Math.floor(daysSinceSync)} days ago).`);
        warn('Plan to run sync_to_server.sh from Mac soon.');
    } else {
        log(`Refresh token OK. Synced ${Math.floor(daysSinceSync)} days ago, ~${Math.floor(daysLeft)} days left.`);
    }
}

function refreshToken(refreshToken) {
    log('Sending token refresh request via curl...');

    const postData = JSON.stringify({
        grant_type: 'refresh_token',
        refresh_token: refreshToken,
        client_id: CLIENT_ID
    });

    try {
        // Используем curl с -L для следования редиректам (обходит Cloudflare 302)
        const result = execSync(`curl -sL -X POST '${TOKEN_URL}' -H 'Content-Type: application/json' -d '${postData.replace(/'/g, "'\"'\"'")}'`, {
            encoding: 'utf8',
            timeout: 30000
        });

        const response = JSON.parse(result);

        if (response.error) {
            const errorMsg = response.error_description || (typeof response.error === 'string' ? response.error : JSON.stringify(response.error));
            throw new Error(`Token refresh failed: ${errorMsg}`);
        }

        if (!response.access_token) {
            throw new Error(`No access_token in response: ${result}`);
        }

        return response;

    } catch (err) {
        if (err.message.includes('Token refresh failed') || err.message.includes('No access_token')) {
            throw err;
        }
        throw new Error(`curl failed: ${err.message}`);
    }
}

function main() {
    try {
        // Загружаем credentials
        const credentials = loadCredentials();

        if (!credentials.claudeAiOauth?.refreshToken) {
            throw new Error('No refresh token found in credentials');
        }

        // Проверяем возраст refresh_token
        checkRefreshTokenAge(credentials);

        // Проверяем нужно ли обновлять access_token
        if (!isTokenExpiringSoon(credentials)) {
            process.exit(0);
        }

        // Обновляем токен
        const tokenData = refreshToken(credentials.claudeAiOauth.refreshToken);

        // Обновляем credentials
        credentials.claudeAiOauth.accessToken = tokenData.access_token;

        // Рассчитываем новое время истечения
        const expiresIn = tokenData.expires_in || 28800; // 8 часов по умолчанию
        credentials.claudeAiOauth.expiresAt = Date.now() + (expiresIn * 1000);

        // Обновляем refresh token если получили новый
        if (tokenData.refresh_token) {
            credentials.claudeAiOauth.refreshToken = tokenData.refresh_token;
            // Сбрасываем счётчик возраста при получении нового refresh_token
            credentials.claudeAiOauth.lastSyncedAt = Date.now();
        }

        // Сохраняем
        saveCredentials(credentials);

        const expiryDate = new Date(credentials.claudeAiOauth.expiresAt).toISOString();
        log(`Token refreshed successfully. New expiry: ${expiryDate}`);

    } catch (err) {
        // Специальная обработка истёкшего refresh_token
        if (err.message.includes('not_found') || err.message.includes('Not Found')) {
            error('REFRESH TOKEN EXPIRED!');
            error('The refresh_token is no longer valid.');
            error('ACTION REQUIRED: Run sync_to_server.sh from Mac to get new tokens.');
        } else {
            error(err.message);
        }
        process.exit(1);
    }
}

main();
