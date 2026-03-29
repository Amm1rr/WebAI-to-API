// src/static/js/utils.js - Shared API wrapper and helpers

const api = {
    async get(url) {
        const res = await fetch(url);
        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: res.statusText }));
            throw err;
        }
        return res.json();
    },

    async post(url, body) {
        const res = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: res.statusText }));
            throw err;
        }
        return res.json();
    },
};

function showResult(el, type, message) {
    el.textContent = message;
    el.className = `result-box ${type}`;
    el.classList.remove("hidden");
}

function showInline(el, message, isError) {
    el.textContent = message;
    el.style.color = isError ? "var(--error)" : "var(--success)";
    setTimeout(() => { el.textContent = ""; }, 3000);
}

function escapeHtml(text) {
    const d = document.createElement("div");
    d.textContent = text;
    return d.innerHTML;
}

/**
 * Build a user-friendly error message with actionable guidance based on error_code.
 */
function buildErrorMessage(data) {
    const code = data.error_code;
    const detail = data.error_detail || "";
    const hints = {
        auth_expired: {
            title: "Cookie expired or invalid",
            steps: [
                "1. Open Chrome \u2192 go to gemini.google.com \u2192 make sure you are logged in",
                "2. Open DevTools (F12) \u2192 Network tab",
                "3. Reload the page, right-click any request \u2192 Copy as cURL",
                "4. Paste it in the Configuration tab \u2192 Import Cookies",
            ],
            note: "Cookies (especially __Secure-1PSIDTS) expire frequently. You may need to repeat this every few hours.",
        },
        no_cookies: {
            title: "No cookies configured",
            steps: [
                "1. Go to the Configuration tab",
                "2. Paste a cURL from gemini.google.com DevTools \u2192 Import Cookies",
            ],
            note: null,
        },
        network: {
            title: "Cannot reach gemini.google.com",
            steps: [
                "Check your internet connection or proxy settings.",
                "If behind a firewall, configure the proxy in the Configuration tab.",
            ],
            note: detail || null,
        },
        disabled: {
            title: "Gemini is disabled in configuration",
            steps: ["Enable Gemini in config.conf under [EnabledAI] section."],
            note: null,
        },
    };
    const info = hints[code] || {
        title: "Connection failed",
        steps: [detail || "Check the Logs tab for details."],
        note: null,
    };
    let html = `<strong>${escapeHtml(info.title)}</strong>`;
    html += `<ul class="error-steps">`;
    info.steps.forEach(s => { html += `<li>${escapeHtml(s)}</li>`; });
    html += `</ul>`;
    if (info.note) {
        html += `<div class="error-note">${escapeHtml(info.note)}</div>`;
    }
    return html;
}

function showResultHtml(el, type, html) {
    el.innerHTML = html;
    el.className = `result-box ${type}`;
    el.classList.remove("hidden");
}
