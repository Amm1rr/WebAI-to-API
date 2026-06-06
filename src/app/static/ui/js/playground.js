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
  var fileInput = form.querySelector("[data-file-input]");
  var fileList = form.querySelector("[data-file-list]");
  var fileAttachmentSummary = form.querySelector("[data-file-attachment-summary]");
  var fileGuidance = form.querySelector("[data-file-guidance]");
  var clearFilesButton = form.querySelector("[data-clear-files]");
  var modelSelect = form.querySelector('select[name="model"]');
  var promptField = form.querySelector('textarea[name="prompt"]');
  var activeController = null;
  var lastConversationId = "";
  var lastReusedConversation = "";
  var lastReusedConversationSeen = false;
  var lastModel = "";
  var MAX_FILE_COUNT = 8;
  var MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024;
  var MAX_TOTAL_FILE_SIZE_BYTES = 40 * 1024 * 1024;
  var ALLOWED_FILE_SUFFIXES = {
    "application/pdf": [".pdf"],
    "application/msword": [".doc"],
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [".docx"],
    "text/plain": [".txt", ".text", ".md", ".csv", ".log"],
    "text/markdown": [".md", ".markdown"],
    "text/csv": [".csv"],
    "image/png": [".png"],
    "image/jpeg": [".jpg", ".jpeg"],
    "image/webp": [".webp"],
    "image/gif": [".gif"]
  };

  function setText(node, value) {
    node.textContent = value == null || value === "" ? "n/a" : String(value);
  }

  function setFileGuidance(text, variant) {
    fileGuidance.textContent = text;
    fileGuidance.classList.remove("warning", "error");
    if (variant) {
      fileGuidance.classList.add(variant);
    }
  }

  function clearFileValidation() {
    fileInput.setCustomValidity("");
  }

  function setFileValidationError(message) {
    fileInput.setCustomValidity(message);
    fileInput.reportValidity();
  }

  function getFileExtension(name) {
    var index = String(name || "").lastIndexOf(".");
    return index === -1 ? "" : String(name).slice(index).toLowerCase();
  }

  function formatBytes(bytes) {
    if (bytes < 1024) {
      return bytes + " B";
    }
    if (bytes < 1024 * 1024) {
      return (bytes / 1024).toFixed(bytes < 10240 ? 1 : 0) + " KiB";
    }
    return (bytes / (1024 * 1024)).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0) + " MiB";
  }

  function getSelectedFiles() {
    return Array.prototype.slice.call(fileInput.files || []);
  }

  function isPlaywrightModel(model) {
    return String(model || "").indexOf("playwright/") === 0;
  }

  function isAtlasModel(model) {
    return String(model || "").indexOf("atlas/") === 0;
  }

  function isGeminiModel(model) {
    var value = String(model || "");
    return value.indexOf("atlas/") !== 0 && value.indexOf("playwright/") !== 0 && value.indexOf("gemini") === 0;
  }

  function getFileSelectionStatus(model, files) {
    var count = files.length;
    var totalSize = 0;

    if (!count) {
      return {
        ok: true,
        mode: "none",
        message: "File attachments work only with Gemini WebAPI. Gemini Playwright and Atlas do not support file parts. Exact text/file interleaving is not preserved by Gemini WebAPI."
      };
    }

    if (count > MAX_FILE_COUNT) {
      return {
        ok: false,
        mode: "error",
        message: "Too many files selected. Maximum allowed is " + MAX_FILE_COUNT + "."
      };
    }

    for (var i = 0; i < files.length; i += 1) {
      var file = files[i];
      var size = file.size || 0;
      var mimeType = String(file.type || "").toLowerCase();
      var suffix = getFileExtension(file.name);
      var allowedSuffixes = ALLOWED_FILE_SUFFIXES[mimeType];

      totalSize += size;

      if (size > MAX_FILE_SIZE_BYTES) {
        return {
          ok: false,
          mode: "error",
          message: "File \"" + file.name + "\" exceeds the maximum allowed size of " + formatBytes(MAX_FILE_SIZE_BYTES) + "."
        };
      }

      if (!mimeType || !allowedSuffixes || allowedSuffixes.indexOf(suffix) === -1) {
        return {
          ok: false,
          mode: "error",
          message: "File \"" + file.name + "\" uses an unsupported file type for this playground."
        };
      }
    }

    if (totalSize > MAX_TOTAL_FILE_SIZE_BYTES) {
      return {
        ok: false,
        mode: "error",
        message: "Total selected file size exceeds the maximum allowed size of " + formatBytes(MAX_TOTAL_FILE_SIZE_BYTES) + "."
      };
    }

    if (isPlaywrightModel(model)) {
      return {
        ok: false,
        mode: "error",
        message: "File attachments are not supported for Playwright models."
      };
    }

    if (isAtlasModel(model)) {
      return {
        ok: false,
        mode: "error",
        message: "File attachments are not supported for Atlas models."
      };
    }

    if (isGeminiModel(model)) {
      return {
        ok: true,
        mode: "warning",
        message: "File attachments require [Gemini] backend = webapi. Exact text/file interleaving is not preserved by Gemini WebAPI."
      };
    }

    return {
      ok: true,
      mode: "warning",
      message: "File attachments are only supported when the request routes to Gemini WebAPI."
    };
  }

  function renderFileList(files) {
    fileList.textContent = "";
    if (!files.length) {
      clearFilesButton.disabled = true;
      return;
    }

    var fragment = document.createDocumentFragment();
    files.forEach(function (file) {
      var item = document.createElement("li");
      item.textContent = file.name + " (" + formatBytes(file.size || 0) + ")";
      fragment.appendChild(item);
    });
    fileList.appendChild(fragment);
    clearFilesButton.disabled = false;
  }

  function setFileAttachmentSummary(files, status) {
    if (!files.length) {
      fileAttachmentSummary.textContent = "No files attached.";
      return;
    }

    var names = files.map(function (file) {
      return file.name + " (" + formatBytes(file.size || 0) + ")";
    }).join(", ");

    if (status && status.ok) {
      fileAttachmentSummary.textContent = "Selected files will be attached on submit: " + names + ".";
      return;
    }

    fileAttachmentSummary.textContent = "Selected files: " + names + ".";
  }

  function syncFileSelectionFeedback() {
    var files = getSelectedFiles();
    var status = getFileSelectionStatus(String(modelSelect.value || "").trim(), files);
    clearFileValidation();

    if (!files.length) {
      renderFileList(files);
      setFileAttachmentSummary(files, status);
      setFileGuidance("File attachments work only with Gemini WebAPI. Gemini Playwright and Atlas do not support file parts. Exact text/file interleaving is not preserved by Gemini WebAPI.", null);
      return status;
    }

    renderFileList(files);
    setFileAttachmentSummary(files, status);

    if (!status.ok) {
      setFileGuidance(status.message, "error");
      setFileValidationError(status.message);
      return status;
    }

    if (status.mode === "warning") {
      setFileGuidance(status.message, "warning");
      return status;
    }

    setFileGuidance("File attachments work only with Gemini WebAPI. Gemini Playwright and Atlas do not support file parts. Exact text/file interleaving is not preserved by Gemini WebAPI.", null);
    return status;
  }

  function clearSelectedFiles() {
    fileInput.value = "";
    clearFileValidation();
    renderFileList([]);
    setFileAttachmentSummary([]);
    setFileGuidance("File attachments work only with Gemini WebAPI. Gemini Playwright and Atlas do not support file parts. Exact text/file interleaving is not preserved by Gemini WebAPI.", null);
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

  function readFileAsDataUrl(file, signal) {
    return new Promise(function (resolve, reject) {
      if (signal.aborted) {
        reject(new DOMException("The operation was aborted.", "AbortError"));
        return;
      }

      var reader = new FileReader();
      reader.onload = function () {
        resolve(String(reader.result || ""));
      };
      reader.onerror = function () {
        reject(reader.error || new Error("Failed to read file."));
      };
      reader.onabort = function () {
        reject(new DOMException("The operation was aborted.", "AbortError"));
      };

      signal.addEventListener("abort", function () {
        if (reader.readyState === 1) {
          reader.abort();
        }
      }, { once: true });

      reader.readAsDataURL(file);
    });
  }

  async function buildRequest(signal) {
    var data = new FormData(form);
    var prompt = String(data.get("prompt") || "").trim();
    var conversationId = String(data.get("conversation_id") || "").trim();
    var model = String(data.get("model") || "").trim();
    var stream = data.get("stream") === "on";
    var files = getSelectedFiles();
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

    if (!files.length) {
      return payload;
    }

    var selectionStatus = getFileSelectionStatus(model, files);
    if (!selectionStatus.ok) {
      setFileGuidance(selectionStatus.message, "error");
      setFileValidationError(selectionStatus.message);
      throw new Error(selectionStatus.message);
    }

    if (selectionStatus.mode === "warning") {
      setFileGuidance(selectionStatus.message, "warning");
    }

    var totalSize = 0;
    // File-only submissions are intentionally unsupported in the playground MVP.
    var parts = [{
      type: "text",
      text: prompt
    }];

    for (var i = 0; i < files.length; i += 1) {
      if (signal.aborted) {
        throw new DOMException("The operation was aborted.", "AbortError");
      }

      var file = files[i];
      totalSize += file.size || 0;
      if (totalSize > MAX_TOTAL_FILE_SIZE_BYTES) {
        var totalMessage = "Total selected file size exceeds the maximum allowed size of " + formatBytes(MAX_TOTAL_FILE_SIZE_BYTES) + ".";
        setFileGuidance(totalMessage, "error");
        setFileValidationError(totalMessage);
        throw new Error(totalMessage);
      }

      var dataUrl = await readFileAsDataUrl(file, signal);
      if (signal.aborted) {
        throw new DOMException("The operation was aborted.", "AbortError");
      }

      parts.push({
        type: "file",
        file: {
          filename: file.name,
          file_data: dataUrl
        }
      });
    }

    payload.messages[0].content = parts;
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

    activeController = new AbortController();
    resetOutput();
    setBusy(true);

    try {
      var payload = await buildRequest(activeController.signal);
      if (payload.stream) {
        await submitStreaming(payload, activeController.signal);
      } else {
        await submitBuffered(payload, activeController.signal);
      }
      clearSelectedFiles();
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

  fileInput.addEventListener("change", function () {
    syncFileSelectionFeedback();
  });

  modelSelect.addEventListener("change", function () {
    syncFileSelectionFeedback();
  });

  clearFilesButton.addEventListener("click", function () {
    clearSelectedFiles();
  });

  syncFileSelectionFeedback();
}());
