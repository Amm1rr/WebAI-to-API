# src/app/services/providers/gemini_playwright_scripts.py

"""
Javascript snippets for Playwright's page.evaluate to interact with Gemini Web.
Isolating these ensures the provider logic remains clean.
"""

# Selectors centralized for easier maintenance
SELECTORS = {
    # Prioritize semantic selectors: role textbox, then aria-labels, then placeholder heuristics
    "INPUT": '[role="textbox"], [aria-label*="Gemini"], [placeholder*="Gemini"], div[contenteditable="true"], textarea',
    "SEND_BUTTON": 'button[aria-label*="Send"], [data-test-id="send-button"]',
    "STOP_BUTTON": 'button[aria-label*="Stop"], .stop-button',
    "RESPONSE_CONTAINER": 'message-content, .message-content, [data-test-id="message-content"]'
}

# Configurable intervals
POLL_INTERVAL_MS = 150
COMPLETION_CHECK_MS = 500

# Script to inject MutationObserver and emit deltas back to Python
# Updated with robust extraction, memory/interval safety, and rewrite-resilient diffing
STREAM_EXTRACTOR_SCRIPT = f"""
(async (callbackName) => {{
    const emit = window[callbackName];
    if (!emit) {{
        console.error("Bridge callback not found:", callbackName);
        return;
    }}

    const findLatestResponse = () => {{
        const containers = document.querySelectorAll('{SELECTORS["RESPONSE_CONTAINER"]}');
        return containers.length > 0 ? containers[containers.length - 1] : null;
    }};

    let responseContainer = null;
    let lastSnapshot = "";
    let observer = null;
    let cleanedUp = false;

    const computeDelta = (current) => {{
        if (current.startsWith(lastSnapshot)) {{
            return {{ type: "chunk", delta: current.substring(lastSnapshot.length) }};
        }}
        
        // Rewrite detected or non-append-only change
        // We find the longest common prefix to minimize the rewrite payload
        let commonLength = 0;
        const minLen = Math.min(current.length, lastSnapshot.length);
        while (commonLength < minLen && current[commonLength] === lastSnapshot[commonLength]) {{
            commonLength++;
        }}
        
        // If common prefix is significant, we can still emit a delta if the client supports it
        // but for now, we emit a 'rewrite' event if the change is structural.
        return {{ type: "rewrite", full_text: current }};
    }};

    const startObservation = (element) => {{
        if (observer) observer.disconnect();
        
        observer = new MutationObserver(() => {{
            if (cleanedUp) return;
            const currentSnapshot = element.innerText || element.textContent || "";
            if (currentSnapshot === lastSnapshot) return;

            const payload = computeDelta(currentSnapshot);
            lastSnapshot = currentSnapshot;
            emit(payload);
        }});

        observer.observe(element, {{
            childList: true,
            subtree: true,
            characterData: true
        }});
        console.log("Observer attached.");
    }};

    const cleanup = () => {{
        if (cleanedUp) return;
        cleanedUp = true;
        clearInterval(pollForContainer);
        clearInterval(checkCompletion);
        if (observer) observer.disconnect();
        window.removeEventListener('unload', cleanup);
        console.log("DOM Observer cleanup complete.");
    }};

    window.addEventListener('unload', cleanup);

    const pollForContainer = setInterval(() => {{
        if (cleanedUp) return;
        const container = findLatestResponse();
        if (container && container !== responseContainer) {{
            responseContainer = container;
            startObservation(responseContainer);
        }}
    }}, {POLL_INTERVAL_MS});

    const checkCompletion = setInterval(() => {{
        if (cleanedUp) return;
        const sendButton = document.querySelector('{SELECTORS["SEND_BUTTON"]}');
        const isStopVisible = !!document.querySelector('{SELECTORS["STOP_BUTTON"]}');
        
        if (sendButton && !sendButton.disabled && !isStopVisible) {{
            if (responseContainer) {{
                const finalSnapshot = responseContainer.innerText || responseContainer.textContent || "";
                if (finalSnapshot !== lastSnapshot) {{
                    emit(computeDelta(finalSnapshot));
                }}
            }}
            
            cleanup();
            emit({{type: "done"}});
        }}
    }}, {COMPLETION_CHECK_MS});
}})
"""
