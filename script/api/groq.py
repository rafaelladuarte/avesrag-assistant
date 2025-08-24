import requests
import random


class GroqAPI:
    def __init__(self, list_api_key: list):
        self._list_api_key = list_api_key

    def send_prompt(self, prompt):

        choice_key = random.choice(self._list_api_key)
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {choice_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "llama-3.1-8b-instant",
                "messages": [{"role": "user", "content": prompt}]
            }
        )
        response.raise_for_status()

        return response.json()["choices"][0]["message"]["content"]
