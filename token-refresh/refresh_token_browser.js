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

        // Перехватываем запросы для отправки POST
        await page.setRequestInterception(true);

        let tokenResponse = null;

        page.on('request', async (request) => {
            if (request.url() === TOKEN_URL && request.method() === 'POST') {
                // Это наш запрос на обновление токена - пропускаем
                request.continue();
            } else if (request.url() === TOKEN_URL) {
                // Модифицируем GET запрос в POST для обновления токена
                request.continue({
                    method: 'POST',
                    postData: JSON.stringify({
                        grant_type: 'refresh_token',
                        refresh_token: refreshToken,
                        client_id: CLIENT_ID
                    }),
                    headers: {
                        ...request.headers(),
                        'Content-Type': 'application/json'
                    }
                });
            } else {
                request.continue();
            }
        });

        page.on('response', async (response) => {
            if (response.url() === TOKEN_URL) {
                try {
                    tokenResponse = await response.json();
                    log(`Got token response: ${response.status()}`);
                } catch (e) {
                    log(`Failed to parse token response: ${e.message}`);
                }
            }
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
        const pageContent = await page.content();
        if (pageContent.includes('Just a moment') || pageContent.includes('Checking your browser')) {
            log('Cloudflare challenge detected, waiting more...');
            await new Promise(resolve => setTimeout(resolve, 10000));
        }

        // Теперь делаем POST запрос для обновления токена
        log('Sending token refresh request...');

        // Переходим на URL токена - запрос будет перехвачен и модифицирован
        await page.goto(TOKEN_URL, {
            waitUntil: 'networkidle2',
            timeout: 30000
        }).catch(() => {
            // Игнорируем ошибку навигации - нам нужен только ответ
        });

        // Ждём ответа
        await new Promise(resolve => setTimeout(resolve, 2000));

        if (!tokenResponse) {
            throw new Error('No response received from token endpoint');
        }

        if (tokenResponse.error) {
            throw new Error(`Token refresh failed: ${tokenResponse.error_description || tokenResponse.error}`);
        }

        if (!tokenResponse.access_token) {
            throw new Error(`No access_token in response: ${JSON.stringify(tokenResponse)}`);
        }

        return tokenResponse;

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
