"""main.py - AegisFit entry point. Starts the Flet GUI by default.

    python main.py          native desktop window (Flet)
    python main.py --web    serve the GUI in a browser on http://localhost:8550
    python main.py --cli    the original terminal menu

Inside Docker the FLET_FORCE_WEB_SERVER environment variable makes Flet serve in web mode.
"""
import argparse

import io_manager
from coach_service import APP_NAME, CoachService


class ConsoleApp:
    """Terminal front end: turns menu choices into CoachService calls.

    All printing and prompting goes through io_manager; all workflow logic is in CoachService.
    """

    MAX_USER_REJECTIONS = 3

    def __init__(self, service, output=None, input_manager=None):
        self._service = service
        self._out = output or io_manager.OutputManager()
        self._in = input_manager or io_manager.InputManager(self._out)
        self._actions = {
            "setup": self.run_setup,
            "suggest": self.run_suggestion_flow,
            "log": self.run_log_session,
            "history": self.run_history,
            "reset": self.run_reset,
        }

    def run(self):
        self._out.show_banner()
        self._out.show_load_status(self._service.load())
        try:
            while True:
                choice = self._in.prompt_main_menu(self._service.has_profile,
                                                   self._service.pending_session is not None)
                if choice == "quit":
                    break
                if choice not in ("setup", "reset") and not self._service.has_profile:
                    self._out.show_error("Please run initial setup first (option 1).")
                    continue
                self._actions[choice]()
        except (KeyboardInterrupt, EOFError):
            self._out.show_message("")
        self._out.show_message("Goodbye - keep moving!")

    def run_setup(self):
        if self._service.has_profile and not self._in.prompt_yes_no(
                "A profile already exists. Replace it (history is kept)?"):
            return
        profile = self._in.prompt_profile()
        while self._service.validate_profile(profile):
            self._out.show_error("None of your chosen activities can be done with your equipment. "
                                 "Add the needed equipment or pick other activities.")
            profile["equipment"] = self._in.prompt_equipment()
            profile["preferred_activities"] = self._in.prompt_activities()
        for note in self._service.equipment_notes(profile):
            self._out.show_message("Note: " + note)
        for error in self._service.save_profile(profile):
            self._out.show_error(error[0].upper() + error[1:] + ".")
        self.run_suggestion_flow()

    def run_suggestion_flow(self):
        pending = self._service.pending_session
        if pending:
            self._out.show_message("You already have a planned session:")
            self._out.show_message(self._out.format_suggestion(pending))
            if not self._in.prompt_yes_no("Replace it with a new suggestion?"):
                return
        target = self._service.weekly_target()
        self._out.show_weekly_context(target)
        feedback = None
        for _ in range(self.MAX_USER_REJECTIONS):
            result = self._service.suggest_session(
                feedback, target,
                on_revision=lambda reasons: self._out.show_message(
                    "The first draft broke safety rules; asking the coach to revise..."))
            if not result.ok:
                self._out.show_error(result.error[0].upper() + result.error[1:] + ". Please try again.")
                return
            self._out.show_suggestion(result.suggestion, result.decision)
            accepted, feedback = self._in.prompt_accept_or_reject()
            if accepted:
                if not self._service.accept_session(result.suggestion):
                    self._out.show_error("Could not save the session to disk.")
                self._out.show_message("Great - go do it, then log how it went (option 3).")
                return
        self._out.show_message("Let's pick this up later - try option 2 again.")

    def run_log_session(self):
        if not self._service.pending_session:
            self._out.show_error("There is no planned session to log. Get a suggestion first (option 2).")
            return
        while True:
            errors = self._service.log_session(self._in.prompt_session_log())
            if not errors:
                break
            for error in errors:
                self._out.show_error(error)
            self._out.show_message("Please re-enter your session details.")
        self._out.show_message("Session saved. Planning your next one...")
        self.run_suggestion_flow()

    def run_history(self):
        pain_only = self._in.prompt_yes_no("Show only sessions with pain reported?")
        self._out.show_history(self._service.history(pain_only=pain_only))

    def run_reset(self):
        if not self._in.prompt_confirm_reset():
            self._out.show_message("Reset cancelled. Your data is unchanged.")
            return
        if self._service.reset():
            self._out.show_message("All data deleted. Run initial setup (option 1) to start again.")
        else:
            self._out.show_error("Could not delete all data files. Check file permissions.")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="aegisfit", description="{}: adaptive training with a safety net".format(APP_NAME))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--cli", action="store_true", help="use the terminal menu instead of the GUI")
    mode.add_argument("--web", action="store_true", help="serve the GUI in a web browser")
    parser.add_argument("--port", type=int, default=8550, help="port for --web (default 8550)")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.cli:
        ConsoleApp(CoachService.create()).run()
        return
    from gui.app import launch   # imported here so the CLI and tests do not need Flet
    launch(web=args.web, port=args.port)


if __name__ == "__main__":
    main()
