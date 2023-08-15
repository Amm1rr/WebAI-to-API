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
# session_id (session_id=Cookie):   str
#       - You can set 'Session' here or configure it in the Config.conf file.
#
# stream:       bool
#       - We can choose between response Streaming or Normal handling for data retrieval.
#
params = {
    "message": user_input,
    "session_id": "",
    "stream": stream,
}

## Make the API request
#
response = requests.post(API_ENDPOINT, json=params)

## Print the response
#
try:
    if stream:
        print(response.text, end="", flush=True)
    else:
        print(response.text)
except Exception as e:
    print(f"Error: {e}")

