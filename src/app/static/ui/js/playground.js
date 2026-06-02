(function () {
  "use strict";

  var root = document.querySelector("[data-playground]");
  if (!root) {
    return;
  }

  var form = root.querySelector("[data-playground-form]");
  var output = root.querySelector("[data-response-output]");
  var state = root.querySelector("[data-playground-state]");
  var submitButton = root.querySelector("[data-submit-button]");
  var stopButton = root.querySelector("[data-stop-button]");
  var metaConversationId = root.querySelector("[data-meta-conversation-id]");
  var metaReused = root.querySelector("[data-meta-reused]");
  var metaModel = root.querySelector("[data-meta-model]");
  var metaStatus = root.querySelector("[data-meta-status]");
  var promptField = form.querySelector('textarea[name="prompt"]');
  var activeController = null;
  var lastConversationId = "";
  var lastReusedConversation = "";
  var lastReusedConversationSeen = false;
  var lastModel = "";

  function setText(node, value) {
    node.textContent = value == null || value === "" ? "n/a" : String(value);
  }

  function setBusy(isBusy) {
    submitButton.disabled = isBusy;
    stopButton.disabled = !isBusy;
  }

  function setState(label) {
    state.textContent = label;
    metaStatus.textContent = label;
  }

  function resetOutput() {
    output.textContent = "";
    lastConversationId = "";
    lastReusedConversation = "";
    lastReusedConversationSeen = false;
    lastModel = "";
    setText(metaConversationId, lastConversationId);
    setText(metaReused, null);
    setText(metaModel, lastModel);
    setState("Sending");
  }

  function validatePrompt() {
    var prompt = String(promptField.value || "").trim();
    if (!prompt) {
      promptField.setCustomValidity("Prompt cannot be empty.");
      promptField.reportValidity();
      return false;
    }

    promptField.setCustomValidity("");
    return true;
  }

  function buildRequest() {
    var data = new FormData(form);
    var prompt = String(data.get("prompt") || "").trim();
    var conversationId = String(data.get("conversation_id") || "").trim();
    var model = String(data.get("model") || "").trim();
    var stream = data.get("stream") === "on";
    var payload = {
      model: model,
      stream: stream,
      messages: [
        {
          role: "user",
          content: prompt
        }
      ]
    };

    if (conversationId) {
      payload.conversation_id = conversationId;
    }

    return payload;
  }

  function applyMetadata(chunk, fallbackModel) {
    if (chunk.conversation_id) {
      lastConversationId = chunk.conversation_id;
    }
    if (Object.prototype.hasOwnProperty.call(chunk, "reused_conversation")) {
      lastReusedConversation = chunk.reused_conversation;
      lastReusedConversationSeen = true;
    }
    if (chunk.model) {
      lastModel = chunk.model;
    } else if (fallbackModel) {
      lastModel = fallbackModel;
    }

    setText(metaConversationId, lastConversationId);
    setText(metaReused, lastReusedConversationSeen ? lastReusedConversation : null);
    setText(metaModel, lastModel);
  }

  function extractAssistantText(data) {
    var choice = data && data.choices && data.choices[0];
    if (!choice) {
      return "";
    }
    if (choice.delta && choice.delta.content) {
      return choice.delta.content;
    }
    if (choice.message && choice.message.content) {
      return choice.message.content;
    }
    return "";
  }

  function renderError(error) {
    output.textContent = error.message || String(error);
    setState("Error");
  }

  async function submitBuffered(payload, signal) {
    var response = await fetch("/v1/chat/completions", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(payload),
      signal: signal
    });

    var body = await response.json().catch(function () {
      return {};
    });

    if (!response.ok) {
      throw new Error(body.detail || "Request failed with HTTP " + response.status);
    }

    output.textContent = extractAssistantText(body);
    applyMetadata(body, payload.model);
    setState("Complete");
  }

  function processSseLine(line, payload) {
    if (!line || line.indexOf("data:") !== 0) {
      return false;
    }

    var data = line.slice(5).trim();
    if (data === "[DONE]") {
      setState("Complete");
      return true;
    }

    var parsed = JSON.parse(data);
    output.textContent += extractAssistantText(parsed);
    applyMetadata(parsed, payload.model);
    return false;
  }

  async function submitStreaming(payload, signal) {
    var response = await fetch("/v1/chat/completions", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(payload),
      signal: signal
    });

    if (!response.ok) {
      var errorBody = await response.json().catch(function () {
        return {};
      });
      throw new Error(errorBody.detail || "Request failed with HTTP " + response.status);
    }

    if (!response.body) {
      throw new Error("Streaming is not supported by this browser.");
    }

    setState("Streaming");
    var reader = response.body.getReader();
    var decoder = new TextDecoder();
    var buffer = "";

    while (true) {
      var result = await reader.read();
      if (result.done) {
        break;
      }

      buffer += decoder.decode(result.value, { stream: true });
      var lines = buffer.split(/\r?\n/);
      buffer = lines.pop() || "";

      for (var i = 0; i < lines.length; i += 1) {
        if (processSseLine(lines[i], payload)) {
          await reader.cancel();
          return;
        }
      }
    }

    if (buffer.trim()) {
      processSseLine(buffer.trim(), payload);
    }
  }

  form.addEventListener("submit", async function (event) {
    event.preventDefault();

    if (!validatePrompt()) {
      return;
    }

    if (activeController) {
      activeController.abort();
    }

    var payload = buildRequest();
    activeController = new AbortController();
    resetOutput();
    setBusy(true);

    try {
      if (payload.stream) {
        await submitStreaming(payload, activeController.signal);
      } else {
        await submitBuffered(payload, activeController.signal);
      }
    } catch (error) {
      if (error.name === "AbortError") {
        setState("Stopped");
      } else {
        renderError(error);
      }
    } finally {
      activeController = null;
      setBusy(false);
    }
  });

  stopButton.addEventListener("click", function () {
    if (activeController) {
      activeController.abort();
    }
  });

  promptField.addEventListener("input", function () {
    promptField.setCustomValidity("");
  });
}());
