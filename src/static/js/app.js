// src/static/js/app.js - Main application controller

document.addEventListener("DOMContentLoaded", () => {
    const tabs = { dashboard: Dashboard, config: Config, logs: Logs };
    let activeTab = "dashboard";

    // Initialize all modules
    Object.values(tabs).forEach((t) => t.init?.());

    // Tab switching
    document.querySelectorAll(".tab-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
            const tabName = btn.dataset.tab;
            if (tabName === activeTab) return;

            // Deactivate current
            tabs[activeTab]?.deactivate?.();
            document.querySelector(".tab-btn.active").classList.remove("active");
            document.getElementById(`tab-${activeTab}`).classList.remove("active");

            // Activate new
            activeTab = tabName;
            btn.classList.add("active");
            document.getElementById(`tab-${tabName}`).classList.add("active");
            tabs[tabName]?.activate?.();
        });
    });

    // Activate initial tab
    tabs[activeTab]?.activate?.();

    // Global status poll for the header badge
    setInterval(async () => {
        try {
            const data = await api.get("/api/admin/status");
            const badge = document.getElementById("connection-status");
            badge.textContent = data.gemini_status === "connected" ? "Connected" : "Disconnected";
            badge.className = "status-badge " + data.gemini_status;
        } catch {
            const badge = document.getElementById("connection-status");
            badge.textContent = "Error";
            badge.className = "status-badge disconnected";
        }
    }, 15000);
});
