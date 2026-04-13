#!/usr/bin/env python3

import os
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from typing import List, Dict
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from twilio.rest import Client
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

# ========================================
# CONFIG PREP, SETUP and DEFINITIONS
# ========================================

# Load environment variables from .env file and injecting them such that
# os.getenv() can access them
load_dotenv()

# Helper to get env vars and sqwak exceptions if something is missing
# and yes, copilot, I want it to say sqwak and I know it's overkill but whatever...
# it needs to be maintainable and stop suggesting endings to my comments!!!
def get_env(key: str, required: bool = True, default: str = None):
    value = os.getenv(key, default)
    if required and not value:
        raise ValueError(f"Missing required environment variable: {key}")
    return value

TIMEZONE = ZoneInfo("America/Chicago")  # adjust if needed

def parse_event_time(event):
    start = event["start"]

    # All-day event
    if "date" in start:
        return {
            "dt": None,
            "time_str": "All Day",
            "day": start["date"]
        }

    # Timed event
    dt = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
    dt = dt.astimezone(TIMEZONE)

    return {
        "dt": dt,
        "time_str": dt.strftime("%I:%M %p").lstrip("0"),
        "day": dt.strftime("%Y-%m-%d")
    }

# ========== SMS CONFIG ==========
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER")
SMS_RECIPIENTS = os.getenv("SMS_RECIPIENTS", "").split(",")

# ========== EMAIL CONFIG ==========
SENDGRID_API_KEY = get_env("SENDGRID_API_KEY")
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECIPIENTS = os.getenv("EMAIL_RECIPIENTS", "").split(",")

# ========== Google CONFIG ==========
GOOGLE_TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "token.json")

print(f"CONFIG CHECK:")
print(f"TWILIO_ACCOUNT_SID: {TWILIO_ACCOUNT_SID}")
print(f"SMS_RECIPIENTS: {SMS_RECIPIENTS}")
print(f"EMAIL_SENDER: {EMAIL_SENDER}")
print(f"GOOGLE_TOKEN_FILE: {GOOGLE_TOKEN_FILE}")

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
        calendarId="family00786821299684947027@group.calendar.google.com",
        timeMin=start.isoformat(),
        timeMax=end.isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    return events_result.get("items", [])


# This is a testing function to list available calendars tied in a google account
# it's not really useful for anything other than debugging
# I struggled a lot with getting the right events until I realized there are 
# multiple calendars avaialbe to a single account
def list_calendars(service):
    print("\nAvailable calendars:\n")

    calendars = service.calendarList().list().execute()

    for cal in calendars.get("items", []):
        print(f"- {cal['summary']}")
        print(f"  id: {cal['id']}\n")


def group_events(events):
    grouped = {}

    for event in events:
        title = event.get("summary", "No Title")
        parsed = parse_event_time(event)

        day_key = parsed["day"]  # YYYY-MM-DD

        grouped.setdefault(day_key, []).append({
            "time": parsed["time_str"],
            "title": title,
            "dt": parsed["dt"]
        })

    return grouped


def sort_grouped_events(grouped):
    for day in grouped:
        grouped[day].sort(
            key=lambda e: (e["dt"] is not None, e["dt"] or datetime.min)
        )
    return grouped

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


# special formatter for SMS to keep the length down and just show late events (3pm or later)
def get_late_event_titles(items):
    late = []

    for e in items:
        if e["dt"] is None:
            continue  # skip all-day here

        if e["dt"].hour >= 15:
            late.append(e["title"])

    return late

# =========================
# FORMATTERS
# =========================

def format_sms(grouped):
    lines = []

    first_day = next(iter(grouped))
    dt = datetime.fromisoformat(first_day)
    lines.append(dt.strftime("Week of %b %d\n"))

    for day, items in grouped.items():
        day_label = format_day_label(day).split()[0]

        total = len(items)

        # All-day events
        all_day = [e for e in items if e["time"] == "All Day"]

        # Late events (after 3PM)
        late_titles = get_late_event_titles(items)

        line = f"{day_label}: {total} events"

        if all_day:
            summary = ", ".join(e["title"] for e in all_day[:1])
            extra = total - len(all_day)

            if extra > 0:
                summary += f" + {extra} events"

            line = f"{day_label}: {summary}"

        elif late_titles:
            # keep it tight — limit to 3 items
            preview = ", ".join(late_titles[:3])

            if len(late_titles) > 3:
                preview += "..."

            line += f" ({preview})"

        lines.append(line)

    return "\n".join(lines)


def format_email(grouped):
    lines = ["Weekly Family Schedule\n"]

    for day, items in grouped.items():
        lines.append(format_day_label(day))

        for e in items:
            lines.append(f"  - {e['time']} {e['title']}")

        lines.append("")  # blank line between days

    return "\n".join(lines)


def format_day_label(day_str: str) -> str:
    dt = datetime.fromisoformat(day_str)

    return dt.strftime("%a (%b %d)")

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
    msg = Mail(
        from_email=EMAIL_SENDER,
        to_emails=[e.strip() for e in EMAIL_RECIPIENTS if e.strip()],
        subject="Weekly Family Schedule",
        plain_text_content=message
    )

    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(msg)
        print(f"Email sent (status {response.status_code})")
    except Exception as e:
        print(f"Email error: {e}")

        if hasattr(e, "body"):
            print("DETAILS:")
            print(e.body)


# =========================
# MAIN EXECUTION
# =========================


def run():
    print("Fetching calendar events...\n")

    service = get_calendar_service()
    events = fetch_events(service)

    print(f"Fetched {len(events)} events\n")

    grouped = group_events(events)
    grouped = sort_grouped_events(grouped)

    sms = format_sms(grouped)
    email = format_email(grouped)

    print("=== SMS PREVIEW ===\n")
    print(sms)

    print("\n=== EMAIL PREVIEW ===\n")
    print(email)
    print("\n=====================\n")
    send_email(email)

if __name__ == "__main__":
    run()