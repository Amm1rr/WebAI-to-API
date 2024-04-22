import requests
import sys

user_input = input("Enter your prompt: ")

## Set the API endpoint
#
API_ENDPOINT = "http://localhost:8000/claude"

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
# stream:       bool
#       - We can choose between response Streaming or Normal handling for data retrieval.
#
params = {
    "message": user_input,
    "stream": stream,
}

if not stream:

    response = requests.post(f"{API_ENDPOINT}", json=params)
    if response.status_code == 200:
        try:
            response_data = response.json()
            print(response_data)
        except requests.exceptions.JSONDecodeError as e:
            print(f"JSON Load Error: {response.text}")
            print(f"Error: {e}")
    else:
        print(f"{response.text}")
else:
    response = requests.post(f"{API_ENDPOINT}", json=params, stream=True)
    
    if response.status_code == 200:
        for chunk in response.iter_content(chunk_size=None):
            if chunk:
                print(chunk.decode(), end="", flush=True)
    else:
        # print(f"Request failed with status code: {response.status_code}")
        print(f"{response.text}")
