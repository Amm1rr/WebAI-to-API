import React, { useState, useEffect } from "react";
import "./App.css";

function App() {
  const [selectedOption, setSelectedOption] = useState("");
  const [googleSessionKey, setGoogleSessionKey] = useState("");
  const [googleSessionKeyTS, setGoogleSessionKeyTS] = useState("");
  const [googleSessionKeyCC, setGoogleSessionKeyCC] = useState("");
  const [claudeSessionKey, setClaudeSessionKey] = useState("");
  const [firstLoad, setFirstLoad] = useState(true); // State to track first load

  const getModelDescription = () => {
    if (selectedOption === "Gemini") {
      return "Gemini excels in advanced natural language understanding and generation.";
    } else {
      return "Claude 3 is optimized for conversational responses and complex reasoning tasks.";
    }
  };

  const handleSelectChange = (event) => {
    setSelectedOption(event.target.value);
    saveConfig(event.target.value);
  };

  useEffect(() => {
    if (firstLoad) {
      // Only fetch data on the first load
      fetchData();
      setFirstLoad(false); // Set first load to false after fetching data
    }
  }, [firstLoad]);

  const fetchData = async () => {
    try {
      const response = await fetch("http://localhost:8000/api/config");
      const data = await response.text();
      // console.log("Data: " + data);

      const responseText = data;
      const cleanedText = responseText.replace(/^"|"$/g, "").trim(); // Remove surrounding quotes and trim whitespace
      const unescapedText = cleanedText.replace(/\\"/g, '"').trim(); // Unescape double quotes
      const jsonData = JSON.parse(unescapedText);

      // console.log("JSON Data: " + unescapedText);

      const aimodel = jsonData.Main.model;
      if (aimodel) {
        setSelectedOption(aimodel);
      }

      const geminiSession = jsonData.Gemini;
      if (geminiSession) {
        setGoogleSessionKey(jsonData.Gemini.session_id);
        setGoogleSessionKeyTS(jsonData.Gemini.session_idts);
        setGoogleSessionKeyCC(jsonData.Gemini.session_idcc);
      }
      const claudeSessionKey = jsonData.Claude;
      if (claudeSessionKey) {
        setClaudeSessionKey(jsonData.Claude.cookie);
      }
    } catch (error) {
      console.error("Error fetching config file:", error);
    }
  };

  const saveConfig = async (modelname) => {
    try {
      const response = await fetch("http://localhost:8000/api/config/save", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          Model: modelname,
        }),
      });

      if (response.ok) {
        console.log("Config file saved successfully.");
      } else {
        console.error("Failed to save config file:", response.statusText);
      }
    } catch (error) {
      console.error("Error saving config file:", error);
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
              Default AI:
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
                href="http://localhost:8000/docs"
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
              <label htmlFor=".googleSession" className="googleSession-label">
                Google Session ID :
              </label>
              <input
                type="text"
                name="googleSession"
                id="googleSession"
                value={googleSessionKey}
              ></input>
            </div>
            <div className="googleSession">
              <label
                htmlFor=".googleSession-value"
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
              ></input>
            </div>
            <div className="googleSession">
              <label
                htmlFor=".googleSession-label"
                className="googleSession-label"
              >
                Google Session ID CC :
              </label>
              <input
                type="text"
                name="googleSession-label"
                id="googleSession-label"
                className="googleSession-value"
                value={googleSessionKeyCC}
              ></input>
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
              ></input>
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
