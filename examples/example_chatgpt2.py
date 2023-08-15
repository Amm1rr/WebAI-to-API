import requests
import sys

stream = True

## Argument for stream if available
#
if len(sys.argv) > 1:
    arg1 = sys.argv[1]  # The first argument
    stream = arg1.upper() == "TRUE"

def chat_with_bot(input_text) -> str:

    # Set the API endpoint
    #
    API_ENDPOINT = "http://127.0.0.1:8000/chatgpt"  # Replace with the actual server URL if it's hosted elsewhere


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
    try:
        response = requests.post(API_ENDPOINT, json=params)
        return response.text
            # return result["response"]

    except requests.exceptions.RequestException as e:
        return f"Error occurred: {e}"


if __name__ == "__main__":

    input_text = input("Enter your prompt: ")

    response = chat_with_bot(input_text)

    # Print the response
    print("Chatbot:", response)
