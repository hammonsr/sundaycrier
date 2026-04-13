#!/usr/bin/env python3

import os
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from typing import List, Dict

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from twilio.rest import Client


# =========================
# CONFIG (ENV-DRIVEN)
# =========================

TIMEZONE = timezone.utc  # adjust if needed

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER")
SMS_RECIPIENTS = os.getenv("SMS_RECIPIENTS", "").split(",")

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECIPIENTS = os.getenv("EMAIL_RECIPIENTS", "").split(",")

GOOGLE_TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "token.json")


# =========================
# CALENDAR CLIENT
# =========================

def get_calendar_service():
    creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE)
    service = build("calendar", "v3", credentials=creds)
    return service


def get_week_bounds():
    now = datetime.now(TIMEZONE)
    start = now - timedelta(days=now.weekday())  # Monday
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)
    return start, end


def fetch_events(service) -> List[Dict]:
    start, end = get_week_bounds()

    events_result = service.events().list(
        calendarId="primary",
        timeMin=start.isoformat(),
        timeMax=end.isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    return events_result.get("items", [])


# =========================
# PROCESSING
# =========================

def normalize_event(event: Dict) -> Dict:
    start = event["start"].get("dateTime", event["start"].get("date"))
    end = event["end"].get("dateTime", event["end"].get("date"))

    return {
        "title": event.get("summary", "No Title"),
        "start": start,
        "end": end,
        "location": event.get("location"),
    }


def process_events(events: List[Dict]) -> Dict[str, List[Dict]]:
    grouped = {}

    for event in events:
        e = normalize_event(event)
        dt = datetime.fromisoformat(e["start"].replace("Z", "+00:00"))
        day = dt.strftime("%A")

        grouped.setdefault(day, []).append({
            "time": dt.strftime("%I:%M %p").lstrip("0"),
            "title": e["title"]
        })

    return grouped


# =========================
# FORMATTERS
# =========================

def format_sms(grouped: Dict[str, List[Dict]]) -> str:
    lines = []
    week_label = datetime.now().strftime("Week of %b %d")
    lines.append(week_label)

    for day, events in grouped.items():
        lines.append(f"\n{day[:3]}:")
        for e in events:
            lines.append(f"- {e['time']} {e['title']}")

    return "\n".join(lines)


def format_email(grouped: Dict[str, List[Dict]]) -> str:
    lines = ["Weekly Family Schedule\n"]

    for day, events in grouped.items():
        lines.append(day)
        for e in events:
            lines.append(f"- {e['time']} – {e['title']}")
        lines.append("")

    return "\n".join(lines)


# =========================
# NOTIFICATIONS
# =========================

def send_sms(message: str):
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

    for recipient in SMS_RECIPIENTS:
        if recipient.strip():
            client.messages.create(
                body=message,
                from_=TWILIO_FROM_NUMBER,
                to=recipient.strip()
            )


def send_email(message: str):
    msg = MIMEText(message)
    msg["Subject"] = "Weekly Family Schedule"
    msg["From"] = EMAIL_SENDER
    msg["To"] = ", ".join(EMAIL_RECIPIENTS)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, EMAIL_RECIPIENTS, msg.as_string())


# =========================
# MAIN EXECUTION
# =========================

def run():
    print("Starting weekly digest job...")

    service = get_calendar_service()
    raw_events = fetch_events(service)

    if not raw_events:
        print("No events found for this week.")
        return

    grouped = process_events(raw_events)

    sms_message = format_sms(grouped)
    email_message = format_email(grouped)

    print("Sending SMS...")
    send_sms(sms_message)

    print("Sending Email...")
    send_email(email_message)

    print("Done.")


if __name__ == "__main__":
    run()