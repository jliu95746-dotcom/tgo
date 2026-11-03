"""Read a recognized question without granting it business authority."""

import json


def customer_question(message: str) -> str:
    """Unwrap only the server media envelope; the result remains untrusted."""
    _, separator, payload = message.partition("\n")
    if separator:
        try:
            data = json.loads(payload)
        except ValueError:
            data = None
        if isinstance(data, dict) and set(data) == {"recognized_customer_content"}:
            text = data["recognized_customer_content"]
            if isinstance(text, str) and text.strip():
                return text
    return message
