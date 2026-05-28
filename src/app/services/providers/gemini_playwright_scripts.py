# src/app/services/providers/gemini_playwright_scripts.py

"""
Javascript snippets for Playwright's page.evaluate to interact with Gemini Web.
Hardened for high-speed synchronization and zero-loss streaming.
Isolating these ensures the provider logic remains clean.
"""

# Selectors optimized for high-precision targeting
SELECTORS = {
    "INPUT": 'div[contenteditable="true"][role="textbox"], textarea.gds-body-l, textarea[placeholder*="Gemini"]',
    "SEND_BUTTON": 'button.send-button, button[aria-label*="Send message"], [data-test-id="send-button"]',
    "STOP_BUTTON": 'button[aria-label*="Stop"], .stop-button',
    "RESPONSE_CONTAINER": 'message-content, .message-content, [data-test-id="message-content"]'
}

# Configurable intervals for responsiveness vs CPU usage
POLL_INTERVAL_MS = 50
COMPLETION_CHECK_MS = 100

# Script to inject MutationObserver and emit deltas back to Python
# Features:
# - Request-scoped isolation (window.__gemini_observers)
# - Deterministic cleanup (clearInterval per request)
# - Invariant-based completion (no done without started)
# - Safe-emit (exception isolation for Playwright bindings)
STREAM_EXTRACTOR_SCRIPT = f"""
(async (callbackName, requestId) => {{
    const rawEmit = window[callbackName];
    if (!rawEmit) {{
        console.error(`[${{requestId}}] Bridge callback not found:`, callbackName);
        return;
    }}

    /**
     * Safe-Emit Wrapper: Prevents JS execution from halting if the Playwright 
     * binding throws (e.g., if the page is closing or navigation occurred).
     */
    const emit = (payload) => {{
        try {{
            rawEmit(payload);
        }} catch (e) {{
            console.warn(`[${{requestId}}] Emit failed (possibly page closing):`, e.message);
        }}
    }};

    // Initialize Global Request Registry (Singleton for the window)
    window.__gemini_observers = window.__gemini_observers || {{}};
    
    // Idempotent Cleanup Definition (Singleton for the window)
    window.__gemini_stop_observer = window.__gemini_stop_observer || ((rId) => {{
        const s = window.__gemini_observers && window.__gemini_observers[rId];
        if (!s || s.cleanedUp) return;
        
        // INVARIANT: cleanup is one-way and terminal
        s.cleanedUp = true;
        
        // 1. Stop all polling
        s.intervals.forEach(clearInterval);
        s.intervals = [];
        
        // 2. Disconnect DOM observer (synchronous)
        if (s.observer) {{
            s.observer.disconnect();
            s.observer = null;
        }}
        
        // 3. Clear from registry to prevent memory leak
        delete window.__gemini_observers[rId];
        console.log(`[${{rId}}] Observer destroyed.`);
    }});

    // Cleanup any existing observer for the same ID (Safety for rapid reuse)
    window.__gemini_stop_observer(requestId);

    // Request State Ownership
    const state = {{
        requestId: requestId,
        initialMessageCount: document.querySelectorAll('{SELECTORS["RESPONSE_CONTAINER"]}').length,
        responseContainer: null,
        lastSnapshot: "",
        observer: null,
        started: false,
        done: false,
        cleanedUp: false,
        intervals: []
    }};
    window.__gemini_observers[requestId] = state;

    console.log(`[${{requestId}}] Initializing observer. Initial messages: ${{state.initialMessageCount}}`);

    const computeDelta = (current) => {{
        if (current.startsWith(state.lastSnapshot)) {{
            return {{ type: "chunk", delta: current.substring(state.lastSnapshot.length) }};
        }}
        return {{ type: "rewrite", full_text: current }};
    }};

    const startObservation = (element) => {{
        if (state.observer) state.observer.disconnect();
        state.lastSnapshot = "";
        
        state.observer = new MutationObserver(() => {{
            // INVARIANT: Mutation callbacks must respect terminal state
            if (state.cleanedUp || state.done) return;
            
            const currentSnapshot = element.innerText || element.textContent || "";
            if (currentSnapshot === state.lastSnapshot) return;

            state.started = true;
            const payload = computeDelta(currentSnapshot);
            state.lastSnapshot = currentSnapshot;
            emit(payload);
        }});

        state.observer.observe(element, {{
            childList: true,
            subtree: true,
            characterData: true
        }});
        console.log(`[${{requestId}}] Observer attached to response container.`);
    }};

    // 1. Container Polling Loop
    const pollForContainer = setInterval(() => {{
        if (state.cleanedUp || state.done) return;
        
        const allResponses = document.querySelectorAll('{SELECTORS["RESPONSE_CONTAINER"]}');
        if (allResponses.length > state.initialMessageCount) {{
            const latest = allResponses[allResponses.length - 1];
            if (latest !== state.responseContainer) {{
                state.responseContainer = latest;
                startObservation(state.responseContainer);
            }}
        }}
    }}, {POLL_INTERVAL_MS});
    state.intervals.push(pollForContainer);

    // 2. Completion Polling Loop
    const checkCompletion = setInterval(() => {{
        if (state.cleanedUp || state.done) return;
        
        const sendButton = document.querySelector('{SELECTORS["SEND_BUTTON"]}');
        const stopButton = document.querySelector('{SELECTORS["STOP_BUTTON"]}');
        const isStopVisible = !!stopButton;
        const isGenerating = (state.responseContainer && sendButton && sendButton.disabled) || isStopVisible;

        // INVARIANT: Transition to 'started' is strictly one-way
        if (isGenerating && !state.started) {{
            state.started = true;
            emit({{type: "started"}});
        }}

        // INVARIANT: Completion required a detected start and a stable idle state
        if (state.started && state.responseContainer && sendButton && !sendButton.disabled && !isStopVisible) {{
            // Terminal State Transition
            state.done = true;
            
            const finalSnapshot = state.responseContainer.innerText || state.responseContainer.textContent || "";
            if (finalSnapshot !== state.lastSnapshot) {{
                emit(computeDelta(finalSnapshot));
            }}
            
            console.log(`[${{requestId}}] Generation finished.`);
            emit({{type: "done"}});
            
            // Self-Cleanup
            window.__gemini_stop_observer(requestId);
        }}
    }}, {COMPLETION_CHECK_MS});
    state.intervals.push(checkCompletion);

    emit({{type: "ready"}});
}})
"""

STOP_OBSERVER_SCRIPT = """
(requestId) => {
    if (window.__gemini_stop_observer) {
        window.__gemini_stop_observer(requestId);
    }
}
"""
