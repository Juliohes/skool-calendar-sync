#!/usr/bin/env python3
"""
Skool Calendar Sync - Generates an ICS file from Skool calendar events.
Uses the Next.js SSP endpoint with Unix timestamp calDate parameter.
No external dependencies required.
"""

import os
import re
import sys
import gzip
import json
import uuid
import urllib.request
import urllib.error
from datetime import datetime, timezone


GROUP_SLUG = os.environ.get("SKOOL_GROUP_SLUG", "ia-masters-automations")
COOKIE = os.environ.get("SKOOL_COOKIE", "")
OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "docs/calendar.ics")
MONTHS_AHEAD = int(os.environ.get("MONTHS_AHEAD", "3"))


def decode_response(response_bytes):
    """Decode response bytes, handling gzip compression."""
    if len(response_bytes) >= 2 and response_bytes[0] == 0x1f and response_bytes[1] == 0x8b:
        return gzip.decompress(response_bytes).decode("utf-8")
    return response_bytes.decode("utf-8")


def get_build_id(cookie):
    """Fetch the Next.js buildId from the Skool calendar page HTML."""
    url = "https://www.skool.com/{}/calendar".format(GROUP_SLUG)
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
            match = re.search(r'"buildId"\s*:\s*"([^"]+)"', html)
            if match:
                build_id = match.group(1)
                print("buildId found: {}".format(build_id))
                return build_id
            print("ERROR: buildId not found in HTML")
            return None
    except urllib.error.HTTPError as e:
        print("HTTP {} getting calendar page: {}".format(e.code, e.reason))
        if e.code == 403:
            print("Cookie expired or invalid. Please update SKOOL_COOKIE secret.")
            sys.exit(0)
        return None
    except Exception as e:
        print("Error getting calendar page: {}".format(e))
        return None


def get_month_timestamp(year, month):
    """Get Unix timestamp (seconds) for the first day of a given month (UTC)."""
    dt = datetime(year, month, 1, tzinfo=timezone.utc)
    return int(dt.timestamp())


def fetch_events_for_month(build_id, cookie, year, month):
    """Fetch calendar events for a specific month using Next.js SSP endpoint."""
    cal_date = get_month_timestamp(year, month)
    url = "https://www.skool.com/_next/data/{}/{}/calendar.json?calDate={}&group={}".format(
        build_id, GROUP_SLUG, cal_date, GROUP_SLUG
    )
    print("Fetching {}-{:02d}: {}".format(year, month, url))
    req = urllib.request.Request(url)
    req.add_header("Cookie", cookie)
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    req.add_header("Accept", "application/json, */*;q=0.8")
    req.add_header("Accept-Encoding", "gzip, deflate")
    req.add_header("Accept-Language", "es-ES,es;q=0.9,en;q=0.8")
    req.add_header("Referer", "https://www.skool.com/{}/calendar?calDate={}".format(GROUP_SLUG, cal_date))
    req.add_header("x-nextjs-data", "1")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            text = decode_response(raw)
            data = json.loads(text)
            events = data.get("pageProps", {}).get("events", [])
            print("  -> {} events".format(len(events)))
            return events
    except urllib.error.HTTPError as e:
        print("  HTTP {} for {}-{:02d}: {}".format(e.code, year, month, e.reason))
        if e.code == 403:
            print("Cookie expired. Please update SKOOL_COOKIE secret.")
            sys.exit(0)
        return []
    except Exception as e:
        print("  Error for {}-{:02d}: {}".format(year, month, e))
        return []


def format_dt_ical(dt_str):
    """Convert ISO datetime string to iCal datetime format (YYYYMMDDTHHMMSSZ)."""
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        # Convert to UTC
        dt_utc = dt.astimezone(timezone.utc)
        return dt_utc.strftime("%Y%m%dT%H%M%SZ")
    except Exception:
        return None


def escape_ical(text):
    """Escape special characters for iCal format."""
    if not text:
        return ""
    text = str(text)
    text = text.replace("\\", "\\\\")
    text = text.replace(";", "\\;")
    text = text.replace(",", "\\,")
    text = text.replace("\n", "\\n")
    text = text.replace("\r", "")
    return text


def fold_line(line):
    """Fold long iCal lines at 75 characters."""
    result = []
    while len(line.encode("utf-8")) > 75:
        result.append(line[:75])
        line = " " + line[75:]
    result.append(line)
    return "\r\n".join(result)


def events_to_ics(events, group_slug):
    """Convert list of Skool events to ICS file content."""
    lines = []
    lines.append("BEGIN:VCALENDAR")
    lines.append("VERSION:2.0")
    lines.append("PRODID:-//Skool Calendar Sync//EN")
    lines.append("CALSCALE:GREGORIAN")
    lines.append("METHOD:PUBLISH")
    lines.append("X-WR-CALNAME:Skool: {}".format(group_slug))
    lines.append("X-WR-TIMEZONE:UTC")
    
    added = 0
    for ev in events:
        meta = ev.get("metadata", {})
        title = meta.get("title", "Skool Event")
        description = meta.get("description", "")
        event_id = ev.get("id", str(uuid.uuid4()))
        start_str = ev.get("startTime", "")
        end_str = ev.get("endTime", "")
        
        dtstart = format_dt_ical(start_str)
        dtend = format_dt_ical(end_str)
        if not dtstart:
            continue
        if not dtend:
            dtend = dtstart
        
        lines.append("BEGIN:VEVENT")
        lines.append("UID:{}@skool.com".format(event_id))
        lines.append("DTSTART:{}".format(dtstart))
        lines.append("DTEND:{}".format(dtend))
        lines.append("SUMMARY:{}".format(escape_ical(title)))
        if description:
            lines.append("DESCRIPTION:{}".format(escape_ical(description)))
        lines.append("URL:https://www.skool.com/{}/calendar".format(group_slug))
        lines.append("END:VEVENT")
        added += 1
    
    lines.append("END:VCALENDAR")
    print("Events added to calendar: {}".format(added))
    return "\r\n".join(lines) + "\r\n"


def main():
    if not COOKIE:
        print("ERROR: SKOOL_COOKIE environment variable not set.")
        sys.exit(1)

    print("Group: {}".format(GROUP_SLUG))
    print("Fetching {} months of events...".format(MONTHS_AHEAD))

    # Step 1: Get buildId
    build_id = get_build_id(COOKIE)
    if not build_id:
        print("ERROR: Could not obtain buildId. Exiting.")
        sys.exit(1)

    # Step 2: Fetch events for current month + months ahead
    now = datetime.now(timezone.utc)
    all_events = {}
    for delta in range(MONTHS_AHEAD + 1):
        total_months = now.month - 1 + delta
        month_num = total_months % 12 + 1
        year_num = now.year + total_months // 12
        events = fetch_events_for_month(build_id, COOKIE, year_num, month_num)
        for ev in events:
            ev_id = ev.get("id", "")
            if ev_id not in all_events:
                all_events[ev_id] = ev

    print("Total unique events: {}".format(len(all_events)))

    # Step 3: Generate ICS content
    ics_content = events_to_ics(list(all_events.values()), GROUP_SLUG)

    # Step 4: Write ICS file
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(ics_content)
    print("Calendar written to {}".format(OUTPUT_PATH))


if __name__ == "__main__":
    main()
