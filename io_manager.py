from call_openai import ask_ai


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








def initial_inputs():

    print("Insert input here: ")
    # AGE ------------------------
    while True:
        try:
            age = int(input("Age: "))
            if 13 <= age <= 100:
                break
            print("Please enter an age between 13 and 100.")
        except ValueError:
            print("Please enter a valid number.")

    # GENDER --------------------
    while True:
        gender = input("Gender: Male(M) or Female(F)?  ").strip().lower()

        if gender in ["male", "female", "m", "f"]:
            break

        print("Please enter male, female, or other.")

    # HEIGHT -----------------
    while True:
        try:
            height = float(input("Height (cm): "))

            if 100 <= height <= 250:
                break

            print("Please enter a height between 100 and 250.")
        except ValueError:
            print("Please enter a valid number.")

    # WEIGHT
    while True:
        try:
            weight = float(input("Weight (kg): "))

            if 30 <= weight <= 300:
                break

            print("Please enter a weight between 30 and 300 kg.")

        except ValueError:
            print("Please enter a valid number.")

    # FITNESS ------------------------
    while True:
        fitness = int(input("Fitness level, pick a number (1-beginner, 2-intermediate, 3-advanced): "))

        if fitness in [1,2,3]:
            break

        print("Please enter 1 - beginner, 2 - intermediate, or 3 - advanced.")

    # TRAINING GOAL --------------------
    training_goal = input("Training goal: ")

    # HEALTH OR INJURIES HISTORY -----------------
    while True:
        health_injury_history = input("Health/injury history (Nil or n for none): ").strip()

        if health_injury_history:
            break

        print("Please enter 'none' if you have no health or injury history.")

    # AVAILABLE EQUIPMENT -------------------
    avail_equipment = input("Available equipment: ")

    # AVAILABLE DAY AND TIMES
    avail_days = input("Available days and times: ")

    return age,gender,height,weight,fitness,training_goal,health_injury_history,avail_equipment,avail_days




def post_exercise_feedback():

    print("How did the session go?")



    # PACE
    while True:
        try:
            pace = float(input("Average pace (min/km): "))

            if 2 <= pace <= 20:
                break

            print("Please enter a pace between 2 and 20 min/km.")

        except ValueError:
            print("Please enter a valid number.")


    # HEART RATE
    while True:
        try:
            heart_rate = int(input("Average heart rate (bpm): "))

            if 40 <= heart_rate <= 220:
                break

            print("Please enter a heart rate between 40 and 220 bpm.")

        except ValueError:
            print("Please enter a valid number.")


    # RPE
    while True:
        try:
            rpe = int(input("Rate of perceived exertion (1-10): "))

            if 1 <= rpe <= 10:
                break

            print("RPE must be between 1 and 10.")

        except ValueError:
            print("Please enter a whole number from 1 to 10.")


    # PAIN / DISCOMFORT
    pain = input(
        "Any pain or discomfort? "
    ).strip()

    if not pain:
        pain = "None"


    # COMMENTS
    comments = input(
        "How did the session feel? "
    ).strip()

    if not comments:
        comments = "No comments"


    return pace, heart_rate, rpe, pain, comments



# GET THE INPUTS ----------------------
(
    age,
    gender,
    height,
    weight,
    fitness,
    training_goal,
    health_injury_history,
    avail_equipment,
    avail_days
) = initial_inputs()



# POST EXERCISE INPUTS -------------------


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


def prompt_text(label, default=None):
    suffix = " [{}]".format(default) if default is not None else ""
    value = input("{}{}: ".format(label, suffix)).strip()
    return value if value else (default if default is not None else "")


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

# POST EXERCISE PROMPT SETTING>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
post_exercise_prompt = f"""

Determine and feedback on user post-exercise data
pace {pace}, 
heart_rate {heart_rate}, 
rpe {rpe} , 
pain {pain}, 
comments {comments}


"""



# Get Input From User.

initial_result = ask_ai(prompt_initial)
print(initial_result)

# Post Exercise Feedback. details saved to database
post_exercise_feedback()
post_result = ask_ai(post_exercise_prompt)
print(post_result)
