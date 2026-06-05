    log.warning("ADVERTENCIA: No se pudo conectar con Skool (HTTP 403 Forbidden).")
    log.warning("La SKOOL_COOKIE ha expirado. El ICS no se actualizara esta vez.")
    log.warning("  -> Para renovar: Settings > Secrets > SKOOL_COOKIE en GitHub")
    # Salir con codigo 0 para no generar emails de error del workflow
    sys.exit(0)

    log.info(f"Usando buildId: {build_id}")
    for month_offset in range(total_months + 1):
        target = start_date + timedelta(days=30 * month_offset)
        cal_date = target.strftime("%Y-%m-01")
        log.info(f"Obteniendo eventos (NextJS) para: {cal_date}")
        events = get_events_via_nextjs(build_id, cal_date)
        for ev in events:
            ev_id = ev.get("eventId") or ev.get("id") or str(ev)
            if ev_id not in seen_ids:
                seen_ids.add(ev_id)
                all_events.append(ev)

    return all_events

# --- Generar ICS -------------------------------------------------------------

def escape_ics(text):
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
    """RFC 5545: lineas de max 75 octetos."""
    result = []
    while len(line.encode("utf-8")) > 75:
        chunk = line[:75]
        while len(chunk.encode("utf-8")) > 75:
            chunk = chunk[:-1]
        result.append(chunk)
        line = " " + line[len(chunk):]
    result.append(line)
    return "\r\n".join(result)

def parse_skool_dt(dt_str):
    if not dt_str:
        return None
    try:
        dt_str = dt_str.replace("Z", "+00:00")
        return datetime.fromisoformat(dt_str)
    except Exception:
        return None

def format_dt_utc(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_utc = dt.astimezone(timezone.utc)
    return dt_utc.strftime("%Y%m%dT%H%M%SZ")

def event_to_vevent(ev):
    event_id = ev.get("eventId") or ev.get("id", "unknown")
    title = ev.get("title", "Sin titulo")
    description = ev.get("description") or ev.get("body", "")
    location = ev.get("location", "")
    start_str = ev.get("startAt") or ev.get("startDate") or ev.get("start")
    end_str   = ev.get("endAt")   or ev.get("endDate")   or ev.get("end")
    start_dt = parse_skool_dt(start_str)
    end_dt   = parse_skool_dt(end_str)
    if start_dt is None:
        return None
    if end_dt is None or end_dt <= start_dt:
        end_dt = start_dt + timedelta(hours=1)
    uid = f"{event_id}@skool-calendar-sync"
    now_str = format_dt_utc(datetime.now(timezone.utc))
    lines = [
        "BEGIN:VEVENT",
        fold_line(f"UID:{uid}"),
        fold_line(f"DTSTAMP:{now_str}"),
        fold_line(f"DTSTART:{format_dt_utc(start_dt)}"),
        fold_line(f"DTEND:{format_dt_utc(end_dt)}"),
        fold_line(f"SUMMARY:{escape_ics(title)}"),
    ]
    if description:
        lines.append(fold_line(f"DESCRIPTION:{escape_ics(description)}"))
    if location:
        lines.append(fold_line(f"LOCATION:{escape_ics(location)}"))
    group_url = f"{SKOOL_BASE_URL}/{GROUP_SLUG}/calendar"
    lines.append(fold_line(f"URL:{group_url}"))
    lines.append("END:VEVENT")
    return "\r\n".join(lines)

def generate_ics(events):
    now_str = format_dt_utc(datetime.now(timezone.utc))
    cal_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//SkoolCalendarSync//EN",
        f"X-WR-CALNAME:{escape_ics(CALENDAR_NAME)}",
        f"X-WR-CALDESC:{escape_ics(CALENDAR_DESC)}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-TIMEZONE:UTC",
        f"LAST-MODIFIED:{now_str}",
    ]
    added = 0
    for ev in events:
        vevent = event_to_vevent(ev)
        if vevent:
            cal_lines.append(vevent)
            added += 1
    cal_lines.append("END:VCALENDAR")
    log.info(f"Eventos incluidos en el ICS: {added}")
    return "\r\n".join(cal_lines) + "\r\n"

# --- Main --------------------------------------------------------------------

def main():
    log.info(f"=== Skool Calendar Sync - {GROUP_SLUG} ===")
    if not SKOOL_COOKIE:
        log.warning("SKOOL_COOKIE no configurada - las peticiones pueden fallar")
    else:
        log.info("Cookie de sesion: configurada")
    events = get_all_events()
    log.info(f"Total de eventos encontrados: {len(events)}")
    ics_content = generate_ics(events)
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(ics_content)
    log.info(f"Archivo ICS guardado: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
