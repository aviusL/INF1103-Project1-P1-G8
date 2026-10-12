"""io_manager.py - Input layer: every print() and input() in the system lives here."""

FITNESS_LEVELS = ("beginner", "intermediate", "advanced")
SEX_OPTIONS = ("male", "female")
EQUIPMENT_OPTIONS = (
    "dumbbells", "barbell", "kettlebell", "resistance bands", "pull-up bar", "bench",
    "treadmill", "stationary bike", "rowing machine", "jump rope", "yoga mat",
    "bicycle", "swimming pool",
)
ACTIVITY_OPTIONS = (
    "running", "cycling", "swimming", "walking", "strength training", "hiit", "yoga", "rowing",
)


# ---------- output ----------
def show_message(text):
    print(text)


def show_error(text):
    print("[!] " + text)


def show_banner():
    print("=" * 50)
    print("  AI Fitness Coach")
    print("=" * 50)


def show_load_status(status):
    if status == "ok":
        show_message("Loaded your saved profile and session history.")
    elif status == "corrupt":
        show_error("Your data file was corrupt. It was backed up and a fresh one started.")
    else:
        show_message("No saved data found - please run initial setup.")


def format_suggestion(s):
    pace = "n/a" if s["target_pace_min_per_km"] is None else "{} min/km".format(s["target_pace_min_per_km"])
    return "\n".join([
        "--- Suggested session ---",
        "Activity:  {}".format(s.get("activity", "n/a")),
        "Type:      {}".format(s["exercise_type"]),
        "Duration:  {} min   Intensity: {}   Risk: {}".format(s["duration_min"], s["intensity"], s["risk_level"]),
        "Target:    {} bpm, pace {}".format(s["target_hr_bpm"], pace),
        "Equipment: {}".format(", ".join(s.get("equipment_used") or ["bodyweight"])),
        "Warm-up:   {}".format(s["warmup"]),
        "Main set:  {}".format(s["main_set"]),
        "Cool-down: {}".format(s["cooldown"]),
        "Why:       {}".format(s["rationale"]),
    ])


def show_weekly_context(target):
    """Show where the next session sits in the user's week."""
    print("\n--- This week ---")
    print("Plan for {} training days: {}".format(target["sessions_per_week"], " > ".join(target["pattern"])))
    if target["week_complete"]:
        print("You have done all {} planned sessions this week; any extra session is light recovery.".format(
            target["sessions_per_week"]))
    else:
        print("Next: session {} of {}, target intensity {}.".format(
            target["session_number"], target["sessions_per_week"], target["intensity"]))


def show_suggestion(suggestion, decision):
    print(format_suggestion(suggestion))
    if decision["outcome"] == "flag":
        print("Warnings (score {}/100):".format(decision["score"]))
        for reason in decision["reasons"]:
            print("  - " + reason)


def format_session_record(record):
    actual = record["actual"]
    pace = "n/a" if actual.get("avg_pace_min_per_km") is None else "{} min/km".format(actual["avg_pace_min_per_km"])
    pain = "pain reported" if actual.get("pain_reported") else "no pain"
    return "#{} {} | planned {} | actual {} min, {}, {} bpm, RPE {}, {} | {}".format(
        record["id"], record["date"], record["planned"]["exercise_type"], actual["duration_min"],
        pace, actual["avg_hr_bpm"], actual["rpe"], pain, actual.get("comments") or "-")


def show_history(records):
    if not records:
        print("No matching sessions yet.")
        return
    for record in records:
        print(format_session_record(record))


# ---------- validated input ----------
def prompt_text(label, default=None):
    suffix = " [{}]".format(default) if default is not None else ""
    value = input("{}{}: ".format(label, suffix)).strip()
    return value if value else (default if default is not None else "")


def prompt_required_text(label):
    while True:
        value = input(label + ": ").strip()
        if value:
            return value
        show_error("This field cannot be empty.")


def prompt_int(label, low, high):
    while True:
        raw = input("{} ({}-{}): ".format(label, low, high)).strip()
        try:
            value = int(raw)
        except ValueError:
            show_error("Please enter a whole number.")
            continue
        if low <= value <= high:
            return value
        show_error("Value must be between {} and {}.".format(low, high))


def prompt_float(label, low, high, allow_blank=False):
    while True:
        raw = input("{} ({}-{}{}): ".format(label, low, high, ", blank = n/a" if allow_blank else "")).strip()
        if allow_blank and raw == "":
            return None
        try:
            value = float(raw)
        except ValueError:
            show_error("Please enter a number.")
            continue
        if low <= value <= high:
            return value
        show_error("Value must be between {} and {}.".format(low, high))


def prompt_choice(label, options):
    while True:
        raw = input("{} ({}): ".format(label, "/".join(options))).strip().lower()
        if raw in options:
            return raw
        show_error("Please choose one of: " + ", ".join(options))


def prompt_yes_no(label):
    while True:
        raw = input(label + " (y/n): ").strip().lower()
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        show_error("Please answer y or n.")


def prompt_main_menu(has_profile, has_pending):
    print("\n1) Initial setup (create/replace profile)")
    print("2) Get next suggested session")
    print("3) Log a completed session" + ("  <- you have a session waiting" if has_pending else ""))
    print("4) View history")
    print("5) Reset all data")
    print("6) Quit")
    keys = {"1": "setup", "2": "suggest", "3": "log", "4": "history", "5": "reset", "6": "quit"}
    while True:
        raw = input("Choose an option: ").strip()
        if raw in keys:
            return keys[raw]
        show_error("Please enter a number from 1 to 6.")


def prompt_confirm_reset():
    """Destructive action: only the exact word RESET confirms; anything else cancels."""
    show_error("This permanently deletes your profile and ALL session history.")
    return input("Type RESET to confirm, or press Enter to cancel: ").strip() == "RESET"


def prompt_multi_choice(options, intro, allow_empty):
    """Pick items from a numbered list. Returns the chosen names (empty list only if allow_empty)."""
    show_message(intro)
    for number, name in enumerate(options, start=1):
        show_message("  {}) {}".format(number, name))
    hint = "or press Enter for none" if allow_empty else "at least one"
    while True:
        raw = input("Enter numbers separated by commas (for example 1,3), {}: ".format(hint)).strip()
        if raw == "":
            if allow_empty:
                return []
            show_error("Please choose at least one.")
            continue
        try:
            chosen = sorted({int(part) for part in raw.split(",")})
        except ValueError:
            show_error("Please enter numbers separated by commas.")
            continue
        if all(1 <= number <= len(options) for number in chosen):
            return [options[number - 1] for number in chosen]
        show_error("Each number must be between 1 and {}.".format(len(options)))


def prompt_equipment():
    """Equipment owned. Enter alone means bodyweight only (empty list)."""
    return prompt_multi_choice(
        EQUIPMENT_OPTIONS,
        "\nWhat equipment do you have? (bodyweight exercises are always included)", True)


def prompt_activities():
    """Exercise types / sports the user wants to do. At least one is required."""
    return prompt_multi_choice(
        ACTIVITY_OPTIONS, "\nWhich exercises or sports do you want to do?", False)


def prompt_profile():
    show_message("\nLet's set up your profile.")
    profile = {
        "age": prompt_int("Age", 12, 100),
        "sex": prompt_choice("Sex", SEX_OPTIONS),
        "height_cm": prompt_float("Height in cm", 100, 250),
        "weight_kg": prompt_float("Weight in kg", 30, 250),
        "fitness_level": prompt_choice("Fitness level", FITNESS_LEVELS),
        "health_concerns": prompt_text("Prior health concerns / injuries", "none"),
        "fitness_goal": prompt_required_text("Fitness goal (e.g. run 5k in 30 min)"),
        "days_per_week": prompt_int("Days per week you can train", 1, 7),
        "session_duration_min": prompt_int("Minutes available per training session", 10, 120),
    }
    profile["equipment"] = prompt_equipment()
    profile["preferred_activities"] = prompt_activities()
    return profile


def prompt_accept_or_reject():
    """Returns (accepted, reason)."""
    if prompt_yes_no("Accept this session?"):
        return True, None
    return False, prompt_required_text("Why are you rejecting it? (the coach will adapt)")


def prompt_session_log():
    show_message("\nHow did the session go?")
    return {
        "duration_min": prompt_int("Actual duration in minutes", 1, 300),
        "avg_pace_min_per_km": prompt_float("Average pace in min/km", 2, 30, allow_blank=True),
        "avg_hr_bpm": prompt_int("Average heart rate (bpm)", 30, 230),
        "rpe": prompt_int("Rate of perceived exertion", 1, 10),
        "pain_reported": prompt_yes_no("Any pain or discomfort?"),
        "comments": prompt_text("Comments (pain, discomfort, how it felt)", ""),
    }
