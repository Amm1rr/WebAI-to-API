# import requests

# API_ENDPOINT = "http://localhost:8000/claude"

# API_KEY = "YOUR_API_KEY"

# session = requests.Session()
# session.headers.update({
#   "Content-Type": "application/json",
#   "Authorization": f"Bearer {API_KEY}"
# })

# def ask(question):
#   data = {
#     "model": "gpt-3.5-turbo",
#     "stream": True,
#     "messages": [{"role": "user", "content": question}]
#   }

#   response = session.post(API_ENDPOINT, json=data)
#   return response.json()

# response = ask("Hello, how are you?")

# try:
#     print(response['choices'][0]['message']['content'])
# except:
#     print(response)

#####
# Normal Call (not stream)
#####

import requests

# Set the API endpoint
API_ENDPOINT = "http://localhost:8000/claude"

# Set your API key
API_KEY = "YOUR_API_KEY"

# Set the model parameters
params = {
    "prompt": "Hello how are you?",
    "max_tokens": 100,
    "temperature": 0.5,
    "stream": True,
    "model": "gpt-3.5-turbo",
    "messages": [{"role": "user", "content": "Hi, Who are you?"}]
}

# Set the headers
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {API_KEY}"
}

# Make the API request
response = requests.post(API_ENDPOINT, json=params, headers=headers)

# Print the response
try:
    print(response.json())
except:
    print(response)
