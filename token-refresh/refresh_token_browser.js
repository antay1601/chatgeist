#!/usr/bin/env node
/**
 * Скрипт для автоматического обновления OAuth токена Claude через headless браузер.
 * Обходит Cloudflare JavaScript challenge используя Puppeteer.
 *
 * Запускается по cron каждый час.
 */

const fs = require('fs');
const puppeteer = require('puppeteer');

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

        // Устанавливаем реалистичный User-Agent
        await page.setUserAgent(
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
            '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        );

        // Эмулируем реальный браузер
        await page.setViewport({ width: 1920, height: 1080 });
        await page.setExtraHTTPHeaders({
            'Accept-Language': 'en-US,en;q=0.9'
        });

        // Сначала посещаем главную страницу для получения cookies и обхода Cloudflare
        log('Visiting main page to get cookies...');
        await page.goto('https://console.anthropic.com', {
            waitUntil: 'networkidle2',
            timeout: 60000
        });

        // Ждём пока Cloudflare пропустит
        log('Waiting for Cloudflare...');
        await new Promise(resolve => setTimeout(resolve, 5000));

        // Проверяем, прошли ли Cloudflare (ищем признаки challenge)
        let pageContent = await page.content();
        if (pageContent.includes('Just a moment') || pageContent.includes('Checking your browser')) {
            log('Cloudflare challenge detected, waiting more...');
            await new Promise(resolve => setTimeout(resolve, 10000));
            pageContent = await page.content();
        }

        // Проверяем что мы прошли Cloudflare
        if (pageContent.includes('Just a moment')) {
            throw new Error('Failed to pass Cloudflare challenge');
        }

        log('Cloudflare passed, sending token refresh request...');

        // Используем CDP для выполнения fetch запроса напрямую
        const client = await page.target().createCDPSession();

        // Делаем POST запрос через page.evaluate после прохождения Cloudflare
        const response = await page.evaluate(async (url, clientId, token) => {
            try {
                const res = await fetch(url, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        grant_type: 'refresh_token',
                        refresh_token: token,
                        client_id: clientId
                    }),
                    credentials: 'include'
                });

                const text = await res.text();
                try {
                    const data = JSON.parse(text);
                    return { ok: res.ok, status: res.status, data };
                } catch (e) {
                    return { ok: false, status: res.status, error: `Invalid JSON: ${text.substring(0, 200)}` };
                }
            } catch (err) {
                return { ok: false, error: err.message };
            }
        }, TOKEN_URL, CLIENT_ID, refreshToken);

        log(`Token response: status=${response.status}, ok=${response.ok}`);

        if (!response.ok) {
            const errorMsg = response.error || response.data?.error_description || response.data?.error || JSON.stringify(response);
            throw new Error(`Token refresh failed: ${errorMsg}`);
        }

        if (!response.data?.access_token) {
            throw new Error(`No access_token in response: ${JSON.stringify(response.data)}`);
        }

        return response.data;

    } finally {
        await browser.close();
    }
}

async function main() {
    try {
        // Загружаем credentials
        const credentials = await loadCredentials();

        if (!credentials.claudeAiOauth?.refreshToken) {
            throw new Error('No refresh token found in credentials');
        }

        // Проверяем нужно ли обновлять
        if (!isTokenExpiringSoon(credentials)) {
            process.exit(0);
        }

        // Обновляем токен через браузер
        const tokenData = await refreshTokenWithBrowser(credentials.claudeAiOauth.refreshToken);

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
