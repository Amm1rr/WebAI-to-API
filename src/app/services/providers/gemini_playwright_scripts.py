# src/app/services/providers/gemini_playwright_scripts.py

"""
Javascript snippets for Playwright's page.evaluate to interact with Gemini Web.
Hardened for high-speed synchronization and zero-loss streaming.
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
# Lower values (50/100) are used for production hardening against fast responses
POLL_INTERVAL_MS = 50
COMPLETION_CHECK_MS = 100

# Script to inject MutationObserver and emit deltas back to Python
# Features:
# - Generation-based history filtering (initialMessageCount)
# - Non-blocking browser-to-python bridge
# - Rewrite-resilient DOM diffing
# - Ready-signal synchronization for Python-side submission timing
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
    let hasStartedGenerating = false;

    /**
     * Computes the difference between current and previous text snapshots.
     * Handles both incremental chunks and full UI rewrites.
     */
    const computeDelta = (current) => {{
        if (current.startsWith(lastSnapshot)) {{
            return {{ type: "chunk", delta: current.substring(lastSnapshot.length) }};
        }}
        
        // Rewrite detected or non-append-only change
        // Gemini often 'polishes' output, causing a rewrite event.
        // We emit the full text to ensure the client has the most accurate state.
        return {{ type: "rewrite", full_text: current }};
    }};

    /**
     * Attaches a MutationObserver to the target element to capture live text deltas.
     */
    const startObservation = (element) => {{
        if (observer) observer.disconnect();
        lastSnapshot = ""; // RESET snapshot for the new message to start fresh
        
        observer = new MutationObserver(() => {{
            if (cleanedUp) return;
            const currentSnapshot = element.innerText || element.textContent || "";
            if (currentSnapshot === lastSnapshot) return;

            hasStartedGenerating = true;
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

    /**
     * Stop all loops and observers.
     */
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

    // Watch for the appearance of the NEW message container
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

    /**
     * Monitors the Send/Stop buttons to detect when Gemini finishes generating.
     */
    const checkCompletion = setInterval(() => {{
        if (cleanedUp) return;
        
        const sendButton = document.querySelector('{SELECTORS["SEND_BUTTON"]}');
        const stopButton = document.querySelector('{SELECTORS["STOP_BUTTON"]}');
        const isStopVisible = !!stopButton;
        const isGenerating = (sendButton && sendButton.disabled) || isStopVisible;

        if (isGenerating && !hasStartedGenerating) {{
            hasStartedGenerating = true;
            emit({{type: "started"}});
        }}

        // Robust completion condition:

        // 1. Must have evidence of starting (button disabled or stop visible)
        // 2. Must have found the container (prevents exiting before observer is attached)
        // 3. Generation must now be finished (button enabled and stop gone)
        if (hasStartedGenerating && responseContainer && sendButton && !sendButton.disabled && !isStopVisible) {{
            // Final delta check to catch trailing text
            const finalSnapshot = responseContainer.innerText || responseContainer.textContent || "";
            if (finalSnapshot !== lastSnapshot) {{
                emit(computeDelta(finalSnapshot));
            }}
            
            console.log("Generation finished successfully.");
            emit({{type: "done"}});
            cleanup();
        }}
    }}, {COMPLETION_CHECK_MS});

    // Notify Python that the observer is READY to capture content
    // Python waits for this signal before submitting the prompt
    emit({{type: "ready"}});
    console.log("Stream extractor READY.");
}})
"""
