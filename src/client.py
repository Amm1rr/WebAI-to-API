import requests
import json

# url = "http://localhost:8000/chatgpt"
url = "http://localhost:8000/bard"
# url = "http://localhost:8000/claude"

data = {
    "session_id": "",
    "message": "Count 0 to 10",
    "stream": False
}

if data["stream"] == True:
    with requests.post(url, json=data, stream=False) as response:
        for line in response.iter_lines(decode_unicode=True):
            if line:
                try:
                    print(line)
                except json.JSONDecodeError:
                    print("Invalid JSON format in line:", line)
                    # print(line)
else:
    response = requests.post(url, json=data, stream=False)
    print(response.text)


# import requests
# import sseclient

# # Replace these values with the actual session_id and message you want to send
# session_id = "your_session_id"
# message_text = "Hello, Claude!"

# # Assuming the API is running locally at http://127.0.0.1:8000
# base_url = "http://127.0.0.1:8000"
# endpoint = "/claude"

# url = f"{base_url}{endpoint}"

# # Prepare the request payload
# payload = {
#     "session_id": session_id,
#     "message": message_text
# }

# # Make the POST request to the /claude endpoint with stream=True
# response = requests.post(url, json=payload, stream=True)

# # Check if the response is successful (status code 200)
# if response.status_code == 200:
#     # Use the sseclient library to parse the stream of events
#     client = sseclient.SSEClient(response.iter_lines())
#     for event in client.events():
#         # Parse the SSE event data (JSON) and handle it as needed
#         data = event.data
#         print("Received SSE event:", data)
# else:
#     print(f"Error: {response.status_code} - {response.text}")
