import requests

def chat_with_bot(input_text):
    url = "http://127.0.0.1:8000/chatgpt"  # Replace with the actual server URL if it's hosted elsewhere

    data = {
        "message": input_text,
        "session_id": "",
        "stream": Frue
    }

    try:
        response = requests.post(url, json=data)
        # response.raise_for_status()  # Raise an exception if the request was not successful

        result = response.json()
        return result
        # return result["response"]
    except requests.exceptions.RequestException as e:
        return f"Error occurred: {e}"

if __name__ == "__main__":
    input_text = input("Enter your message: ")
    response = chat_with_bot(input_text)
    print("Chatbot:", response)
