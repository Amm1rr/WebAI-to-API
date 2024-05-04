import requests
import argparse
import configparser
import sys
import os
import json
import webai2api.utils.utility


# Define the base URL of your API
base_url = "http://localhost:8000"

# Define the endpoint URLs
claude_endpoint = "/claude"
gemini_endpoint = "/gemini"
tochatgpt_endpoint = "/v1/chat/completions"

CONFIG_FILE_NAME = "Config.conf"
CONFIG_FOLDER = os.getcwd()
if "/src" not in CONFIG_FOLDER:
    CONFIG_FOLDER += "/webai2api"
CONFIG_FILE_PATH = os.path.join(CONFIG_FOLDER, CONFIG_FILE_NAME)

# Create a sample message payload for Claude (non-streaming)
claude_message_payload_non_streaming = {
    "message": "Who are you?",
    "stream": False,
    "conversation_id": None
}

# Create a sample message payload for Claude (streaming)
claude_message_payload_streaming = {
    "message": "Who are you? Can you explain the concept of machine learning?",
    "stream": True,
    "conversation_id": None
}

# Create a sample message payload for Gemini (non-streaming)
gemini_message_payload_non_streaming = {
    "message": "Who are you?",
    "stream": False
}

# Create a sample message payload for Gemini (streaming)
gemini_message_payload_streaming = {
    "message": "Who are you? Can you tell me about the history of AI?",
    "stream": True
}

# Create a sample message payload for Chat Complation response
tochatgpt_message_payload = {
    "messages": [{ "role": "user", "content": "What is your name?" }],
    "stream": False
}

parser = argparse.ArgumentParser(description="Test WEBAI Server")
parser.add_argument("--host", type=str, default="localhost", help="Host IP address")
parser.add_argument("--port", type=int, default=8000, help="Port number")
parser.add_argument("--model", type=str, default="*", help="AI Model to test (Claude/Gemini/*)")
parser.add_argument("--v1", type=str, default="*", help="AI Model to test at v1/chat/completions endpoint (Claude/Gemini/*)")
args = parser.parse_args()
model = args.model.lower()
model_v1 = args.v1.lower()

SEPRATOR = f"--------------------------------------------------"
SEPRATOR2 = f"----------------------------"
print(SEPRATOR)

if (model == "claude" or model == "*"):
    # Test Claude (streaming)
    print("Testing Claude (streaming):")
    print(SEPRATOR2)
    
    response = requests.post(f"{base_url}{claude_endpoint}", json=claude_message_payload_streaming, stream=True)

    if response.status_code == 200:
        for chunk in response.iter_content(chunk_size=None):
            if chunk:
                print(chunk.decode(), end="", flush=True)
    else:
        print(f"Request failed with status code: {response.status_code}")
        print(f"Response text: {response.text}")


    # Test Claude (non-streaming)
    print("\n", SEPRATOR)
    print("Testing Claude (non-streaming):")
    print(SEPRATOR2)
    response = requests.post(f"{base_url}{claude_endpoint}", json=claude_message_payload_non_streaming)

    if response.status_code == 200:
        try:
            response_data = response.json()
            print(response_data)
        except requests.exceptions.JSONDecodeError as e:
            print(f"Error: {e}")
            print(f"Response text: {response.text}")
    else:
        print(f"Request failed with status code: {response.status_code}")
        print(f"Response text: {response.text}")

    print(SEPRATOR)

if (model == "gemini" or model == "*"):
    # Test Gemini (non-streaming)
    print("Testing Gemini:")
    print(SEPRATOR2)
    
    response = requests.post(f"{base_url}{gemini_endpoint}", json=gemini_message_payload_non_streaming)

    if response.status_code == 200:
        try:
            print(response.text)
        except requests.exceptions.JSONDecodeError as e:
            print(f"Error: {e}")
            print(f"Response text: {response.text}")
    else:
        print(f"Request failed with status code: {response.status_code}")
        print(f"Response text: {response.text}")

    print(SEPRATOR)

if (model == "tochatgpt" or model == "*"):
    
    original_model_response = webai2api.utils.utility.ResponseModel(CONFIG_FILE_PATH)
    
    
    if (model_v1 == "claude" or model_v1 == "*"):
        
        #### Save Claude
        requests.post(f"{base_url}/api/config/save", headers={"Content-Type": "application/json"}, data=json.dumps({"Model": "Claude"}))    
        
        # Test ClaudeToChatGPT (non-streaming)
        print("Testing Claude to ChatGPT :")
        print(SEPRATOR2)
        response = requests.post(f"{base_url}{tochatgpt_endpoint}", json=tochatgpt_message_payload)

        if response.status_code == 200:
            try:
                print(response.text)
            except requests.exceptions.JSONDecodeError as e:
                print(f"Error: {e}")
                print(f"Response text: {response.text}")
        else:
            print(f"Request failed with status code: {response.status_code}")
            print(f"Response text: {response.text}")

        print(SEPRATOR)
    
    if (model_v1 == "gemini" or model_v1 == "*"):
        
        #### Save Gemini 
        requests.post(f"{base_url}/api/config/save", headers={"Content-Type": "application/json"}, data=json.dumps({"Model": "Gemini"}))
        
        # Test GeminiToChatGPT (non-streaming)
        print("Testing Gemini to ChatGPT :")
        print(SEPRATOR2)
        response = requests.post(f"{base_url}{tochatgpt_endpoint}", json=tochatgpt_message_payload)

        if response.status_code == 200:
            try:
                print(response.text)
            except requests.exceptions.JSONDecodeError as e:
                print(f"Error: {e}")
                print(f"Response text: {response.text}")
        else:
            print(f"Request failed with status code: {response.status_code}")
            print(f"Response text: {response.text}")
        
        
        print(SEPRATOR)
    
    #### Save Default AI (Claude or Gemini)
    if original_model_response != "Gemini":
        requests.post(f"{base_url}/api/config/save", headers={"Content-Type": "application/json"}, data=json.dumps({"Model": "Gemini"}))

# # Test Gemini (streaming)
# print("Testing Gemini (streaming):")
# response = requests.post(f"{base_url}{gemini_endpoint}", json=gemini_message_payload_streaming, stream=True)

# if response.status_code == 200:
#     for chunk in response.iter_content(chunk_size=None):
#         if chunk:
#             print(chunk.decode(), end="")
# else:
#     print(f"Request failed with status code: {response.status_code}")
#     print(f"Response text: {response.text}")