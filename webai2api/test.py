import requests
import argparse
import configparser
import sys
import os
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

# Create a sample message payload for Gemini (streaming)
tochatgpt_message_payload = {
    "message": "Who are you? Can you tell me about the history of AI?",
    "stream": False
}

parser = argparse.ArgumentParser(description="Test WEBAI Server")
parser.add_argument("--host", type=str, default="localhost", help="Host IP address")
parser.add_argument("--port", type=int, default=8000, help="Port number")
parser.add_argument("--model", type=str, default="*", help="Model to test")
args = parser.parse_args()
model = args.model.lower()

SEPRATOR = f"------------------------------"
print(SEPRATOR)

if (model == "claude" or model == "*"):
    # Test Claude (streaming)
    print("Testing Claude (streaming):")
    print(SEPRATOR)
    
    response = requests.post(f"{base_url}{claude_endpoint}", json=claude_message_payload_streaming, stream=True)

    if response.status_code == 200:
        for chunk in response.iter_content(chunk_size=None):
            if chunk:
                print(chunk.decode(), end="", flush=True)
    else:
        print(f"Request failed with status code: {response.status_code}")
        print(f"Response text: {response.text}")

    print("\n", SEPRATOR)
    print("Testing Claude (non-streaming):")
    print(SEPRATOR)
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
    print(SEPRATOR)
    
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
    
    config = configparser.ConfigParser()
    config['Main'] = {}
    if "Claude" not in original_model_response:
        config['Main']['model'] = "Claude"
        with open(CONFIG_FILE_PATH, 'w') as configfile:
            config.write(configfile)
    
    # Test CloudeToChatGPT (non-streaming)
    print("Testing Cloude to ChatGPT :")
    print(SEPRATOR)
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
    
    config['Main']['model'] = "Gemini"
    with open(CONFIG_FILE_PATH, 'w') as configfile:
        config.write(configfile)
    
    # Test GeminiToChatGPT (non-streaming)
    print("Testing Gemini to ChatGPT :")
    print(SEPRATOR)
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

    if original_model_response != "Gemini":
        config['Main']['model'] = "Gemini"
        with open(CONFIG_FILE_PATH, 'w') as configfile:
            config.write(configfile)

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