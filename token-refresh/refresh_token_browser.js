#!/usr/bin/env node
/**
 * Скрипт для автоматичного оновлення OAuth токена Claude через headless браузер.
 * Обходить Cloudflare JavaScript challenge використовуючи Puppeteer.
 *
 * Запускається по cron кожну годину.
 */

const fs = require('fs');
const path = require('path');
const puppeteer = require('puppeteer');

// Конфігурація
const CREDENTIALS_FILE = process.env.CREDENTIALS_FILE || '/home/node/.claude/.credentials.json';
const CLIENT_ID = '9d1c250a-e61b-44d9-88ed-5944d1962f5e';
const TOKEN_URL = 'https://console.anthropic.com/api/oauth/token';
const REFRESH_THRESHOLD_MS = 30 * 60 * 1000; // 30 хвилин до закінчення

function log(message) {
    console.log(`[token-refresh] ${new Date().toISOString()} ${message}`);
}

function error(message) {
    console.error(`[token-refresh] ${new Date().toISOString()} ERROR: ${message}`);
}

async function loadCredentials() {
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

async function refreshTokenWithBrowser(refreshToken) {
    log('Starting headless browser for token refresh...');

    const browser = await puppeteer.launch({
        headless: 'new',
        args: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--disable-accelerated-2d-canvas',
            '--disable-gpu',
            '--window-size=1920x1080'
        ]
    });

    try {
        const page = await browser.newPage();

        // Встановлюємо реалістичний User-Agent
        await page.setUserAgent(
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
            '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        );

        // Емулюємо реальний браузер
        await page.setViewport({ width: 1920, height: 1080 });
        await page.setExtraHTTPHeaders({
            'Accept-Language': 'en-US,en;q=0.9'
        });

        // Спочатку відвідуємо головну сторінку для отримання cookies
        log('Visiting main page to get cookies...');
        await page.goto('https://console.anthropic.com', {
            waitUntil: 'networkidle0',
            timeout: 30000
        });

        // Чекаємо поки Cloudflare пропустить (до 10 секунд)
        await new Promise(resolve => setTimeout(resolve, 3000));

        // Тепер робимо POST запит для оновлення токена
        log('Sending token refresh request...');

        const response = await page.evaluate(async (url, clientId, refreshToken) => {
            try {
                const res = await fetch(url, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        grant_type: 'refresh_token',
                        refresh_token: refreshToken,
                        client_id: clientId
                    })
                });

                const data = await res.json();
                return { ok: res.ok, status: res.status, data };
            } catch (err) {
                return { ok: false, error: err.message };
            }
        }, TOKEN_URL, CLIENT_ID, refreshToken);

        if (!response.ok) {
            throw new Error(`Token refresh failed: ${JSON.stringify(response)}`);
        }

        if (!response.data.access_token) {
            throw new Error(`No access_token in response: ${JSON.stringify(response.data)}`);
        }

        return response.data;

    } finally {
        await browser.close();
    }
}

async function main() {
    try {
        // Завантажуємо credentials
        const credentials = await loadCredentials();

        if (!credentials.claudeAiOauth?.refreshToken) {
            throw new Error('No refresh token found in credentials');
        }

        // Перевіряємо чи потрібно оновлювати
        if (!isTokenExpiringSoon(credentials)) {
            process.exit(0);
        }

        // Оновлюємо токен через браузер
        const tokenData = await refreshTokenWithBrowser(credentials.claudeAiOauth.refreshToken);

        // Оновлюємо credentials
        credentials.claudeAiOauth.accessToken = tokenData.access_token;

        // Розраховуємо новий час закінчення
        const expiresIn = tokenData.expires_in || 28800; // 8 годин за замовчуванням
        credentials.claudeAiOauth.expiresAt = Date.now() + (expiresIn * 1000);

        // Оновлюємо refresh token якщо отримали новий
        if (tokenData.refresh_token) {
            credentials.claudeAiOauth.refreshToken = tokenData.refresh_token;
        }

        // Зберігаємо
        saveCredentials(credentials);

        const expiryDate = new Date(credentials.claudeAiOauth.expiresAt).toISOString();
        log(`Token refreshed successfully. New expiry: ${expiryDate}`);

    } catch (err) {
        error(err.message);
        process.exit(1);
    }
}

main();
