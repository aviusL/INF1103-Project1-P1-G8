"""io_manager.py - Input layer: every print() and input() in the system lives here."""

import os
import sys
import textwrap


class ConsoleFormatter:

    LINE_WIDTH = 56
    HEAVY_RULE = "=" * LINE_WIDTH
    LIGHT_RULE = "-" * LINE_WIDTH
    

    @staticmethod
    def bold(text):
        """Bold text on a real terminal; plain text when piped or when NO_COLOR is set."""
        if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
            return text
        if os.name == "nt":
            os.system("")  # switches on ANSI escape codes in the Windows console
        return "\033[1m" + text + "\033[0m"
    
    @classmethod
    def section_title(cls,title):
        """A light rule with a bold title, used to separate blocks of output."""
        return "\n{}\n {}\n{}".format(cls.LIGHT_RULE, cls.bold(title.upper()), cls.LIGHT_RULE)
    
    @classmethod
    def field(cls,label, value):
        """A bold 'Label:' followed by the value, wrapped so long text lines up under itself."""
        pad = 11
        head = (label + ":").ljust(pad)
        body = textwrap.fill(str(value), width=cls.LINE_WIDTH, initial_indent=" " * pad,
                            subsequent_indent=" " * pad)
        return cls.bold(head) + body[pad:]
 
    # ---------- output ----------
    @classmethod
    def labelled(cls,label, text):
        """'Label:  text' with a bold label; long text wraps under the text column, not under the label."""
        indent = " " * 11
        body = textwrap.fill(str(text), width=cls.LINE_WIDTH, initial_indent=indent, subsequent_indent=indent)
        return cls.bold((label + ":").ljust(11)) + body[len(indent):]

 

# ---------- validated input ----------
class InputManager:

    FITNESS_LEVELS = ("beginner", "intermediate", "advanced")
    SEX_OPTIONS = ("male", "female")
    EQUIPMENT_OPTIONS = (
    "dumbbells", "barbell", "kettlebell", "resistance bands", "pull-up bar", "bench",
    "treadmill", "stationary bike", "rowing machine", "jump rope", "bicycle", "swimming pool",
    )
    ACTIVITY_OPTIONS = (
    "running", "cycling", "swimming", "walking", "strength training", "hiit", "rowing",
    )


    def __init__(self, out_put):
        self.out_put = out_put


    def prompt_text(self,label, default=None):
        suffix = " [{}]".format(default) if default is not None else ""
        value = input("{}{}: ".format(label, suffix)).strip()
        return value if value else (default if default is not None else "")
    
    
    def prompt_required_text(self,label):
        while True:
            value = input(label + ": ").strip()
            if value:
                return value
            self.out_put.show_error("This field cannot be empty.")
    
    
    def prompt_int(self,label, low, high):
        while True:
            raw = input("{} ({}-{}): ".format(label, low, high)).strip()
            try:
                value = int(raw)
            except ValueError:
                self.out_put.show_error("Please enter a whole number.")
                continue
            if low <= value <= high:
                return value
            self.out_put.show_error("Value must be between {} and {}.".format(low, high))
    
    
    def prompt_float(self,label, low, high, allow_blank=False):
        while True:
            raw = input("{} ({}-{}{}): ".format(label, low, high, ", blank = n/a" if allow_blank else "")).strip()
            if allow_blank and raw == "":
                return None
            try:
                value = float(raw)
            except ValueError:
                self.out_put.show_error("Please enter a number.")
                continue
            if low <= value <= high:
                return value
            self.out_put.show_error("Value must be between {} and {}.".format(low, high))
    
    
    def prompt_choice(self,label, options):
        while True:
            raw = input("{} ({}): ".format(label, "/".join(options))).strip().lower()
            if raw in options:
                return raw
            self.out_put.show_error("Please choose one of: " + ", ".join(options))
    
    
    def prompt_yes_no(self,label):
        while True:
            raw = input(label + " (y/n): ").strip().lower()
            if raw in ("y", "yes"):
                return True
            if raw in ("n", "no"):
                return False
            self.out_put.show_error("Please answer y or n.")
    
    
    def prompt_main_menu(self, has_profile, has_pending):
        print(ConsoleFormatter.section_title("Main menu"))
        print("1) Initial setup (create/replace profile)")
        print("2) Get next suggested session")
        print("3) Log a completed session" + ("  <- you have a session waiting" if has_pending else ""))
        print("4) View history")
        print("5) Reset all data")
        print("6) Quit")
        print(ConsoleFormatter.LIGHT_RULE)
        keys = {"1": "setup", "2": "suggest", "3": "log", "4": "history", "5": "reset", "6": "quit"}
        while True:
            raw = input("Choose an option: ").strip()
            if raw in keys:
                return keys[raw]
            self.out_put.show_error("Please enter a number from 1 to 6.")
    
    
    def prompt_confirm_reset(self):
        """Destructive action: only the exact word RESET confirms; anything else cancels."""
        self.out_put.show_error("This permanently deletes your profile and ALL session history.")
        return input("Type RESET to confirm, or press Enter to cancel: ").strip() == "RESET"
    
    
    def prompt_multi_choice(self, options, intro, allow_empty):
        """Pick items from a numbered list. Returns the chosen names (empty list only if allow_empty)."""
        self.out_put.show_message(intro)
        for number, name in enumerate(options, start=1):
            self.out_put.show_message("  {}) {}".format(number, name))
        hint = "or press Enter for none" if allow_empty else "at least one"
        while True:
            raw = input("Enter numbers separated by commas (for example 1,3), {}: ".format(hint)).strip()
            if raw == "":
                if allow_empty:
                    return []
                self.out_put.show_error("Please choose at least one.")
                continue
            try:
                chosen = sorted({int(part) for part in raw.split(",")})
            except ValueError:
                self.out_put.show_error("Please enter numbers separated by commas.")
                continue
            if all(1 <= number <= len(options) for number in chosen):
                return [options[number - 1] for number in chosen]
            self.out_put.show_error("Each number must be between 1 and {}.".format(len(options)))
    
    
    def prompt_equipment(self):
        """Equipment owned. Enter alone means bodyweight only (empty list)."""
        return self.prompt_multi_choice(
            self.EQUIPMENT_OPTIONS,
            "\nWhat equipment do you have? (bodyweight exercises are always included)", True)
    
    
    def prompt_activities(self):
        """Exercise types / sports the user wants to do. At least one is required."""
        return self.prompt_multi_choice(
            self.ACTIVITY_OPTIONS, "\nWhich exercises or sports do you want to do?", False)
    
    
    def prompt_profile(self):
        self.out_put.show_message(ConsoleFormatter.section_title("Profile setup"))
        profile = {
            "age": self.prompt_int("Age", 12, 100),
            "sex": self.prompt_choice("Sex", self.SEX_OPTIONS),
            "height_cm": self.prompt_float("Height in cm", 100, 250),
            "weight_kg": self.prompt_float("Weight in kg", 30, 250),
            "fitness_level": self.prompt_choice("Fitness level", self.FITNESS_LEVELS),
            "health_concerns": self.prompt_text("Prior health concerns / injuries", "none"),
            "fitness_goal": self.prompt_required_text("Fitness goal (e.g. run 5k in 30 min)"),
            "days_per_week": self.prompt_int("Days per week you can train", 1, 7),
            "session_duration_min": self.prompt_int("Minutes available per training session", 10, 120),
        }
        profile["equipment"] = self.prompt_equipment()
        profile["preferred_activities"] = self.prompt_activities()
        return profile
    
    
    def prompt_accept_or_reject(self):
        """Returns (accepted, reason)."""
        if self.prompt_yes_no("Accept this session?"):
            return True, None
        return False, self.prompt_required_text("Why are you rejecting it? (the coach will adapt)")
    
    
    def prompt_session_log(self):
        self.out_put.show_message(ConsoleFormatter.section_title("Log your session"))
        return {
            "duration_min": self.prompt_int("Actual duration in minutes", 1, 300),
            "avg_pace_min_per_km": self.prompt_float("Average pace in min/km", 2, 30, allow_blank=True),
            "avg_hr_bpm": self.prompt_int("Average heart rate (bpm)", 30, 230),
            "rpe": self.prompt_int("Rate of perceived exertion", 1, 10),
            "pain_reported": self.prompt_yes_no("Any pain or discomfort?"),
            "comments": self.prompt_text("Comments (pain, discomfort, how it felt)", ""),
        }


class OutputManager:
    """ Used to display output """

    def __init__(self):
        pass

    def show_message(self, text):
        print(text)

    def show_error(self, text):
        print("[!] " + text)

    def show_banner(self):
        print(ConsoleFormatter.HEAVY_RULE)
        print("  " + ConsoleFormatter.bold("AI Fitness Coach"))
        print(ConsoleFormatter.HEAVY_RULE)


    def show_load_status(self,status):
        if status == "ok":
            self.show_message("Loaded your saved profile and session history.")
        elif status == "corrupt":
            self.show_error("Your data file was corrupt. It was backed up and a fresh one started.")
        else:
            self.show_message("No saved data found - please run initial setup.")

    def format_suggestion(self,s):
        pace = "n/a" if s["target_pace_min_per_km"] is None else "{} min/km".format(s["target_pace_min_per_km"])
        rows = [
            ConsoleFormatter.labelled("Activity", s.get("activity", "n/a")),
            ConsoleFormatter.labelled("Type", s["exercise_type"]),
            ConsoleFormatter.labelled("Duration", "{} min   Intensity: {}   Risk: {}".format(
                s["duration_min"], s["intensity"], s["risk_level"])),
            ConsoleFormatter.labelled("Target", "{} bpm, pace {}".format(s["target_hr_bpm"], pace)),
            ConsoleFormatter.labelled("Equipment", ", ".join(s.get("equipment_used") or ["bodyweight"])),
            ConsoleFormatter.labelled("Warm-up", s["warmup"]),
            ConsoleFormatter.labelled("Main set", s["main_set"]),
            ConsoleFormatter.labelled("Cool-down", s["cooldown"]),
            ConsoleFormatter.labelled("Why", s["rationale"]),
        ]
        return "\n".join([ConsoleFormatter.section_title("Suggested session"), "\n\n".join(rows), ConsoleFormatter.LIGHT_RULE])


    def show_weekly_context(self,target):
        """Show where the next session sits in the user's week."""
        print(ConsoleFormatter.section_title("This week"))
        print("Plan for {} training days: {}".format(target["sessions_per_week"], " > ".join(target["pattern"])))
        if target["week_complete"]:
            print("You have done all {} planned sessions this week; any extra session is light recovery.".format(
                target["sessions_per_week"]))
        else:
            print("Next: session {} of {}, target intensity {}.".format(
                target["session_number"], target["sessions_per_week"], target["intensity"]))


    def show_suggestion(self, suggestion, decision):
        print(self.format_suggestion(suggestion))
        if decision["outcome"] == "flag":
            print("Warnings (score {}/100):".format(decision["score"]))
            for reason in decision["reasons"]:
                print("  ! " + reason)
            print(ConsoleFormatter.LIGHT_RULE)


    def format_session_record(self, record):
        actual = record["actual"]
        pace = "n/a" if actual.get("avg_pace_min_per_km") is None else "{} min/km".format(actual["avg_pace_min_per_km"])
        pain = "pain reported" if actual.get("pain_reported") else "no pain"
        return "\n".join([
            "#{}  {}  {}".format(record["id"], record["date"], record["planned"]["exercise_type"]),
            "    {} min | {} | {} bpm | RPE {} | {}".format(
                actual["duration_min"], pace, actual["avg_hr_bpm"], actual["rpe"], pain),
            "    Comments: {}".format(actual.get("comments") or "-"),
        ])


    def show_history(self, records):
        print(ConsoleFormatter.section_title("Session history"))
        if not records:
            print("No matching sessions yet.")
            return
        for index, record in enumerate(records):
            if index:
                print()
            print(self.format_session_record(record))
        print(ConsoleFormatter.LIGHT_RULE)


#Create shared INSTANCES
out_put = OutputManager()
in_put = InputManager(out_put)