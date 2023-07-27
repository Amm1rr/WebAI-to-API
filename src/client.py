import requests
import json
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# response = requests.post(url, json=data)

# if response.ok:
#     print("1")
#     print(response)
#     result = response.json()
#     print(result["response"])
# else:
#     print("Error:", response.status_code, response.text)


# url = "http://127.0.0.1:8000/chatgpt"
# url = "http://localhost:8000/claude"
# url = "http://localhost:8000/bard"
# ai = "/V1/chat/completions"
ai = "chatgpt"
url = "http://localhost:8000/" + ai

data = {
    "message": "3+6= ?",
    "stream": True,
    "session_id": "",
}
if data["stream"] == True:
    with requests.post(url, json=data, stream=True) as response:
        try:
            for line in response.iter_lines(decode_unicode=True):
                if line:
                    try:
                        print(line)
                    except json.JSONDecodeError:
                        print("Invalid JSON format in line:", line)
        except requests.exceptions.RequestException as e:
            print(f"Error: {e}")
        except:
            print(f"Error: !")

else:
    try:
        response = requests.post(url, json=data, stream=False)
        # response.raise_for_status()  # Raise an exception if the request was not successful

        result = response.json()
        print(result)
        # return result["response"]

        # Check if the response is successful (status code 200)
        # if response.status_code == 200:
        #     pass
        # else:
        #     print(f"Error: {response.status_code} - {response.text}")

    except requests.exceptions.RequestException as e:
        print(f"Error occurred: {e}")

    # result = response.json()
    # print(result["response"])


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
