import requests

API_ENDPOINT = "http://localhost:8000/gemini"

user_input = input("Enter your prompt: ")
params = {"message": user_input}

response = requests.post(f"{API_ENDPOINT}", json=params)

if response.status_code == 200:
    print("Gemini:")
    print(response.text) 
else:
    print(f"Request failed with status code: {response.status_code}")