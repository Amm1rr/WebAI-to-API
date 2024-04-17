import requests

# Define the base URL of your API
base_url = "http://localhost:8000"

# Define the endpoint URLs
claude_endpoint = "/claude"
gemini_endpoint = "/gemini"

# Create a sample message payload for Claude (non-streaming)
claude_message_payload_non_streaming = {
    "message": "Who are you?",
    "stream": False
}

# Create a sample message payload for Claude (streaming)
claude_message_payload_streaming = {
    "message": "Who are you? Can you explain the concept of machine learning?",
    "stream": True
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

# Test Claude (non-streaming)
print("\n------------------------------")
print("Testing Claude (non-streaming):")
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

print("\n------------------------------")  # Add a newline for better readability

# Test Claude (streaming)
print("Testing Claude (streaming):")
response = requests.post(f"{base_url}{claude_endpoint}", json=claude_message_payload_streaming, stream=True)

if response.status_code == 200:
    for chunk in response.iter_content(chunk_size=None):
        if chunk:
            print(chunk.decode(), end="")
else:
    print(f"Request failed with status code: {response.status_code}")
    print(f"Response text: {response.text}")

print("\n------------------------------")  # Add a newline for better readability

# Test Gemini (non-streaming)
print("Testing Gemini (non-streaming):")
response = requests.post(f"{base_url}{gemini_endpoint}", json=gemini_message_payload_non_streaming)

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

print("\n------------------------------")  # Add a newline for better readability

# Test Gemini (streaming)
print("Testing Gemini (streaming):")
response = requests.post(f"{base_url}{gemini_endpoint}", json=gemini_message_payload_streaming, stream=True)

if response.status_code == 200:
    for chunk in response.iter_content(chunk_size=None):
        if chunk:
            print(chunk.decode(), end="")
else:
    print(f"Request failed with status code: {response.status_code}")
    print(f"Response text: {response.text}")