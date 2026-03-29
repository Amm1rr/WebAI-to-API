// src/static/js/config.js - Configuration tab logic

const Config = {
    init() {
        // cURL import
        document.getElementById("btn-curl-import").addEventListener("click", () => this.handleCurlImport());

        // Manual cookies
        document.getElementById("btn-manual-cookies").addEventListener("click", () => this.handleManualCookies());

        // Model save
        document.getElementById("btn-save-model").addEventListener("click", () => this.handleModelSave());

        // Proxy save
        document.getElementById("btn-save-proxy").addEventListener("click", () => this.handleProxySave());

        // Telegram
        document.getElementById("btn-save-telegram").addEventListener("click", () => this.handleTelegramSave());
        document.getElementById("btn-test-telegram").addEventListener("click", () => this.handleTelegramTest());

    },

    activate() {
        this.refresh();
    },

    deactivate() {},

    async refresh() {
        try {
            const data = await api.get("/api/admin/config");
            this.updateDisplay(data);
        } catch {
            // Ignore on error
        }
        try {
            const tg = await api.get("/api/admin/config/telegram");
            this.updateTelegramDisplay(tg);
        } catch {
            // Ignore on error
        }
    },

    updateDisplay(data) {
        // Cookie status
        const badge = document.getElementById("cookie-status-badge");
        badge.textContent = data.cookies_set ? "Configured" : "Not Set";
        badge.className = "status-badge " + (data.cookies_set ? "connected" : "disconnected");

        document.getElementById("cookie-1psid-preview").textContent = data.cookie_1psid_preview || "Not set";
        document.getElementById("cookie-1psidts-preview").textContent = data.cookie_1psidts_preview || "Not set";

        // Model dropdown
        const select = document.getElementById("model-select");
        select.innerHTML = "";
        (data.available_models || []).forEach(m => {
            const opt = document.createElement("option");
            opt.value = m;
            opt.textContent = m;
            if (m === data.model) opt.selected = true;
            select.appendChild(opt);
        });

        // Proxy
        document.getElementById("proxy-input").value = data.proxy || "";
    },

    async handleCurlImport() {
        const textarea = document.getElementById("curl-input");
        const resultDiv = document.getElementById("curl-result");
        const text = textarea.value.trim();

        if (!text) {
            showResult(resultDiv, "error", "Please paste a cURL command or cookie string.");
            return;
        }

        const btn = document.getElementById("btn-curl-import");
        btn.disabled = true;
        btn.textContent = "Importing...";

        try {
            const data = await api.post("/api/admin/config/curl-import", { curl_text: text });
            // Always refresh — cookies are saved even if reinit fails
            this.refresh();
            Dashboard.refresh();
            if (data.success) {
                showResult(resultDiv, "success", data.message);
                textarea.value = "";
            } else {
                const html = buildErrorMessage(data);
                showResultHtml(resultDiv, "error",
                    `<strong>Cookies saved</strong>, but connection failed:<br><br>` + html);
            }
        } catch (err) {
            const detail = err.detail || {};
            let msg = detail.message || "Failed to import cookies";
            if (detail.errors) msg += "\n" + detail.errors.join("\n");
            if (detail.found_cookies) msg += "\nFound cookies: " + detail.found_cookies.join(", ");
            showResult(resultDiv, "error", msg);
        } finally {
            btn.disabled = false;
            btn.textContent = "Import Cookies";
        }
    },

    async handleManualCookies() {
        const psid = document.getElementById("manual-1psid").value.trim();
        const psidts = document.getElementById("manual-1psidts").value.trim();
        const resultDiv = document.getElementById("manual-result");

        if (!psid || !psidts) {
            showResult(resultDiv, "error", "Both cookie values are required.");
            return;
        }

        try {
            const data = await api.post("/api/admin/config/cookies", {
                secure_1psid: psid,
                secure_1psidts: psidts,
            });
            this.refresh();
            Dashboard.refresh();
            if (data.success) {
                showResult(resultDiv, "success", data.message);
                document.getElementById("manual-1psid").value = "";
                document.getElementById("manual-1psidts").value = "";
            } else {
                showResultHtml(resultDiv, "error",
                    `<strong>Cookies saved</strong>, but connection failed:<br><br>` + buildErrorMessage(data));
            }
        } catch (err) {
            showResult(resultDiv, "error", "Failed: " + (err.detail || "Unknown error"));
        }
    },

    async handleModelSave() {
        const model = document.getElementById("model-select").value;
        const result = document.getElementById("model-result");
        try {
            await api.post("/api/admin/config/model", { model });
            showInline(result, "Saved", false);
        } catch {
            showInline(result, "Failed", true);
        }
    },

    async handleProxySave() {
        const proxy = document.getElementById("proxy-input").value.trim();
        const result = document.getElementById("proxy-result");
        try {
            await api.post("/api/admin/config/proxy", { http_proxy: proxy });
            showInline(result, "Saved", false);
            Dashboard.refresh();
        } catch {
            showInline(result, "Failed", true);
        }
    },

    updateTelegramDisplay(data) {
        document.getElementById("telegram-enabled").checked = !!data.enabled;
        document.getElementById("telegram-chatid").value = data.chat_id || "";
        document.getElementById("telegram-cooldown").value = data.cooldown_seconds || 60;
        const preview = document.getElementById("telegram-token-preview");
        preview.textContent = data.bot_token_preview || "";
        // Notify types checkboxes
        const types = data.notify_types || ["auth"];
        ["auth", "503", "500"].forEach(t => {
            const el = document.getElementById("notify-" + t);
            if (el) el.checked = types.includes(t);
        });
    },

    async handleTelegramSave() {
        const enabled = document.getElementById("telegram-enabled").checked;
        const bot_token = document.getElementById("telegram-token").value.trim();
        const chat_id = document.getElementById("telegram-chatid").value.trim();
        const cooldown_seconds = parseInt(document.getElementById("telegram-cooldown").value, 10) || 60;
        const notify_types = ["auth", "503", "500"].filter(t => {
            const el = document.getElementById("notify-" + t);
            return el && el.checked;
        });
        const result = document.getElementById("telegram-result");
        try {
            await api.post("/api/admin/config/telegram", { enabled, bot_token, chat_id, cooldown_seconds, notify_types });
            showInline(result, "Saved", false);
            // Clear token field and refresh preview
            document.getElementById("telegram-token").value = "";
            const tg = await api.get("/api/admin/config/telegram");
            this.updateTelegramDisplay(tg);
        } catch {
            showInline(result, "Failed", true);
        }
    },

    async handleTelegramTest() {
        const result = document.getElementById("telegram-result");
        const btn = document.getElementById("btn-test-telegram");
        btn.disabled = true;
        btn.textContent = "Sending...";
        try {
            const data = await api.post("/api/admin/config/telegram/test", {});
            showInline(result, data.message, !data.success);
        } catch (err) {
            const msg = (err && err.detail) ? err.detail : "Failed — check token and chat_id";
            showInline(result, msg, true);
        } finally {
            btn.disabled = false;
            btn.textContent = "Send Test";
        }
    },
};
