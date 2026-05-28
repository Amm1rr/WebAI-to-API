# src/app/services/browser/adapters/scripts/gemini_scripts.py

"""
Javascript snippets for Playwright's page.evaluate to interact with Gemini Web.
Relocated purely for organizational separation of vendor-specific DOM artifacts.
Stream lifecycle and orchestration remain completely owned by the Python orchestrator.
"""

SELECTORS = {
    "INPUT": 'div[contenteditable="true"][role="textbox"], textarea.gds-body-l, textarea[placeholder*="Gemini"]',
    "SEND_BUTTON": 'button.send-button, button[aria-label*="Send message"], [data-test-id="send-button"]',
    "STOP_BUTTON": 'button[aria-label*="Stop"], .stop-button',
    "RESPONSE_CONTAINER": 'message-content, .message-content, [data-test-id="message-content"]'
}

POLL_INTERVAL_MS = 50
COMPLETION_CHECK_MS = 100

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
            if (rawEmit) {{
                payload.requestId = requestId;
                rawEmit(payload);
            }}
        }} catch (e) {{
            console.warn(`[${{requestId}}] Emit failed:`, e.message);
        }}
    }};

    /**
     * Robust Visibility Heuristic:
     * - Element exists
     * - AND has positive dimensions or client rects
     * - AND display is not 'none' / visibility is not 'hidden'
     * - Exception safe: falls back to raw existence if sandboxed ComputedStyle errors.
     */
    const isElementVisible = (el) => {{
        try {{
            if (!el) return false;
            const rect = el.getBoundingClientRect();
            if (!(rect.width || rect.height || el.offsetWidth || el.offsetHeight || el.getClientRects().length)) {{
                return false;
            }}
            const style = window.getComputedStyle(el);
            return style.display !== 'none' && style.visibility !== 'hidden';
        }} catch (e) {{
            return !!el;
        }}
    }};

    /**
     * Normalize Snapshot:
     * - Removes known trailing transient cursor indicators (▋, ▌, █, etc.)
     * - Removes zero-width spaces and trailing whitespaces
     */
    const normalizeSnapshot = (text) => {{
        if (!text) return "";
        return text.replace(/[\\u200B]*[\\u2580-\\u258F█▌▋]+$/, "");
    }};

    /**
     * Helper to verify if the raw DOM text has probable model-generated content
     * or active generation indicators, excluding pure whitespaces and zero-width artifacts.
     */
    const hasMeaningfulContent = (text) => {{
        if (!text) return false;
        return text.replace(/[\\s\\u200B\\u200C\\u200D\\uFEFF]+/g, "").length > 0;
    }};

    // Initialize Global Request Registry (Singleton for the window)
    try {{
        window.__gemini_observers = window.__gemini_observers || {{}};
    }} catch (e) {{
        console.warn("Global observers registry blocked (sandboxed iframe context):", e.message);
    }}
    
    // Idempotent Cleanup Definition (Singleton for the window)
    try {{
        window.__gemini_stop_observer = window.__gemini_stop_observer || ((rId) => {{
            try {{
                const s = window.__gemini_observers && window.__gemini_observers[rId];
                if (!s || s.cleanedUp) return;
                
                // INVARIANT: cleanup is one-way and terminal
                s.cleanedUp = true;
                s.done = true;
                
                // 1. Stop all polling
                if (s.intervals) {{
                    s.intervals.forEach(clearInterval);
                    s.intervals = [];
                }}
                
                // 2. Disconnect DOM observer (synchronous)
                if (s.observer) {{
                    s.observer.disconnect();
                    s.observer = null;
                }}
                
                // 3. Clear from registry to prevent memory leak
                if (window.__gemini_observers) {{
                    delete window.__gemini_observers[rId];
                }}
                console.log(`[${{rId}}] Observer destroyed.`);
            }} catch (err) {{
                console.warn("Stop observer error:", err.message);
            }}
        }});
    }} catch (e) {{
        console.warn("Global stop observer injection blocked:", e.message);
    }}

    // Cleanup any existing observer for the same ID (Safety for rapid reuse)
    try {{
        if (window.__gemini_stop_observer) {{
            window.__gemini_stop_observer(requestId);
        }}
    }} catch (e) {{}}

    // Request State Ownership
    const state = {{
        requestId: requestId,
        initialMessageCount: document.querySelectorAll('{SELECTORS["RESPONSE_CONTAINER"]}').length,
        responseContainer: null,
        lastSnapshot: "",
        lastTextContent: "", // Optimized layout-bypass filter
        observer: null,
        started: false,
        done: false,
        cleanedUp: false,
        intervals: []
    }};

    try {{
        if (window.__gemini_observers) {{
            window.__gemini_observers[requestId] = state;
        }}
    }} catch (e) {{}}

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
        state.lastTextContent = "";
        
        state.observer = new MutationObserver(() => {{
            try {{
                // INVARIANT: Mutation callbacks must respect terminal state
                if (state.cleanedUp || state.done) return;
                
                // Verify container is still attached to document
                if (!element || !element.isConnected) return;
                
                /**
                 * Performance Optimization (Reflow Bypass):
                 * Comparing textContent is 100x faster than innerText because it does not 
                 * force style recalculations or browser layouts. We only proceed to fetch 
                 * layout-aware innerText if the raw text content itself has changed.
                 */
                const textContent = element.textContent || "";
                if (textContent === state.lastTextContent) return;
                
                let rawSnapshot = element.innerText || "";
                
                // If raw DOM has meaningful content or active cursor, transition started to true
                if (hasMeaningfulContent(rawSnapshot)) {{
                    state.started = true;
                }}
                
                // Reduce Mutation Noise: Normalize repeated inline spaces/tabs to a single space
                rawSnapshot = rawSnapshot.replace(/[ \t]+/g, ' ');
                const currentSnapshot = normalizeSnapshot(rawSnapshot);
                
                // Ignore empty/no-op snapshots
                if (!rawSnapshot.trim()) return;
                if (currentSnapshot === state.lastSnapshot) return;

                // Re-verify terminal state after layout recalculation delay
                if (state.cleanedUp || state.done) return;
                
                state.started = true;
                const payload = computeDelta(currentSnapshot);
                state.lastSnapshot = currentSnapshot;
                state.lastTextContent = textContent;
                
                emit(payload);
            }} catch (e) {{
                console.warn(`[${{requestId}}] Observer callback error:`, e.message);
            }}
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
        try {{
            if (state.cleanedUp || state.done) return;
            
            const allResponses = document.querySelectorAll('{SELECTORS["RESPONSE_CONTAINER"]}');
            if (allResponses.length > state.initialMessageCount) {{
                const latest = allResponses[allResponses.length - 1];
                if (latest !== state.responseContainer) {{
                    state.responseContainer = latest;
                    startObservation(state.responseContainer);
                }}
            }}
        }} catch (e) {{}}
    }}, {POLL_INTERVAL_MS});
    state.intervals.push(pollForContainer);

    // 2. Completion Polling Loop
    const checkCompletion = setInterval(() => {{
        try {{
            if (state.cleanedUp || state.done) return;
            
            const sendButton = document.querySelector('{SELECTORS["SEND_BUTTON"]}');
            const stopButton = document.querySelector('{SELECTORS["STOP_BUTTON"]}');
            const isStopVisible = isElementVisible(stopButton);
            const isGenerating = (sendButton && sendButton.disabled) || isStopVisible;

            // INVARIANT: Transition to 'started' is strictly one-way
            if (isGenerating && !state.started) {{
                state.started = true;
                emit({{type: "started"}});
            }}

            // INVARIANT: Completion required a detected start and a stable idle state
            if (state.started && state.responseContainer && sendButton && !sendButton.disabled && !isStopVisible) {{
                // Terminal State Transition
                state.done = true;
                
                try {{
                    const rawSnapshot = state.responseContainer.innerText || state.responseContainer.textContent || "";
                    const normalized = rawSnapshot.replace(/[ \t]+/g, ' ');
                    const finalSnapshot = normalizeSnapshot(normalized);
                    if (finalSnapshot && finalSnapshot !== state.lastSnapshot) {{
                        emit(computeDelta(finalSnapshot));
                    }}
                }} catch (err) {{}}
                
                console.log(`[${{requestId}}] Generation finished.`);
                emit({{type: "done"}});
                
                // Self-Cleanup
                if (window.__gemini_stop_observer) {{
                    window.__gemini_stop_observer(requestId);
                }}
            }}
        }} catch (e) {{}}
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
