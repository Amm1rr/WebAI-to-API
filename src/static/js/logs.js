// src/static/js/logs.js - Real-time log viewer with SSE

const Logs = {
    eventSource: null,
    entries: [],
    lastId: 0,
    autoScroll: true,

    init() {
        document.getElementById("log-autoscroll").addEventListener("change", (e) => {
            this.autoScroll = e.target.checked;
        });

        document.getElementById("btn-clear-logs").addEventListener("click", () => {
            this.entries = [];
            this.renderAll();
        });

        document.getElementById("log-level-filter").addEventListener("change", () => this.renderAll());
        document.getElementById("log-search").addEventListener("input", () => this.renderAll());
    },

    async activate() {
        // Load recent logs first
        try {
            const data = await api.get("/api/admin/logs/recent?count=100");
            this.entries = data.logs || [];
            if (this.entries.length > 0) {
                this.lastId = this.entries[this.entries.length - 1].id;
            }
        } catch {
            this.entries = [];
        }
        this.renderAll();
        this.connectSSE();
    },

    deactivate() {
        if (this.eventSource) {
            this.eventSource.close();
            this.eventSource = null;
        }
    },

    connectSSE() {
        if (this.eventSource) this.eventSource.close();

        const url = `/api/admin/logs/stream?last_id=${this.lastId}`;
        this.eventSource = new EventSource(url);

        this.eventSource.addEventListener("log", (event) => {
            const entry = JSON.parse(event.data);
            this.lastId = parseInt(event.lastEventId) || entry.id;
            this.entries.push(entry);

            // Cap at 1000 entries client-side
            if (this.entries.length > 1000) {
                this.entries = this.entries.slice(-500);
            }

            if (this.matchesFilter(entry)) {
                this.appendEntry(entry);
            }
        });

        this.eventSource.onerror = () => {
            // EventSource auto-reconnects
        };
    },

    matchesFilter(entry) {
        const levelFilter = document.getElementById("log-level-filter").value;
        const searchText = document.getElementById("log-search").value.toLowerCase();

        if (levelFilter !== "ALL" && entry.level !== levelFilter) return false;
        if (searchText && !entry.message.toLowerCase().includes(searchText) &&
            !entry.logger.toLowerCase().includes(searchText)) return false;

        return true;
    },

    renderAll() {
        const container = document.getElementById("log-container");
        const filtered = this.entries.filter(e => this.matchesFilter(e));

        if (filtered.length === 0) {
            container.innerHTML = '<p class="empty-state">Waiting for logs...</p>';
            return;
        }

        container.innerHTML = filtered.map(e => this.formatEntry(e)).join("");
        if (this.autoScroll) container.scrollTop = container.scrollHeight;
    },

    appendEntry(entry) {
        const container = document.getElementById("log-container");
        // Remove empty state if present
        const emptyState = container.querySelector(".empty-state");
        if (emptyState) emptyState.remove();

        container.insertAdjacentHTML("beforeend", this.formatEntry(entry));
        if (this.autoScroll) container.scrollTop = container.scrollHeight;
    },

    formatEntry(entry) {
        const ts = entry.timestamp.split("T")[1] || entry.timestamp;
        return `<div class="log-entry"><span class="ts">${escapeHtml(ts)}</span> <span class="lvl lvl-${entry.level}">${entry.level.padEnd(7)}</span> <span class="logger-name">[${escapeHtml(entry.logger)}]</span> <span class="msg">${escapeHtml(entry.message)}</span></div>`;
    },
};
