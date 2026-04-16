"""
Форматирование данных о здоровье в Telegram MarkdownV2 сообщение.
"""
import re
from datetime import date as date_type


# MarkdownV2 special chars that must be escaped (backslash first to avoid double-escaping)
_SPECIAL = r'\_*[]()~`>#+-=|{}.!'
_ESC_RE = re.compile(r'([' + re.escape(_SPECIAL) + r'])')


def _esc(text) -> str:
    """Escape all MarkdownV2 special characters."""
    return _ESC_RE.sub(r'\\\1', str(text))


def _fmt_minutes(minutes: int | None) -> str:
    """Convert minutes to 'Xч YYм' format."""
    if minutes is None:
        return "?"
    h, m = divmod(int(minutes), 60)
    return f"{h}ч {m:02d}м" if h else f"{m}м"


def _fmt_date_ru(date_str: str) -> str:
    """'2026-04-15' → '15 апреля 2026'"""
    months = [
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    ]
    d = date_type.fromisoformat(date_str)
    return f"{d.day} {months[d.month - 1]} {d.year}"


def format_day(data: dict) -> str:
    """
    Format a full day's data dict (as returned by db.get_day) into
    a Telegram MarkdownV2 string.

    Sections with no data are omitted entirely.
    Returns empty string if there is no data at all.
    """
    date_str = data.get("date", "")
    lines = []

    # Header
    lines.append(f"📊 *Health Hub · {_esc(_fmt_date_ru(date_str))}*")
    lines.append("")

    has_content = False

    # --- Nutrition ---
    nutrition = data.get("nutrition")
    if nutrition:
        cal = nutrition.get("calories")
        prot = nutrition.get("protein_g")
        fat = nutrition.get("fat_g")
        carbs = nutrition.get("carbs_g")
        water = nutrition.get("water_ml")

        if cal is not None:
            parts = [f"{int(cal)} kcal"]
            if prot is not None:
                parts.append(f"Б {int(prot)}")
            if fat is not None:
                parts.append(f"Ж {int(fat)}")
            if carbs is not None:
                parts.append(f"У {int(carbs)}")
            lines.append("🍽 " + _esc(" · ".join(parts)))
            has_content = True

        if water:
            water_l = water / 1000
            lines.append(f"💧 {_esc(f'{water_l:.1f}')}л")

        lines.append("")

    # --- Food log (individual meals) ---
    food_log = data.get("food_log") or []
    if food_log:
        _MEAL_ORDER = ["Breakfast", "Lunch", "Dinner", "Snack", "Anytime"]
        by_meal: dict[str, list] = {}
        for item in food_log:
            meal = item.get("meal_type") or "Anytime"
            by_meal.setdefault(meal, []).append(item)

        _MEAL_RU = {
            "Breakfast": "Завтрак",
            "Lunch": "Обед",
            "Dinner": "Ужин",
            "Snack": "Перекус",
            "Anytime": "Другое",
        }
        for meal in _MEAL_ORDER:
            items = by_meal.get(meal)
            if not items:
                continue
            lines.append(f"_{_esc(_MEAL_RU.get(meal, meal))}_")
            for item in items:
                name = item.get("food_name") or "?"
                cal = item.get("calories")
                amount = item.get("amount")
                unit = item.get("unit")
                detail = _esc(name)
                suffix_parts = []
                if amount is not None and unit:
                    suffix_parts.append(f"{_esc(amount)} {_esc(unit)}")
                if cal is not None:
                    suffix_parts.append(f"{int(cal)} kcal")
                if suffix_parts:
                    detail += " — " + ", ".join(suffix_parts)
                lines.append(f"  • {detail}")
        lines.append("")

    # --- Activity ---
    activity = data.get("activity")
    if activity:
        steps = activity.get("steps")
        dist = activity.get("distance_km")
        lightly = activity.get("active_minutes_lightly", 0) or 0
        fairly = activity.get("active_minutes_fairly", 0) or 0
        very = activity.get("active_minutes_very", 0) or 0
        active_min = lightly + fairly + very

        if steps is not None:
            parts = [f"{steps:,} шага".replace(",", "\u00a0")]
            if dist:
                parts.append(f"{_esc(f'{dist:.1f}')} км")
            if active_min:
                parts.append(f"{active_min} акт\\.мин")
            lines.append("🏃 " + " · ".join(parts))
            has_content = True
            lines.append("")

    # --- Sleep ---
    sleep_sessions = data.get("sleep", [])
    main_sleep = next((s for s in sleep_sessions if s.get("is_main_sleep")), None)
    if not main_sleep and sleep_sessions:
        main_sleep = sleep_sessions[0]

    if main_sleep:
        dur = main_sleep.get("duration_minutes")
        eff = main_sleep.get("efficiency")
        deep = main_sleep.get("deep_minutes")
        rem = main_sleep.get("rem_minutes")

        parts = []
        if dur is not None:
            parts.append(_fmt_minutes(dur))
        if eff is not None:
            parts.append(f"{eff}%")
        if deep is not None:
            parts.append(f"Глуб {_fmt_minutes(deep)}")
        if rem is not None:
            parts.append(f"REM {_fmt_minutes(rem)}")

        if parts:
            lines.append("😴 " + _esc(" · ".join(parts)))
            has_content = True
            lines.append("")

    # --- Weight + HRV on same line ---
    weight = data.get("weight")
    hrv = data.get("hrv")
    wh_parts = []
    if weight and weight.get("weight_kg") is not None:
        wh_parts.append(f"⚖️ {_esc(str(weight['weight_kg']))} кг")
    if hrv and hrv.get("rmssd") is not None:
        wh_parts.append(f"❤️ HRV {_esc(str(round(hrv['rmssd'])))} мс")
    if wh_parts:
        lines.append("   ".join(wh_parts))
        has_content = True
        lines.append("")

    # --- CPAP ---
    cpap = data.get("cpap")
    if cpap:
        dur = cpap.get("duration_minutes")
        ahi = cpap.get("ahi")
        obstr = cpap.get("obstructive_events")
        central = cpap.get("central_events")
        hypopnea = cpap.get("hypopnea_events")
        leak = cpap.get("leak_median")
        p_min = cpap.get("pressure_min")
        p_max = cpap.get("pressure_max")

        if dur is not None or ahi is not None:
            header_parts = []
            if dur is not None:
                header_parts.append(_fmt_minutes(dur))
            if ahi is not None:
                header_parts.append(f"AHI {_esc(str(round(ahi, 1)))}")
            lines.append("🫁 CPAP " + " · ".join(header_parts))
            has_content = True

        event_parts = []
        if obstr is not None:
            event_parts.append(f"Обстр {obstr}")
        if central is not None:
            event_parts.append(f"Центр {central}")
        if hypopnea is not None:
            event_parts.append(f"Гипопн {hypopnea}")
        if event_parts:
            lines.append("  " + _esc(" · ".join(event_parts)))

        tech_parts = []
        if leak is not None:
            tech_parts.append(f"Утечка {_esc(str(round(leak, 1)))} л/м")
        if p_min is not None and p_max is not None:
            tech_parts.append(
                f"Давл {_esc(str(round(p_min, 1)))}\\-{_esc(str(round(p_max, 1)))}"
            )
        if tech_parts:
            lines.append("  " + " · ".join(tech_parts))

        lines.append("")

    # --- O2Ring ---
    o2ring = data.get("o2ring")
    if o2ring:
        avg_spo2 = o2ring.get("avg_spo2")
        min_spo2 = o2ring.get("min_spo2")
        drops = o2ring.get("spo2_drops_count")
        avg_hr = o2ring.get("avg_hr")

        spo2_parts = []
        if avg_spo2 is not None:
            spo2_parts.append(f"ср {_esc(str(round(avg_spo2)))}%")
        if min_spo2 is not None:
            spo2_parts.append(f"мин {_esc(str(round(min_spo2)))}%")
        if spo2_parts:
            lines.append("🩸 SpO2 " + " · ".join(spo2_parts))
            has_content = True
        if drops is not None:
            line = f"  Десатураций: {drops}"
            if avg_hr is not None:
                line += f" · HR ср {_esc(str(round(avg_hr)))}"
            lines.append(line)
        lines.append("")

    if not has_content:
        return ""

    # Remove trailing empty lines
    while lines and lines[-1] == "":
        lines.pop()

    return "\n".join(lines)
