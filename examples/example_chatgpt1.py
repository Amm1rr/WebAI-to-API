import requests
import sys
import json
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


input_text = input("Enter your prompt: ")

API_ENDPOINT = "http://127.0.0.1:8000/chatgpt"  # Replace with the actual server URL if it's hosted elsewhere

stream = True

## Argument for stream if available
#
if len(sys.argv) > 1:
    arg1 = sys.argv[1]  # The first argument
    stream = arg1.upper() == "TRUE"
### Set the model parameters
##
# message:      str
#       - Enter prompt
#
# session_id:   str
#       - You can set 'Session' here or configure it in the Config.conf file.
#
# stream:       bool
#       - We can choose between response Streaming or Normal handling for data retrieval.
#
params = {
    "message": input_text,
    "session_id": "",
    "stream": stream
}

### Make the API request
#
response = requests.post(
    API_ENDPOINT,
    headers={"Content-Type": "application/json", "Authorization": "Bearer "},
    json=params,
    timeout=360,
    stream=params["stream"],
)
response.raise_for_status()

if not params["stream"]:
    data = response.text
    print(data)
    # print(data["choices"][0]["message"]["content"])  # type: ignore
    exit()
for line in response.iter_lines():
    data = line.lstrip(b"data: ").decode("utf-8")
    if data == "[DONE]":  # type: ignore
        break
    if not data:
        continue

    print(data, end="", flush=True)

    # data = json.loads(data)  # type: ignore
    # delta = data["choices"][0]["delta"]  # type: ignore
    # if "content" not in delta:
    #     continue
    # print(delta["content"])
