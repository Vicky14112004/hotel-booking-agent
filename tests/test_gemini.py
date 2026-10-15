
from dotenv import load_dotenv
from google import genai
from google.genai import types
import os

# Load environment variables from the project root .env file
load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    raise ValueError(
        "GEMINI_API_KEY not found. Please check your .env file."
    )

# Initialize Gemini client
client = genai.Client(
    api_key=api_key,
    http_options=types.HttpOptions(timeout=15000),
)

try:
    print("Sending generation request...", flush=True)

    response = client.models.generate_content(
        model="gemini-3.8-flash",
        contents="Reply with exactly: Gemini connection successful.",
        config=types.GenerateContentConfig(
            max_output_tokens=30
        ),
    )

    print("\nResponse received:", flush=True)
    print(response.text, flush=True)

except Exception as exc:
    print(
        f"\nGeneration failed: {type(exc).__name__}: {exc}",
        flush=True,
    )

finally:
    client.close()

