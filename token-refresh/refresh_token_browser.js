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
const REFRESH_THRESHOLD_MS = 30 * 60 * 1000; // 30 минут до истечения

function log(message) {
    console.log(`[token-refresh] ${new Date().toISOString()} ${message}`);
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

        // Проверяем нужно ли обновлять
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
        }

        // Сохраняем
        saveCredentials(credentials);

        const expiryDate = new Date(credentials.claudeAiOauth.expiresAt).toISOString();
        log(`Token refreshed successfully. New expiry: ${expiryDate}`);

    } catch (err) {
        error(err.message);
        process.exit(1);
    }
}

main();
