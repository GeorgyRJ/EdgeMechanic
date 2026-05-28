import requests
import json
from pydantic import ValidationError

# Assuming HVACJobRecord is imported from your schema file

from schemas import HVACJobRecord 

def generate_job_record(prompt: str) -> HVACJobRecord:
    # 1. Read the grammar file (.gbnf)
    # This file forces llama.cpp to output strictly according to the schema
    try:
        with open("grammars/hvac_job.gbnf", "r", encoding="utf-8") as f:
            grammar_str = f.read()
    except FileNotFoundError:
        raise FileNotFoundError("grammars/hvac_job.gbnf not found.")

    # 2. Prepare the Payload for the POST request to the llama.cpp server
    url = "http://127.0.0.1:8080/completion"
    
    # Wrap the prompt with HVAC context so the model understands its role
    system_prompt = "You are an expert HVAC technician assistant. Extract the job details from the log and format it strictly as JSON."
    full_prompt = f"{system_prompt}\n\nLog:\n{prompt}\n\nJSON Output:\n"

    payload = {
        "prompt": full_prompt,
        "grammar": grammar_str,
        "n_predict": 512,       # Maximum expected token length for the response
        "temperature": 0.0,     # Crucial: Set to 0.0 to minimize hallucination (Greedy decoding)
    }

    # Send the Request (Set a timeout in case the server hangs)
    response = requests.post(url, json=payload, timeout=60)
    response.raise_for_status() # Throw an error immediately if HTTP Status is not 200 OK

    # 3. Parse the JSON response back into an HVACJobRecord
    response_data = response.json()
    result_text = response_data.get("content", "").strip()

    try:
        # Pydantic V2 uses model_validate_json to parse the string into an object and validate types
        job_record = HVACJobRecord.model_validate_json(result_text)
        return job_record
    
    except ValidationError as e:
        print("Error: AI response does not match the schema")
        print("Raw Output:", result_text)
        raise e

# ==========================================
# Test execution (You can run this if llama-server is running)
# ==========================================
if __name__ == "__main__":
    test_log = """
    Tech: Mitsubishi Heavy Duty AC is leaking water heavily. How to fix?
    AI: I recommend checking the drain pan and flushing the drain pipe. There might be a slime clog.
    (Tech flushed the drain pipe and reassembled. Status: Completed)
    """
    
    try:
        record = generate_job_record(test_log)
        print("Data extraction successful!")
        print(record.model_dump_json(indent=2))
    except Exception as error:
        print(f"Test Failed: {error}")