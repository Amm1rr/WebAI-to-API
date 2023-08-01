import requests

user_input = input("Enter your prompt: ")

# Set the API endpoint
#
API_ENDPOINT = "http://localhost:8000/claude"


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
    "stream": False,
}

# Make the API request
#
response = requests.post(API_ENDPOINT, json=params)

# Print the response
#
try:
    print(response.json())
except:
    print(response.text)
