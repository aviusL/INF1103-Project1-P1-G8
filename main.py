"""main.py - Controller. Wires the four managers together; no console output, no business rules."""
import ai_manager
import data_manager
import io_manager
import logic_manager

HISTORY_WINDOW = 5        # how many past sessions are sent to the AI
MAX_AI_ATTEMPTS = 3       # AI re-tries after the logic layer rejects its output
MAX_USER_REJECTIONS = 3


def generate_valid_session(store, user_feedback=None):
    """AI -> logic loop. Returns (suggestion, decision) or (None, None)."""
    profile = store["profile"]
    history = data_manager.get_recent_sessions(store, HISTORY_WINDOW)
    rule_feedback = None
    for _ in range(MAX_AI_ATTEMPTS):
        result = ai_manager.request_session(profile, history, user_feedback, rule_feedback)
        if not result["ok"]:
            io_manager.show_error("Coach unavailable: {}. Please try again later.".format(result["error"]))
            return None, None
        decision = logic_manager.evaluate_session(result["data"], profile, history)
        if decision["outcome"] != "reject":
            return result["data"], decision
        rule_feedback = decision["reasons"]
        io_manager.show_message("The first draft broke safety rules; asking the coach to revise...")
    io_manager.show_error("The coach could not produce a safe session. Please try again.")
    return None, None


def run_setup(store):
    if store["profile"] and not io_manager.prompt_yes_no(
            "A profile already exists. Replace it (history is kept)?"):
        return False
    profile = io_manager.prompt_profile()
    if not data_manager.save_profile(store, profile):
        io_manager.show_error("Could not save your profile to disk.")
    return True


def run_suggestion_flow(store):
    if store["pending_session"]:
        io_manager.show_message("You already have a planned session:")
        io_manager.show_message(io_manager.format_suggestion(store["pending_session"]))
        if not io_manager.prompt_yes_no("Replace it with a new suggestion?"):
            return
    user_feedback = None
    for _ in range(MAX_USER_REJECTIONS):
        suggestion, decision = generate_valid_session(store, user_feedback)
        if suggestion is None:
            return
        io_manager.show_suggestion(suggestion, decision)
        accepted, user_feedback = io_manager.prompt_accept_or_reject()
        if accepted:
            if not data_manager.set_pending_session(store, suggestion):
                io_manager.show_error("Could not save the session to disk.")
            io_manager.show_message("Great - go do it, then log how it went (option 3).")
            return
    io_manager.show_message("Let's pick this up later - try option 2 again.")


def run_log_session(store):
    if not store["pending_session"]:
        io_manager.show_error("There is no planned session to log. Get a suggestion first (option 2).")
        return
    while True:
        log = io_manager.prompt_session_log()
        errors = logic_manager.validate_session_log(log, store["profile"])
        if not errors:
            break
        for error in errors:
            io_manager.show_error(error)
        io_manager.show_message("Please re-enter your session details.")
    if not data_manager.complete_pending_session(store, log):
        io_manager.show_error("Could not save the session to disk.")
        return
    io_manager.show_message("Session saved. Planning your next one...")
    run_suggestion_flow(store)


def run_history(store):
    pain_only = io_manager.prompt_yes_no("Show only sessions with pain reported?")
    records = data_manager.filter_sessions(store, pain_only=pain_only)
    io_manager.show_history(records)


def main():
    data_manager.setup_logging()
    io_manager.show_banner()
    store, status = data_manager.load_data()
    io_manager.show_load_status(status)
    try:
        while True:
            choice = io_manager.prompt_main_menu(
                store["profile"] is not None, store["pending_session"] is not None)
            if choice == "quit":
                break
            if choice == "setup":
                if run_setup(store):
                    run_suggestion_flow(store)
            elif store["profile"] is None:
                io_manager.show_error("Please run initial setup first (option 1).")
            elif choice == "suggest":
                run_suggestion_flow(store)
            elif choice == "log":
                run_log_session(store)
            elif choice == "history":
                run_history(store)
    except (KeyboardInterrupt, EOFError):
        io_manager.show_message("")
    io_manager.show_message("Goodbye - keep moving!")


if __name__ == "__main__":
    main()
