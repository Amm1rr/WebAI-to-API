// src/static/js/dashboard.js - Dashboard tab logic

function _relativeTime(secondsAgo) {
    if (secondsAgo < 5) return "just now";
    if (secondsAgo < 60) return `${Math.floor(secondsAgo)}s ago`;
    if (secondsAgo < 3600) return `${Math.floor(secondsAgo / 60)}m ago`;
    if (secondsAgo < 86400) return `${Math.floor(secondsAgo / 3600)}h ago`;
    return `${Math.floor(secondsAgo / 86400)}d ago`;
}

function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
        return navigator.clipboard.writeText(text);
    }
    // Fallback for HTTP (non-secure) contexts
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;top:-9999px;left:-9999px;opacity:0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    try { document.execCommand("copy"); } catch (_) {}
    document.body.removeChild(ta);
    return Promise.resolve();
}

const API_ENDPOINTS = [
    { method: "GET",  path: "/v1/models",            desc: "List available models" },
    { method: "POST", path: "/v1/chat/completions",  desc: "Chat completions — OpenAI format (text + vision)" },
    { method: "POST", path: "/v1/responses",         desc: "Responses API — Home Assistant openai_conversation format (camera images)" },
    { method: "POST", path: "/v1/files",             desc: "Upload image/PDF — returns file_id" },
    { method: "GET",  path: "/v1/files/{file_id}",   desc: "Get uploaded file info" },
    { method: "DELETE", path: "/v1/files/{file_id}", desc: "Delete uploaded file" },
    { method: "POST", path: "/v1beta/models/{model}", desc: "Generate content — Google AI format" },
    { method: "POST", path: "/gemini",               desc: "Generate content (text + images in response)" },
    { method: "POST", path: "/gemini-chat",          desc: "Stateful chat with session context" },
];

// ---------------------------------------------------------------------------
// cURL Usage Examples
// ---------------------------------------------------------------------------
const CURL_EXAMPLES = [
    {
        id: "chat",
        label: "💬 Chat",
        desc: "Dùng cho mọi client OpenAI-compatible: Home Assistant, n8n, LangChain... Đổi <code>model</code> thành <code>gemini-3.0-pro</code> hoặc <code>gemini-3.0-flash-thinking</code> nếu cần.",
        curl: (base) => `curl -X POST ${base}/v1/chat/completions \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "gemini-3.0-flash",
    "messages": [
      {"role": "user", "content": "Xin chào!"}
    ]
  }'`,
    },
    {
        id: "vision",
        label: "🖼️ Vision",
        desc: "Gửi ảnh kèm câu hỏi. Dùng <code>data:image/jpeg;base64,...</code> cho ảnh inline, hoặc URL công khai — server tự tải về.",
        curl: (base) => `# Dùng URL công khai
curl -X POST ${base}/v1/chat/completions \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "gemini-3.0-flash",
    "messages": [{
      "role": "user",
      "content": [
        {"type": "text",      "text": "Ảnh này chụp gì?"},
        {"type": "image_url", "image_url": {"url": "https://example.com/photo.jpg"}}
      ]
    }]
  }'

# Hoặc dùng base64 (Home Assistant dùng format này)
# "url": "data:image/jpeg;base64,<BASE64_DATA>"`,
    },
    {
        id: "image-gen",
        label: "🎨 Tạo ảnh",
        desc: "Yêu cầu Gemini sinh ảnh. Kết quả trả về trong <code>images[]</code> gồm URL và base64.",
        curl: (base) => `curl -X POST ${base}/gemini \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "gemini-3.0-flash",
    "message": "Vẽ một bức tranh hoàng hôn trên biển"
  }'`,
    },
    {
        id: "home-assistant",
        label: "🏠 Home Assistant",
        desc: "Tích hợp service này vào Home Assistant qua extension <strong>Local OpenAI LLM</strong> (cài qua HACS). Hỗ trợ chat, điều khiển thiết bị và phân tích ảnh camera.",
        steps: [
            "Cài <strong>Local OpenAI LLM</strong> qua HACS: thêm custom repo <code>https://github.com/skye-harris/hass_local_openai_llm</code> → chọn <em>Integration</em> → Install.",
            "Vào <em>Settings → Devices &amp; Services → Add Integration</em>, tìm <strong>Local OpenAI LLM</strong>.",
            "Điền <strong>Server URL</strong>: <code>" + location.origin + "/v1</code> &nbsp;·&nbsp; <strong>API Key</strong>: để trống.",
            "Chọn <strong>Model</strong> từ dropdown — HA tự query <code>/v1/models</code> và hiện danh sách: <code>gemini-3.0-flash</code>, <code>gemini-3.0-pro</code>, <code>gemini-3.0-flash-thinking</code>.",
            "<strong>Chat / Assist:</strong> tạo subentry <em>Conversation Agent</em> → chọn model vừa cấu hình.",
            "<strong>Phân tích ảnh camera:</strong> tạo subentry <em>AI Task Agent</em> → HA tự gửi ảnh qua <code>/v1/chat/completions</code>, không cần cấu hình thêm.",
        ],
    },
    {
        id: "list-models",
        label: "📋 Models",
        desc: "Lấy danh sách model hiện có.",
        curl: (base) => `curl ${base}/v1/models`,
    },
];

const Dashboard = {
    intervalId: null,
    _dismissedAlertId: null,
    _activeCurlTab: null,

    init() {
        // Render static sections
        this.renderApiReference();
        this.renderCurlExamples();

        document.getElementById("btn-reinit").addEventListener("click", async () => {
            const btn = document.getElementById("btn-reinit");
            const resultEl = document.getElementById("reinit-result");
            btn.disabled = true;
            btn.textContent = "Reinitializing...";
            try {
                const data = await api.post("/api/admin/client/reinitialize");
                if (data.success) {
                    showInline(resultEl, data.message, false);
                } else {
                    resultEl.innerHTML = buildErrorMessage(data);
                    resultEl.style.color = "var(--error)";
                }
            } catch (err) {
                showInline(resultEl, "Failed: " + (err.detail || "Unknown error"), true);
            } finally {
                btn.disabled = false;
                btn.textContent = "Reinitialize Gemini Client";
                this.refresh();
            }
        });
        // Test Generation button
        const btnTest = document.getElementById("btn-test-gen");
        if (btnTest) {
            btnTest.addEventListener("click", async () => {
                const resultEl = document.getElementById("test-gen-result");
                btnTest.disabled = true;
                btnTest.textContent = "Testing...";
                showInline(resultEl, "Sending test prompt to Gemini...", false);
                try {
                    const data = await api.post("/api/admin/client/test");
                    if (data.success) {
                        showInline(resultEl, data.message || "Test passed!", false);
                        resultEl.style.color = "var(--success)";
                    } else {
                        showInline(resultEl, `Test failed: ${data.error || "Unknown"} (${data.suggestion || ""})`, true);
                        resultEl.style.color = "var(--error)";
                    }
                } catch (err) {
                    showInline(resultEl, "Test failed: " + (err.detail || "Request error"), true);
                } finally {
                    btnTest.disabled = false;
                    btnTest.innerHTML = '<span class="material-symbols-outlined">play_arrow</span> Test Generation';
                    this.refresh();
                }
            });
        }

        // Clear errors button
        const btnClearErrors = document.getElementById("btn-clear-errors");
        if (btnClearErrors) {
            btnClearErrors.addEventListener("click", async () => {
                try {
                    await api.post("/api/admin/errors/clear");
                    this.refresh();
                } catch (_) {}
            });
        }

        // Dismiss alert banner
        const btnDismiss = document.getElementById("alert-banner-close");
        if (btnDismiss) {
            btnDismiss.addEventListener("click", () => {
                const banner = document.getElementById("status-alert-banner");
                banner.style.display = "none";
                if (this._currentAlertId) {
                    this._dismissedAlertId = this._currentAlertId;
                }
            });
        }


        // Copy button handlers
        document.querySelectorAll(".btn-copy").forEach(btn => {
            btn.addEventListener("click", () => {
                const targetId = btn.dataset.copyTarget;
                const el = document.getElementById(targetId);
                if (!el) return;
                const text = el.textContent;
                copyText(text).then(() => {
                    const orig = btn.textContent;
                    btn.textContent = "Copied!";
                    btn.classList.add("copied");
                    setTimeout(() => { btn.textContent = orig; btn.classList.remove("copied"); }, 1500);
                });
            });
        });
    },

    activate() {
        this.refresh();
        this.intervalId = setInterval(() => this.refresh(), 10000);
    },

    deactivate() {
        if (this.intervalId) {
            clearInterval(this.intervalId);
            this.intervalId = null;
        }
    },

    async refresh() {
        try {
            const data = await api.get("/api/admin/status");
            this.updateCards(data);
            this.updateAlertBanner(data);
            this.updateRecentErrors(data.recent_errors);
            this.updateEndpointTable(data.stats.endpoints, data.stats.endpoints_detail, data.stats.total_requests);
        } catch {
            document.getElementById("val-status").textContent = "Error";
        }
    },

    updateCards(data) {
        const statusEl = document.getElementById("val-status");
        const statusCard = document.getElementById("card-status");
        if (data.status_level === "warning") {
            statusEl.textContent = "Warning";
            statusEl.style.color = "var(--md-warning)";
            let detailEl = statusCard.querySelector(".stat-detail");
            if (!detailEl) {
                detailEl = document.createElement("div");
                detailEl.className = "stat-detail";
                statusCard.appendChild(detailEl);
            }
            detailEl.innerHTML = `<span style="color:var(--md-warning);font-weight:600;">⚠️ ${escapeHtml(data.last_error?.title || "Stream issue detected")}</span>`;
        } else if (data.gemini_status === "connected") {
            statusEl.textContent = "Connected";
            statusEl.style.color = "var(--success)";
            statusCard.querySelector(".stat-detail")?.remove();
        } else {
            statusEl.textContent = "Disconnected";
            statusEl.style.color = "var(--error)";
            // Show error hint under status card
            let detailEl = statusCard.querySelector(".stat-detail");
            if (!detailEl) {
                detailEl = document.createElement("div");
                detailEl.className = "stat-detail";
                statusCard.appendChild(detailEl);
            }
            const hints = {
                auth_expired: "Cookie expired — get fresh cookies",
                no_cookies: "No cookies — go to Configuration tab",
                network: "Network error — check connection/proxy",
                disabled: "Gemini disabled in config",
            };
            detailEl.innerHTML = `<span class="error">${escapeHtml(hints[data.error_code] || data.client_error || "Check Configuration tab")}</span>`;
        }
        document.getElementById("val-model").textContent = data.current_model || "--";
        document.getElementById("val-requests").textContent = data.stats.total_requests;
        document.getElementById("val-success").textContent = data.stats.success_count + " OK";
        document.getElementById("val-errors").textContent = data.stats.error_count + " ERR";
        document.getElementById("val-uptime").textContent = data.stats.uptime;

        // Update header badge
        const badge = document.getElementById("connection-status");
        if (data.status_level === "warning") {
            badge.textContent = "Warning";
            badge.className = "status-badge warning";
        } else if (data.gemini_status === "connected") {
            badge.textContent = "Connected";
            badge.className = "status-badge connected";
        } else {
            badge.textContent = "Disconnected";
            badge.className = "status-badge disconnected";
        }

        // Update version chip
        const versionEl = document.getElementById("app-version");
        if (versionEl && data.version) versionEl.textContent = "v" + data.version;
    },
    updateAlertBanner(data) {
        const banner = document.getElementById("status-alert-banner");
        if (!banner) return;

        const lastErr = data.last_error;
        const isFresh = lastErr && (Date.now() / 1000 - lastErr.timestamp < 1800);

        if (isFresh && lastErr.id !== this._dismissedAlertId) {
            this._currentAlertId = lastErr.id;
            banner.style.display = "flex";
            if (data.status_level === "error" || lastErr.category === "auth") {
                banner.classList.add("error-mode");
            } else {
                banner.classList.remove("error-mode");
            }

            document.getElementById("alert-banner-title").textContent = lastErr.title || "Service Issue Detected";
            document.getElementById("alert-banner-badge").textContent = (lastErr.category || "Warning").toUpperCase();
            document.getElementById("alert-banner-message").textContent = `${lastErr.endpoint} (${lastErr.model || "default"}): ${lastErr.message}`;
            document.getElementById("alert-banner-tip").textContent = "💡 " + (lastErr.suggestion || "Check Logs tab for more info.");
        } else if (data.status_level === "error") {
            banner.style.display = "flex";
            banner.classList.add("error-mode");
            document.getElementById("alert-banner-title").textContent = "Client Disconnected";
            document.getElementById("alert-banner-badge").textContent = "ERROR";
            document.getElementById("alert-banner-message").textContent = data.client_error || "Gemini client failed to initialize.";
            document.getElementById("alert-banner-tip").textContent = "💡 Check Configuration tab to verify cookies.";
        } else {
            banner.style.display = "none";
        }
    },

    updateRecentErrors(recentErrors) {
        const section = document.getElementById("section-recent-errors");
        const listEl = document.getElementById("recent-errors-list");
        if (!section || !listEl) return;

        if (!recentErrors || recentErrors.length === 0) {
            section.style.display = "none";
            listEl.innerHTML = "";
            return;
        }

        section.style.display = "block";
        const now = Date.now() / 1000;
        listEl.innerHTML = recentErrors.map(err => {
            const timeAgo = _relativeTime(now - err.timestamp);
            return `<div class="error-card-item cat-${escapeHtml(err.category || 'error')}">
                <div class="error-card-top">
                    <span class="error-card-title">⚠️ ${escapeHtml(err.title || "Error")}</span>
                    <span class="error-card-time">${escapeHtml(timeAgo)} (${escapeHtml(err.endpoint || '')})</span>
                </div>
                <div class="error-card-msg">${escapeHtml(err.message)}</div>
                <div class="error-card-tip">💡 ${escapeHtml(err.suggestion || '')}</div>
            </div>`;
        }).join("");
    },

    updateEndpointTable(endpoints, endpointsDetail, totalRequests) {
        const tbody = document.getElementById("endpoint-tbody");
        const noData = document.getElementById("no-endpoints");
        const detail = endpointsDetail || {};
        const entries = Object.entries(detail);

        if (entries.length === 0) {
            tbody.innerHTML = "";
            noData.classList.remove("hidden");
            return;
        }

        noData.classList.add("hidden");
        entries.sort((a, b) => b[1].count - a[1].count);

        const total = totalRequests || 1;
        const now = Date.now() / 1000;

        tbody.innerHTML = entries.map(([path, d]) => {
            const pct = total > 0 ? ((d.count / total) * 100).toFixed(1) : "0.0";
            const lastSeen = d.last_seen ? _relativeTime(now - d.last_seen) : "—";
            const errClass = d.error > 0 ? ' class="ep-err"' : '';
            return `<tr>
                <td class="ep-path">${escapeHtml(path)}</td>
                <td class="col-num ep-count">${d.count}</td>
                <td class="col-num ep-ok">${d.success}</td>
                <td class="col-num"${errClass}>${d.error}</td>
                <td class="col-num ep-pct">
                    <span class="pct-bar-wrap">
                        <span class="pct-bar" style="width:${Math.min(parseFloat(pct),100)}%"></span>
                        <span class="pct-label">${pct}%</span>
                    </span>
                </td>
                <td class="ep-last">${escapeHtml(lastSeen)}</td>
            </tr>`;
        }).join("");
    },

    getBaseUrl() {
        return window.location.origin;
    },

    renderApiReference() {
        const baseUrl = this.getBaseUrl();
        document.getElementById("api-base-url").textContent = baseUrl + "/v1";

        const tbody = document.getElementById("api-ref-tbody");
        tbody.innerHTML = API_ENDPOINTS.map(ep => {
            const fullUrl = baseUrl + ep.path;
            const methodLower = ep.method.toLowerCase();
            return `<tr>
                <td><span class="method-badge method-${methodLower}">${ep.method}</span></td>
                <td><code class="api-url">${escapeHtml(fullUrl)}</code></td>
                <td class="api-desc">${escapeHtml(ep.desc)}</td>
                <td><button class="btn btn-small btn-copy" data-copy-value="${escapeHtml(fullUrl)}" title="Copy URL">Copy</button></td>
            </tr>`;
        }).join("");

        tbody.querySelectorAll(".btn-copy").forEach(btn => {
            btn.addEventListener("click", () => {
                const text = btn.dataset.copyValue;
                copyText(text).then(() => {
                    const orig = btn.textContent;
                    btn.textContent = "Copied!";
                    btn.classList.add("copied");
                    setTimeout(() => { btn.textContent = orig; btn.classList.remove("copied"); }, 1500);
                });
            });
        });
    },

    renderCurlExamples() {
        const baseUrl = this.getBaseUrl();
        const container = document.getElementById("curl-tabs");
        if (!container) return;

        // Tab buttons row
        const tabsHtml = CURL_EXAMPLES.map((ex, i) =>
            `<button class="curl-tab-btn${i === 0 ? " active" : ""}" data-tab="${ex.id}">${escapeHtml(ex.label)}</button>`
        ).join("");

        // Panels
        const panelsHtml = CURL_EXAMPLES.map((ex, i) => {
            let bodyHtml;
            if (ex.steps) {
                const items = ex.steps.map(s => `<li>${s}</li>`).join("");
                bodyHtml = `<ol class="guide-steps">${items}</ol>`;
            } else {
                const curlText = ex.curl(baseUrl);
                bodyHtml = `<div class="curl-code-wrap">
                    <pre class="curl-code" id="curl-code-${ex.id}">${escapeHtml(curlText)}</pre>
                    <button class="btn btn-small btn-copy curl-copy-btn" data-copy-id="curl-code-${ex.id}">Copy</button>
                </div>`;
            }
            return `<div class="curl-panel${i === 0 ? " active" : ""}" id="curl-panel-${ex.id}">
                <p class="help-text curl-desc">${ex.desc}</p>
                ${bodyHtml}
            </div>`;
        }).join("");

        container.innerHTML = `<div class="curl-tab-bar">${tabsHtml}</div><div class="curl-panels">${panelsHtml}</div>`;

        // Tab switching
        container.querySelectorAll(".curl-tab-btn").forEach(btn => {
            btn.addEventListener("click", () => {
                const tabId = btn.dataset.tab;
                container.querySelectorAll(".curl-tab-btn").forEach(b => b.classList.remove("active"));
                container.querySelectorAll(".curl-panel").forEach(p => p.classList.remove("active"));
                btn.classList.add("active");
                container.querySelector(`#curl-panel-${tabId}`)?.classList.add("active");
            });
        });

        // Copy buttons
        container.querySelectorAll(".curl-copy-btn").forEach(btn => {
            btn.addEventListener("click", () => {
                const el = document.getElementById(btn.dataset.copyId);
                if (!el) return;
                copyText(el.textContent).then(() => {
                    const orig = btn.textContent;
                    btn.textContent = "Copied!";
                    btn.classList.add("copied");
                    setTimeout(() => { btn.textContent = orig; btn.classList.remove("copied"); }, 1500);
                });
            });
        });
    },
};
