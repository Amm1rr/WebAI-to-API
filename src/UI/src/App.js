import React, { useState, useEffect } from "react";
import DOMPurify from "dompurify"; // Sanitize INPUT
import "./App.css";

function App() {
  const [selectedOption, setSelectedOption] = useState("");
  const [googleSessionKey, setGoogleSessionKey] = useState("");
  const [googleSessionKeyTS, setGoogleSessionKeyTS] = useState("");
  const [googleSessionKeyCC, setGoogleSessionKeyCC] = useState("");
  const [claudeSessionKey, setClaudeSessionKey] = useState("");
  const [firstLoad, setFirstLoad] = useState(true);

  const getModelDescription = () => {
    if (selectedOption === "Gemini") {
      return "Gemini excels in advanced natural language understanding and generation.";
    } else {
      return "Claude 3 is optimized for conversational responses and complex reasoning tasks.";
    }
  };

  const handleSelectChange = (event) => {
    const sanitizedValue = DOMPurify.sanitize(event.target.value);
    setSelectedOption(sanitizedValue);
    saveConfig(sanitizedValue);
  };

  useEffect(() => {
    if (firstLoad) {
      fetchData();
      setFirstLoad(false);
    }
  }, [firstLoad]);

  const fetchData = async () => {
    try {
      const response = await fetch("/api/config");
      const data = await response.text();
      const responseText = data;
      const cleanedText = responseText.replace(/^"|"$/g, "").trim();
      const unescapedText = cleanedText.replace(/\\"/g, '"').trim();
      const jsonData = JSON.parse(unescapedText);

      const aimodel = jsonData.Main.model;
      if (aimodel) {
        setSelectedOption(aimodel);
      }

      const geminiSessions = jsonData.Gemini;
      if (geminiSessions) {
        setGoogleSessionKey(jsonData.Gemini.session_id);
        setGoogleSessionKeyTS(jsonData.Gemini.session_idts);
        setGoogleSessionKeyCC(jsonData.Gemini.session_idcc);
      } else {
        const respCookie = await fetch("/api/config/getgeminikey");
        const respCookieText = await respCookie.json();

        try {
          const parsedData = JSON.parse(respCookieText);
          const session_id = parsedData[0][1];
          const session_idts = parsedData[1][1];
          const session_idcc = parsedData[2][1];
          setGoogleSessionKey(session_id);
          setGoogleSessionKeyTS(session_idts);
          setGoogleSessionKeyCC(session_idcc);
          // claude = parsedData[3]["sessionKey"];
        } catch (error) {
          console.error("Error parsing JSON:", error);
        }
      }

      const claudeSessionKey = jsonData.Claude;
      if (claudeSessionKey) {
        setClaudeSessionKey(jsonData.Claude.cookie);
      } else {
        const respCookie = await fetch("/api/config/getclaudekey");
        const respCookieText = await respCookie.json();

        setClaudeSessionKey(respCookieText["Claude"]);
      }
    } catch (error) {
      console.error("Error fetching Claude cookie session:", error);
    }
  };

  const saveConfig = async (modelname) => {
    try {
      const response = await fetch("/api/config/save", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          Model: modelname,
        }),
      });

      if (response.ok) {
        console.log(modelname + " saved as the default model.");
      } else {
        console.error(
          "Error: failed to save config file:",
          response.statusText
        );
      }
    } catch (error) {
      console.error("Error: failed saving config file:", error);
    }
  };

  return (
    <div className="App">
      <header className="App-header">
        <div className="Header-content">
          <h1 className="App-title">WebAI to API</h1>
          <hr />
          <div className="Select-container">
            <label htmlFor="option-select" className="Select-label">
              Default Model :
            </label>
            <select
              id="option-select"
              value={selectedOption}
              onChange={handleSelectChange}
              className="Select-dropdown"
            >
              <option value="Claude">Claude</option>
              <option value="Gemini">Gemini</option>
            </select>
          </div>
          <p className="Endpoint-description">
            Default Response for{" "}
            <strong>
              <a
                href="/docs"
                target="_blank"
                rel="noopener noreferrer"
                className="Footer-link"
              >
                v1/chat/completion
              </a>
            </strong>{" "}
            endpoint.
          </p>
          <label className="Model-description">
            <strong>Selected Model:</strong> {getModelDescription()}
          </label>
          <div className="googleSessionsContainer">
            <div className="googleSession">
              <label htmlFor="googleSession" className="googleSession-label">
                Google Session ID :
              </label>
              <input
                type="text"
                name="googleSession"
                id="googleSession"
                value={googleSessionKey}
              />
            </div>
            <div className="googleSession">
              <label
                htmlFor="googleSession-value"
                className="googleSession-label"
              >
                Google Session IDTS :
              </label>
              <input
                type="text"
                name="googleSession-value"
                id="googleSession-value"
                className="googleSession-value"
                value={googleSessionKeyTS}
              />
            </div>
            <div className="googleSession">
              <label
                htmlFor="googleSession-label"
                className="googleSession-label"
              >
                Google Session IDCC :
              </label>
              <input
                type="text"
                name="googleSession-label"
                id="googleSession-label"
                className="googleSession-value"
                value={googleSessionKeyCC}
              />
            </div>
          </div>
          <div className="claudeSessionsContainer">
            <div className="claudeSession">
              <label
                htmlFor="claudeSession-value"
                className="claudeSession-label"
              >
                Claude Session Key :
              </label>
              <input
                type="text"
                name="claudeSession-value"
                id="claudeSession-value"
                className="claudeSession-value"
                value={claudeSessionKey}
              />
            </div>
          </div>
        </div>
      </header>
      <footer className="App-footer">
        <a
          href="https://github.com/amm1rr/WebAI-to-API"
          target="_blank"
          rel="noopener noreferrer"
          className="Footer-link"
        >
          Made with ❤️
        </a>
      </footer>
    </div>
  );
}

export default App;
