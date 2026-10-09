"""io_manager.py - Input layer: every print() and input() in the system lives here."""

FITNESS_LEVELS = ("beginner", "intermediate", "advanced")

# --------- OUTPUT -------------------
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
        "Type:      {}".format(s["exercise_type"]),
        "Duration:  {} min   Intensity: {}   Risk: {}".format(s["duration_min"], s["intensity"], s["risk_level"]),
        "Target:    {} bpm, pace {}".format(s["target_hr_bpm"], pace),
        "Warm-up:   {}".format(s["warmup"]),
        "Main set:  {}".format(s["main_set"]),
        "Cool-down: {}".format(s["cooldown"]),
        "Why:       {}".format(s["rationale"]),
    ])


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


def prompt_yes_no(label):
    while True:
        raw = input(label + " (y/n): ").strip().lower()
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        show_error("Please answer y or n.")


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


# ----------- Input functions --------------------

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


def prompt_choice(label, options):
    while True:
        raw = input("{} ({}): ".format(label, "/".join(options))).strip().lower()
        if raw in options:
            return raw
        show_error("Please choose one of: " + ", ".join(options))


def prompt_main_menu(has_profile, has_pending):
    print("\n1) Initial setup (create/replace profile)")
    print("2) Get next suggested session")
    print("3) Log a completed session" + ("  <- you have a session waiting" if has_pending else ""))
    print("4) View history")
    print("5) Quit")
    keys = {"1": "setup", "2": "suggest", "3": "log", "4": "history", "5": "quit"}
    while True:
        raw = input("Choose an option: ").strip()
        if raw in keys:
            return keys[raw]
        show_error("Please enter a number from 1 to 5.")


def prompt_profile():
    show_message("\nLet's set up your profile.")
    return {
        "age": prompt_int("Age", 12, 100),
        "height_cm": prompt_float("Height in cm", 100, 250),
        "weight_kg": prompt_float("Weight in kg", 30, 250),
        "fitness_level": prompt_choice("Fitness level", FITNESS_LEVELS),
        "health_concerns": prompt_text("Prior health concerns / injuries", "none"),
        "fitness_goal": prompt_required_text("Fitness goal (e.g. run 5k in 30 min)"),
    }


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





