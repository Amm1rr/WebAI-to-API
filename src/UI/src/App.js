import React, { useState } from "react";
import "./App.css";

function App() {
  const [selectedOption, setSelectedOption] = useState("Claude3");

  const handleSelectChange = (event) => {
    setSelectedOption(event.target.value);
  };

  const getModelDescription = () => {
    if (selectedOption === "Claude3") {
      return "Claude 3 is optimized for conversational responses and complex reasoning tasks.";
    } else if (selectedOption === "GoogleGemini") {
      return "Gemini excels in advanced natural language understanding and generation.";
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
              <option value="Claude3">Claude 3</option>
              <option value="GoogleGemini">Google Gemini</option>
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
          <p className="Model-description">
            <strong>Selected Model:</strong> {getModelDescription()}
          </p>
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
