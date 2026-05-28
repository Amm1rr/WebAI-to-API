# src/app/services/providers/gemini_playwright_scripts.py

"""
Javascript snippets for Playwright's page.evaluate to interact with Gemini Web.
Isolating these ensures the provider logic remains clean.
"""

# Selectors optimized for high-precision targeting to avoid navigational elements
# Centralized for easier maintenance across UI updates
SELECTORS = {
    # Target ONLY editable regions, avoiding buttons or links
    # Prioritize semantic selectors: role textbox, then specific placeholders
    "INPUT": 'div[contenteditable="true"][role="textbox"], textarea.gds-body-l, textarea[placeholder*="Gemini"]',
    "SEND_BUTTON": 'button.send-button, button[aria-label*="Send message"], [data-test-id="send-button"]',
    "STOP_BUTTON": 'button[aria-label*="Stop"], .stop-button',
    "RESPONSE_CONTAINER": 'message-content, .message-content, [data-test-id="message-content"]'
}

# Configurable intervals for responsiveness vs CPU usage
POLL_INTERVAL_MS = 150
COMPLETION_CHECK_MS = 500

# Script to inject MutationObserver and emit deltas back to Python
# Features:
# - Generation-based history filtering (initialMessageCount)
# - Non-blocking browser-to-python bridge
# - Rewrite-resilient DOM diffing
STREAM_EXTRACTOR_SCRIPT = f"""
(async (callbackName) => {{
    const emit = window[callbackName];
    if (!emit) {{
        console.error("Bridge callback not found:", callbackName);
        return;
    }}

    const getAllResponses = () => document.querySelectorAll('{SELECTORS["RESPONSE_CONTAINER"]}');
    
    // Count existing messages to avoid picking up history from previous sessions
    const initialMessageCount = getAllResponses().length;
    console.log("Initial message count for filtering:", initialMessageCount);

    let responseContainer = null;
    let lastSnapshot = "";
    let observer = null;
    let cleanedUp = false;
    let hasStarted = false;

    const computeDelta = (current) => {{
        if (current.startsWith(lastSnapshot)) {{
            return {{ type: "chunk", delta: current.substring(lastSnapshot.length) }};
        }}
        
        // Rewrite detected or non-append-only change
        // Find longest common prefix to potentially minimize payload in future
        let commonLength = 0;
        const minLen = Math.min(current.length, lastSnapshot.length);
        while (commonLength < minLen && current[commonLength] === lastSnapshot[commonLength]) {{
            commonLength++;
        }}
        
        // Gemini often 'polishes' output, causing a rewrite event.
        // We emit the full text to ensure the client has the most accurate state.
        return {{ type: "rewrite", full_text: current }};
    }};

    const startObservation = (element) => {{
        if (observer) observer.disconnect();
        lastSnapshot = ""; // RESET snapshot for the new message to start fresh
        
        observer = new MutationObserver(() => {{
            if (cleanedUp) return;
            const currentSnapshot = element.innerText || element.textContent || "";
            if (currentSnapshot === lastSnapshot) return;

            hasStarted = true;
            const payload = computeDelta(currentSnapshot);
            lastSnapshot = currentSnapshot;
            emit(payload);
        }});

        observer.observe(element, {{
            childList: true,
            subtree: true,
            characterData: true
        }});
        console.log("Observer attached to NEW message container.");
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

    // Only start observing when a NEW message container appears
    // This prevents picking up historical chat entries
    const pollForContainer = setInterval(() => {{
        if (cleanedUp) return;
        const allResponses = getAllResponses();
        if (allResponses.length > initialMessageCount) {{
            const latest = allResponses[allResponses.length - 1];
            if (latest !== responseContainer) {{
                responseContainer = latest;
                startObservation(responseContainer);
            }}
        }}
    }}, {POLL_INTERVAL_MS});

    const checkCompletion = setInterval(() => {{
        if (cleanedUp) return;
        
        const sendButton = document.querySelector('{SELECTORS["SEND_BUTTON"]}');
        const stopButton = document.querySelector('{SELECTORS["STOP_BUTTON"]}');
        const isStopVisible = !!stopButton;
        const isGenerating = (sendButton && sendButton.disabled) || isStopVisible;

        if (isGenerating) {{
            hasStarted = true;
        }}

        // Completion condition: We MUST have started, and now Send is enabled AND Stop is gone
        if (hasStarted && sendButton && !sendButton.disabled && !isStopVisible) {{
            // Final delta check to catch trailing text
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
