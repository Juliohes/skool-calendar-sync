#!/usr/bin/env python3
"""
Skool Calendar Sync - Generates an ICS file from Skool calendar events.
Uses the Next.js SSP endpoint with Unix timestamp calDate parameter.
"""

import os
import re
import sys
import gzip
import time
import calendar
import urllib.request
import urllib.error
from datetime import datetime, timezone
from icalendar import Calendar, Event
import json


GROUP_SLUG = os.environ.get("SKOOL_GROUP_SLUG", "ia-masters-automations")
COOKIE = os.environ.get("SKOOL_COOKIE", "")
OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "docs/calendar.ics")
MONTHS_AHEAD = int(os.environ.get("MONTHS_AHEAD", "3"))


def decode_response(response_bytes):
    """Decode response bytes, handling gzip compression."""
    if response_bytes[:2] == b'\x1f\x8b':
        return gzip.decompress(response_bytes).decode("utf-8")
    return response_bytes.decode("utf-8")


def get_build_id(cookie):
    """Fetch the Next.js buildId from the Skool calendar page HTML."""
    url = f"https://www.skool.com/{GROUP_SLUG}/calendar"
    req = urllib.request.Request(url)
    req.add_header("Cookie", cookie)
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    req.add_header("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
    req.add_header("Accept-Encoding", "gzip, deflate")
    req.add_header("Accept-Language", "es-ES,es;q=0.9,en;q=0.8")
    req.add_header("Referer", "https://www.skool.com/")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            html = decode_response(raw)
            # Extract buildId from __NEXT_DATA__
            match = re.search(r'"buildId"s*:s*"([^"]+)"', html)
            if match:
                build_id = match.group(1)
                print(f"buildId found: {build_id}")
                return build_id
            print("ERROR: buildId not found in HTML")
            return None
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} getting calendar page: {e.reason}")
        if e.code == 403:
            print("Cookie expired or invalid. Please update SKOOL_COOKIE secret.")
            sys.exit(0)
        return None
    except Exception as e:
        print(f"Error getting calendar page: {e}")
        return None


def get_month_timestamp(year, month):
    """Get Unix timestamp (seconds) for the first day of a given month (UTC)."""
    dt = datetime(year, month, 1, tzinfo=timezone.utc)
    return int(dt.timestamp())


def fetch_events_for_month(build_id, cookie, year, month):
    """Fetch calendar events for a specific month using Next.js SSP endpoint."""
    cal_date = get_month_timestamp(year, month)
    url = (
        f"https://www.skool.com/_next/data/{build_id}/"
        f"{GROUP_SLUG}/calendar.json"
        f"?calDate={cal_date}&group={GROUP_SLUG}"
    )
    print(f"Fetching {year}-{month:02d}: {url}")
    req = urllib.request.Request(url)
    req.add_header("Cookie", cookie)
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    req.add_header("Accept", "application/json, */*;q=0.8")
    req.add_header("Accept-Encoding", "gzip, deflate")
    req.add_header("Accept-Language", "es-ES,es;q=0.9,en;q=0.8")
    req.add_header("Referer", f"https://www.skool.com/{GROUP_SLUG}/calendar?calDate={cal_date}")
    req.add_header("x-nextjs-data", "1")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            text = decode_response(raw)
            data = json.loads(text)
            events = data.get("pageProps", {}).get("events", [])
            print(f"  -> {len(events)} events")
            return events
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code} for {year}-{month:02d}: {e.reason}")
        if e.code == 403:
            print("Cookie expired. Please update SKOOL_COOKIE secret.")
            sys.exit(0)
        return []
    except Exception as e:
        print(f"  Error for {year}-{month:02d}: {e}")
        return []


def event_to_ical(event):
    """Convert a Skool event dict to an icalendar Event object."""
    cal_event = Event()
    meta = event.get("metadata", {})
    title = meta.get("title", "Skool Event")
    description = meta.get("description", "")
    event_id = event.get("id", "")
    start_str = event.get("startTime", "")
    end_str = event.get("endTime", "")
    # Parse start/end times
    try:
        start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
    except Exception:
        return None
    try:
        end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
    except Exception:
        end_dt = start_dt
    cal_event.add("summary", title)
    cal_event.add("dtstart", start_dt)
    cal_event.add("dtend", end_dt)
    cal_event.add("description", description)
    cal_event.add("uid", f"{event_id}@skool.com")
    # Add URL
    cal_event.add("url", f"https://www.skool.com/{GROUP_SLUG}/calendar")
    return cal_event


def main():
    if not COOKIE:
        print("ERROR: SKOOL_COOKIE environment variable not set.")
        sys.exit(1)

    print(f"Group: {GROUP_SLUG}")
    print(f"Fetching {MONTHS_AHEAD} months of events...")

    # Step 1: Get buildId
    build_id = get_build_id(COOKIE)
    if not build_id:
        print("ERROR: Could not obtain buildId. Exiting.")
        sys.exit(1)

    # Step 2: Fetch events for current month and months ahead
    now = datetime.now(timezone.utc)
    all_events = {}
    for delta in range(MONTHS_AHEAD + 1):
        month_num = (now.month - 1 + delta) % 12 + 1
        year_num = now.year + (now.month - 1 + delta) // 12
        events = fetch_events_for_month(build_id, COOKIE, year_num, month_num)
        for ev in events:
            ev_id = ev.get("id", "")
            if ev_id not in all_events:
                all_events[ev_id] = ev

    print(f"Total unique events: {len(all_events)}")

    # Step 3: Generate ICS
    cal = Calendar()
    cal.add("prodid", "-//Skool Calendar Sync//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("x-wr-calname", f"Skool: {GROUP_SLUG}")
    cal.add("x-wr-timezone", "UTC")

    added = 0
    for ev in all_events.values():
        cal_event = event_to_ical(ev)
        if cal_event:
            cal.add_component(cal_event)
            added += 1

    print(f"Events added to calendar: {added}")

    # Step 4: Write ICS file
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "wb") as f:
        f.write(cal.to_ical())
    print(f"Calendar written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
