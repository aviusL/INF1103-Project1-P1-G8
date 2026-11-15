"""main.py - Controller. Wires the four managers together; no console output, no business rules."""
import ai_manager
from data_manager import AppLogger, DataConfig, DataManager  # [data_manager OOP]
import io_manager
from logic_manager import LogicManager

logic = LogicManager()

# Get the OUTPUT class from io_manager module
io___manager_output = io_manager.OutputManager()
io___manager_input = io_manager.InputManager(io___manager_output)

# GUI related stuff for kivy
def use_io(output, input_manager):
    global io___manager_output, io___manager_input
    io___manager_output = output
    io___manager_input = input_manager


HISTORY_WINDOW = 5        # how many past sessions are sent to the AI
MAX_AI_ATTEMPTS = 3       # AI re-tries after the logic layer rejects its output
MAX_USER_REJECTIONS = 3


def current_weekly_target(data):  # [data_manager OOP]
    """Where the next session sits in the user's week (sessions already logged this week)."""
    history = data.get_recent_sessions(HISTORY_WINDOW)  # [data_manager OOP]
    done = data.sessions_this_week()  # [data_manager OOP]
    return logic.weekly_target(data.profile, done, history)  # [data_manager OOP]


def generate_valid_session(data, user_feedback=None, weekly_target=None):  # [data_manager OOP]
    """AI -> logic loop. Returns (suggestion, decision) or (None, None)."""
    profile = data.profile  # [data_manager OOP]
    history = data.get_recent_sessions(HISTORY_WINDOW)  # [data_manager OOP]
    if weekly_target is None:
        weekly_target = current_weekly_target(data)  # [data_manager OOP]
    rule_feedback = None
    for _ in range(MAX_AI_ATTEMPTS):
        result = ai_manager.request_session(profile, history, user_feedback, rule_feedback, weekly_target)
        if not result["ok"]:
            io___manager_output.show_error("Coach unavailable: {}. Please try again later.".format(result["error"]))
            return None, None
        decision = logic.evaluate_session(result["data"], profile, history, weekly_target)
        if decision["outcome"] != "reject":
            return result["data"], decision
        rule_feedback = decision["reasons"]
        io___manager_output.show_message("The first draft broke safety rules; asking the coach to revise...")
    io___manager_output.show_error("The coach could not produce a safe session. Please try again.")
    return None, None


def run_setup(data):  # [data_manager OOP]
    if data.profile and not io___manager_input.prompt_yes_no(  # [data_manager OOP]
            "A profile already exists. Replace it (history is kept)?"):
        return False
    profile = io___manager_input.prompt_profile()
    while not logic.usable_activities(profile):
        io___manager_output.show_error("None of your chosen activities can be done with your equipment. "
                              "Add the needed equipment or pick other activities.")
        profile["equipment"] = io___manager_input.prompt_equipment()
        profile["preferred_activities"] = io___manager_input.prompt_activities()
    for activity in profile["preferred_activities"]:
        needed = logic.missing_equipment_for(activity, profile)
        if needed:
            io___manager_output.show_message("Note: {} needs {}; it will be skipped until you add it.".format(
                activity, " or ".join(needed)))
    if not data.save_profile(profile):  # [data_manager OOP]
        io___manager_output.show_error("Could not save your profile to disk.")
    return True


def run_suggestion_flow(data):  # [data_manager OOP]
    if data.pending_session:  # [data_manager OOP]
        io___manager_output.show_message("You already have a planned session:")
        io___manager_output.show_message(io___manager_output.format_suggestion(data.pending_session))  # [data_manager OOP]
        if not io___manager_input.prompt_yes_no("Replace it with a new suggestion?"):
            return
    weekly_target = current_weekly_target(data)  # [data_manager OOP]
    io___manager_output.show_weekly_context(weekly_target)
    user_feedback = None
    for _ in range(MAX_USER_REJECTIONS):
        suggestion, decision = generate_valid_session(data, user_feedback, weekly_target)  # [data_manager OOP]
        if suggestion is None:
            return
        io___manager_output.show_suggestion(suggestion, decision)
        accepted, user_feedback = io___manager_input.prompt_accept_or_reject()
        if accepted:
            if not data.set_pending_session(suggestion):  # [data_manager OOP]
                io___manager_output.show_error("Could not save the session to disk.")
            io___manager_output.show_message("Great - go do it, then log how it went (option 3).")
            return
    io___manager_output.show_message("Let's pick this up later - try option 2 again.")


def run_log_session(data):  # [data_manager OOP]
    if not data.pending_session:  # [data_manager OOP]
        io___manager_output.show_error("There is no planned session to log. Get a suggestion first (option 2).")
        return
    while True:
        log = io___manager_input.prompt_session_log()
        errors = logic.validate_session_log(log, data.profile)  # [data_manager OOP]
        if not errors:
            break
        for error in errors:
            io___manager_output.show_error(error)
        io___manager_output.show_message("Please re-enter your session details.")
    if not data.complete_pending_session(log):  # [data_manager OOP]
        io___manager_output.show_error("Could not save the session to disk.")
        return
    io___manager_output.show_message("Session saved. Planning your next one...")
    run_suggestion_flow(data)  # [data_manager OOP]


def run_history(data):  # [data_manager OOP]
    pain_only = io___manager_input.prompt_yes_no("Show only sessions with pain reported?")
    records = data.filter_sessions(pain_only=pain_only)  # [data_manager OOP]
    io___manager_output.show_history(records)


def run_reset(data):  # [data_manager OOP]
    if not io___manager_input.prompt_confirm_reset():
        io___manager_output.show_message("Reset cancelled. Your data is unchanged.")
        return
    if data.reset():  # [data_manager OOP]
        io___manager_output.show_message("All data deleted. Run initial setup (option 1) to start again.")
    else:
        io___manager_output.show_error("Could not delete all data files. Check file permissions.")


def main():
    AppLogger(DataConfig.from_env().log_file).configure()  # [data_manager OOP]
    io___manager_output.show_banner()
    data = DataManager.from_config()  # [data_manager OOP]
    status = data.load()  # [data_manager OOP]
    io___manager_output.show_load_status(status)
    try:
        while True:
            choice = io___manager_input.prompt_main_menu(
                data.profile is not None, data.pending_session is not None)  # [data_manager OOP]
            if choice == "quit":
                break
            if choice == "setup":
                if run_setup(data):  # [data_manager OOP]
                    run_suggestion_flow(data)  # [data_manager OOP]
            elif choice == "reset":
                run_reset(data)  # [data_manager OOP]
            elif data.profile is None:  # [data_manager OOP]
                io___manager_output.show_error("Please run initial setup first (option 1).")
            elif choice == "suggest":
                run_suggestion_flow(data)  # [data_manager OOP]
            elif choice == "log":
                run_log_session(data)  # [data_manager OOP]
            elif choice == "history":
                run_history(data)  # [data_manager OOP]
    except (KeyboardInterrupt, EOFError):
        io___manager_output.show_message("")
    io___manager_output.show_message("Goodbye - keep moving!")


if __name__ == "__main__":
    main()
