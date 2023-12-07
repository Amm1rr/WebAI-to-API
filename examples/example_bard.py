import requests
import sys
import json
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

user_input = input("Enter your prompt: ")

# Set the API endpoint
#
API_ENDPOINT = "http://127.0.0.1:8000/bard"

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
# session_idTS:   str
#       - You can set 'SessionTS' here or configure it in the Config.conf file.
#
# stream:       bool
#       - We can choose between response Streaming or Normal handling for data retrieval.
#
params = {
    "message": user_input,
    "session_id": "",
    "session_idTS": "",
    "stream": stream,
}


### Make the API request
#
if params["stream"] == True:
    with requests.post(API_ENDPOINT, json=params, stream=True) as response:
        try:
            for line in response.iter_lines(decode_unicode=True):
                if line:
                    try:
                        # Print the response
                        print(line, end="", flush=True)
                    except json.JSONDecodeError:
                        print("Invalid JSON format in line:", line)
        except TypeError as e:
            print(f"Error!: {str(e)}")

else:
    try:
        response = requests.post(API_ENDPOINT, json=params, stream=False)
        # response.raise_for_status()  # Raise an exception if the request was not successful

        # Print the response
        result = response.json()
        print(result["choices"])

        # return result["response"]

        # Check if the response is successful (status code 200)
        # if response.status_code == 200:
        #     pass
        # else:
        #     print(f"Error: {response.status_code} - {response.text}")

    except TypeError as e:
        print(f"Error occurred: {e}")

