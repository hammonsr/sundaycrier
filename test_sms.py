# test_sms.py

import os
from dotenv import load_dotenv
from twilio.rest import Client

load_dotenv()

client = Client(
    os.getenv("TWILIO_ACCOUNT_SID"),
    os.getenv("TWILIO_AUTH_TOKEN")
)

message = client.messages.create(
    body="SundayCrier test message",
    from_=os.getenv("TWILIO_FROM_NUMBER"),
    to=os.getenv("SMS_RECIPIENTS")
)

print("Sent:", message.sid)