"""Pop-up dialogs: log a session, reject a suggestion, confirm, reset, pick a day, session details
with coach feedback, and where to find a Strava export."""
import asyncio
import calendar
import datetime
import inspect

import flet as ft

from activity_import import STRAVA_HELP_URL
from coach_service import LOG_LIMITS
from gui.theme import STATUS_COLORS, STATUS_LABELS, Palette
from gui.widgets import LabeledSlider, activity_badge, detail_rows, field_border, heading, label, pill

RPE_WORDS = {1: "very easy", 2: "easy", 3: "easy", 4: "moderate", 5: "moderate", 6: "somewhat hard",
             7: "hard", 8: "very hard", 9: "very hard", 10: "max effort"}


class BaseDialog:
    """Builds one AlertDialog and shows it through the shell (template method: subclasses
    supply title, content and actions)."""

    def __init__(self, app):
        self.app = app
        self.error_text = ft.Text(color=Palette.ERROR, size=12, visible=False)

    def title(self):
        return ""

    def content(self):
        return ft.Container()

    def actions(self):
        return [ft.TextButton("Close", on_click=lambda e: self.close())]

    def open(self):
        self.dialog = ft.AlertDialog(
            title=ft.Text(self.title(), weight=ft.FontWeight.W_700),
            content=ft.Container(ft.Column([self.content(), self.error_text], tight=True, spacing=12,
                                           scroll=ft.ScrollMode.AUTO), width=420),
            actions=self.actions(),
            bgcolor=Palette.SURFACE,
            shape=ft.RoundedRectangleBorder(radius=20),
        )
        self.app.page.show_dialog(self.dialog)
        self.app.page.update()

    def close(self):
        self.app.close_dialog()

    def show_errors(self, errors):
        self.error_text.value = "\n".join("• " + error[0].upper() + error[1:] for error in errors)
        self.error_text.visible = bool(errors)
        self.app.page.update()


class LogSessionDialog(BaseDialog):
    """Sliders, a checkbox and text fields for how the planned session went."""

    def __init__(self, app, plan, on_saved):
        super().__init__(app)
        self._plan = plan or {}
        self._on_saved = on_saved

    def title(self):
        return "How did it go?"

    def content(self):
        plan = self._plan
        self.duration = LabeledSlider("Duration", 5, 180, plan.get("duration_min", 30), unit="min")
        self.heart_rate = LabeledSlider("Average heart rate", 60, 210, plan.get("target_hr_bpm", 130), unit="bpm")
        self.rpe = LabeledSlider("Effort (RPE)", 1, 10, 5, describe=lambda v: RPE_WORDS.get(v, ""))
        # Rides are measured in km/h, runs and walks in min/km (CoachService.logs_speed decides).
        self._speed_mode = self.app.service.logs_speed(plan)
        low, high = LOG_LIMITS["avg_speed_kmh"]
        if self._speed_mode:
            tracked = True                 # bike computers and watches almost always record speed
            self.pace = LabeledSlider("Average speed", low, high, self.app.service.suggested_speed(plan),
                                      step=0.5, unit="km/h")
        else:
            tracked = plan.get("target_pace_min_per_km") is not None
            self.pace = LabeledSlider("Average pace", 3.0, 15.0, plan.get("target_pace_min_per_km") or 6.0,
                                      step=0.1, unit="min/km")
        self.track_pace = ft.Switch(label="I tracked my speed" if self._speed_mode else "I tracked my pace",
                                    value=tracked, active_color=Palette.ON_ACCENT,
                                    active_track_color=Palette.ACCENT, on_change=self._pace_toggled)
        self.pace.control.visible = tracked
        self.pain = ft.Checkbox(label="I felt pain or discomfort", value=False, fill_color=Palette.ACCENT,
                                check_color=Palette.ON_ACCENT)
        self.comments = ft.TextField(label="Comments", hint_text="How it felt, where it hurt...",
                                     multiline=True, min_lines=2, max_lines=4, border=field_border())
        return ft.Column([
            ft.Row([activity_badge(plan.get("activity"), size=36),
                    ft.Text(str(plan.get("exercise_type", "Session")).capitalize(), weight=ft.FontWeight.W_600,
                            expand=True)]),
            self.duration.control, self.heart_rate.control, self.rpe.control,
            self.track_pace, self.pace.control, self.pain, self.comments,
        ], spacing=10, tight=True)

    def _pace_toggled(self, e):
        self.pace.control.visible = self.track_pace.value

    def actions(self):
        return [
            ft.TextButton("Cancel", on_click=lambda e: self.close()),
            ft.FilledButton("Save session", icon=ft.Icons.CHECK, on_click=self._save,
                            style=ft.ButtonStyle(bgcolor=Palette.ACCENT, color=Palette.ON_ACCENT)),
        ]

    def log(self):
        tracked = self.pace.value if self.track_pace.value else None
        return {
            "duration_min": self.duration.value,
            "avg_pace_min_per_km": None if self._speed_mode else tracked,
            "avg_speed_kmh": tracked if self._speed_mode else None,
            "avg_hr_bpm": self.heart_rate.value,
            "rpe": self.rpe.value,
            "pain_reported": bool(self.pain.value),
            "comments": (self.comments.value or "").strip(),
        }

    def _save(self, e):
        errors = self.app.service.log_session(self.log())
        if errors:
            self.show_errors(errors)
            return
        self.close()
        self._on_saved()


class RejectDialog(BaseDialog):
    """Quick-reason chips plus free text; the reason goes back to the AI."""

    QUICK_REASONS = ("Too long", "Too hard", "Too easy", "Different activity", "No equipment today", "Short on time")

    def __init__(self, app, on_submit):
        super().__init__(app)
        self._on_submit = on_submit

    def title(self):
        return "What should change?"

    def content(self):
        self.chips = [ft.Chip(label=reason, on_select=lambda e: None, selected_color=Palette.ACCENT_SOFT,
                              check_color=Palette.ACCENT) for reason in self.QUICK_REASONS]
        self.details = ft.TextField(label="Anything else?", hint_text="e.g. my knee is sore, keep it low impact",
                                    multiline=True, min_lines=1, max_lines=3, border=field_border())
        return ft.Column([
            label("The coach adapts the next draft to your feedback.", size=12, weight=ft.FontWeight.W_500),
            ft.Row(self.chips, wrap=True, spacing=8, run_spacing=8),
            self.details,
        ], spacing=12, tight=True)

    def actions(self):
        return [
            ft.TextButton("Cancel", on_click=lambda e: self.close()),
            ft.FilledButton("Ask for another", icon=ft.Icons.REFRESH, on_click=self._submit,
                            style=ft.ButtonStyle(bgcolor=Palette.ACCENT, color=Palette.ON_ACCENT)),
        ]

    def reason(self):
        parts = [chip.label.lower() for chip in self.chips if chip.selected]
        text = (self.details.value or "").strip()
        if text:
            parts.append(text)
        return "; ".join(parts)

    async def _submit(self, e):
        reason = self.reason()
        if not reason:
            self.show_errors(["pick a reason or type one, so the coach knows what to change"])
            return
        self.close()
        await self._on_submit(reason)


class ConfirmDialog(BaseDialog):
    def __init__(self, app, title, message, confirm_text, on_confirm):
        super().__init__(app)
        self._title, self._message = title, message
        self._confirm_text, self._on_confirm = confirm_text, on_confirm

    def title(self):
        return self._title

    def content(self):
        return ft.Text(self._message, color=Palette.MUTED)

    def actions(self):
        return [ft.TextButton("Cancel", on_click=lambda e: self.close()),
                ft.FilledButton(self._confirm_text, on_click=self._confirm,
                                style=ft.ButtonStyle(bgcolor=Palette.ERROR, color=Palette.ON_ERROR))]

    async def _confirm(self, e):
        self.close()
        result = self._on_confirm()
        if inspect.isawaitable(result):
            await result


class ResetDialog(ConfirmDialog):
    """Destructive action: the delete button only enables after typing RESET."""

    def __init__(self, app, on_confirm):
        super().__init__(app, "Delete all data?",
                         "This permanently deletes your profile and ALL session history.",
                         "Delete everything", on_confirm)

    def content(self):
        self.confirm_field = ft.TextField(label="Type RESET to confirm", on_change=self._typed, border=field_border())
        return ft.Column([super().content(), self.confirm_field], tight=True, spacing=12)

    def actions(self):
        buttons = super().actions()
        self.delete_button = buttons[-1]
        self.delete_button.disabled = True
        return buttons

    def _typed(self, e):
        self.delete_button.disabled = (self.confirm_field.value or "").strip() != "RESET"


class DayPickerDialog(BaseDialog):
    """Pick one calendar day between two dates.

    Deliberately not Flet's DatePicker: that control reports the chosen day as a UTC timestamp
    of local midnight, so for anyone east of Greenwich (Singapore is UTC+8) the server read it
    as the day before. This dialog works with plain dates only, so no time zone can shift them.
    """

    def __init__(self, app, on_pick, initial, first, last, help_text="Choose a day"):
        super().__init__(app)
        self._on_pick = on_pick
        self._first, self._last = first, max(first, last)
        self._help = help_text
        self.chosen = min(max(initial, self._first), self._last)
        self._month = self.chosen.replace(day=1)
        self._body = ft.Column(spacing=8, tight=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
        self._render()

    def title(self):
        return self._help

    def content(self):
        return self._body

    def actions(self):
        return [ft.TextButton("Cancel", on_click=lambda e: self.close()),
                ft.FilledButton("Select", icon=ft.Icons.CHECK, on_click=self._confirm,
                                style=ft.ButtonStyle(bgcolor=Palette.ACCENT, color=Palette.ON_ACCENT))]

    # ---- drawing ----
    def _first_weekday(self):
        return 6 if self.app.service.settings.week_starts_on == "sunday" else 0

    def _render(self):
        first_month, last_month = self._first.replace(day=1), self._last.replace(day=1)
        header = ft.Row([
            ft.IconButton(ft.Icons.CHEVRON_LEFT, tooltip="Previous month", disabled=self._month <= first_month,
                          on_click=lambda e: self._shift(-1)),
            ft.Container(heading(self._month.strftime("%B %Y"), size=16), expand=True, alignment=ft.Alignment.CENTER),
            ft.IconButton(ft.Icons.CHEVRON_RIGHT, tooltip="Next month", disabled=self._month >= last_month,
                          on_click=lambda e: self._shift(1)),
        ])
        names = ("M", "T", "W", "T", "F", "S", "S")
        start = self._first_weekday()
        weekdays = ft.Row([ft.Container(label(name, size=11), expand=1, alignment=ft.Alignment.CENTER)
                           for name in names[start:] + names[:start]])
        weeks = calendar.Calendar(firstweekday=start).monthdatescalendar(self._month.year, self._month.month)
        rows = [ft.Row([self._cell(day) for day in week], spacing=4) for week in weeks]
        self._chosen_text = label(self._describe(), size=12, weight=ft.FontWeight.W_500)
        self._body.controls = [header, weekdays, *rows, ft.Divider(color=Palette.BORDER), self._chosen_text]

    def _describe(self):
        return "Selected: " + self.chosen.strftime("%A %d %B %Y")

    def _cell(self, day):
        if day.month != self._month.month:
            return ft.Container(expand=1, height=40)          # blank: days of other months are not offered
        allowed = self._first <= day <= self._last
        picked = day == self.chosen
        is_today = day == self.app.service.today
        return ft.Container(
            ft.Text(str(day.day), size=13, weight=ft.FontWeight.W_700 if picked else ft.FontWeight.W_500,
                    color=Palette.ON_ACCENT if picked else (Palette.TEXT if allowed else Palette.BORDER)),
            expand=1, height=40, border_radius=12, alignment=ft.Alignment.CENTER,
            bgcolor=Palette.ACCENT if picked else None,
            border=ft.Border.all(1.5, Palette.ACCENT) if is_today and not picked else None,
            on_click=(lambda e, d=day: self._choose(d)) if allowed else None,
        )

    # ---- events ----
    def _shift(self, months):
        year, month = self._month.year, self._month.month + months
        self._month = datetime.date(year + (month - 1) // 12, (month - 1) % 12 + 1, 1)
        self._render()
        self.app.page.update()

    def _choose(self, day):
        self.chosen = day
        self._render()
        self.app.page.update()

    def _confirm(self, e):
        self.close()
        self._on_pick(self.chosen)


class SessionDetailDialog(BaseDialog):
    """Details of any CalendarEvent (completed, imported or planned) via its shared interface.

    For a session that has been done (event.can_review), it also shows the app's plan-vs-actual
    comparison straight away, and a button that asks the AI coach for written feedback. The
    feedback is saved with the session, so it is shown again next time without another request.
    """

    DIRECTION_STYLE = {          # direction -> (icon, colour attribute on Palette)
        "on": (ft.Icons.CHECK_CIRCLE_OUTLINE, "DONE"),
        "over": (ft.Icons.ARROW_UPWARD, "PAIN"),
        "under": (ft.Icons.ARROW_DOWNWARD, "PLANNED"),
    }

    def __init__(self, app, event, extra_actions=None, on_change=None):
        super().__init__(app)
        self._event = event
        self._extra_actions = extra_actions or []
        self._on_change = on_change
        self._feedback = event.feedback
        self._busy = False
        self._feedback_area = ft.Column(spacing=10, tight=True)

    def title(self):
        return self._event.title.capitalize()

    def content(self):
        event = self._event
        color = STATUS_COLORS[event.status]
        parts = [
            ft.Row([activity_badge(event.activity, size=40),
                    ft.Column([heading(event.day.strftime("%A %d %B %Y"), size=15),
                               pill(STATUS_LABELS[event.status], color)], spacing=4, expand=True)], spacing=12),
            ft.Divider(color=Palette.BORDER),
            detail_rows(event.details()),
        ]
        if event.can_review:
            parts += [ft.Divider(color=Palette.BORDER), self._review_section(), self._feedback_area]
            self._render_feedback()
        return ft.Column(parts, spacing=12, tight=True)

    # ---- the app's own comparison (instant, no AI) ----
    def _review_section(self):
        review = self.app.service.review_event(self._event)
        planned = review.adherence != "no_plan"
        rows = [ft.Row([heading("Plan vs actual" if planned else "Session review", size=15),
                        pill(review.label, Palette.ACCENT if review.adherence == "on_plan" else Palette.MUTED)],
                       alignment=ft.MainAxisAlignment.SPACE_BETWEEN, wrap=True)]
        for check in review.checks:
            rows.append(self._check_row(check))
        for flag in review.flags:
            rows.append(ft.Row([ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, size=16, color=Palette.PAIN),
                                ft.Text(flag, size=12, color=Palette.PAIN, expand=True)],
                               vertical_alignment=ft.CrossAxisAlignment.START))
        if not review.checks:
            rows.append(label("No numbers to compare for this session.", size=12, weight=ft.FontWeight.W_500))
        return ft.Column(rows, spacing=8, tight=True)

    def _check_row(self, check):
        value = check.actual if not check.planned else "{} → {}".format(check.planned, check.actual)
        icon = []
        if check.direction in self.DIRECTION_STYLE:
            name, colour = self.DIRECTION_STYLE[check.direction]
            icon = [ft.Icon(name, size=16, color=getattr(Palette, colour))]
        return ft.Row([
            ft.Container(label(check.metric, size=12), width=86),
            ft.Column([ft.Text(value, size=13, color=Palette.TEXT), label(check.note, size=11,
                                                                         weight=ft.FontWeight.W_500)],
                      spacing=1, expand=True),
            *icon,
        ], vertical_alignment=ft.CrossAxisAlignment.START)

    # ---- the AI coach's written feedback ----
    def _render_feedback(self, error=None):
        controls = []
        feedback = self._feedback
        if feedback:
            controls.append(self._feedback_card(feedback))
        if error:
            controls.append(ft.Text(error[0].upper() + error[1:], color=Palette.ERROR, size=12))
        if self._busy:
            controls.append(ft.Row([ft.ProgressRing(width=18, height=18, stroke_width=2, color=Palette.ACCENT),
                                    label("The coach is reviewing this session...", size=12,
                                          weight=ft.FontWeight.W_500)], spacing=10))
        else:
            self.feedback_button = ft.FilledButton(
                "Regenerate feedback" if feedback else "Get coach feedback",
                icon=ft.Icons.REFRESH if feedback else ft.Icons.AUTO_AWESOME,
                on_click=self._ask_coach,
                style=ft.ButtonStyle(bgcolor=Palette.ACCENT, color=Palette.ON_ACCENT))
            controls.append(self.feedback_button)
        self._feedback_area.controls = controls

    def _feedback_card(self, feedback):
        def bullets(title, items, icon, colour):
            if not items:
                return []
            return [label(title, size=12)] + [
                ft.Row([ft.Icon(icon, size=14, color=colour), ft.Text(item, size=13, expand=True)],
                       vertical_alignment=ft.CrossAxisAlignment.START, spacing=8) for item in items]
        generated = feedback.get("generated_on")
        try:
            generated = datetime.date.fromisoformat(generated).strftime("%d %b %Y")
        except (TypeError, ValueError):
            generated = None
        return ft.Container(
            ft.Column([
                ft.Row([ft.Icon(ft.Icons.AUTO_AWESOME, size=16, color=Palette.ACCENT),
                        heading("Coach feedback", size=14)], spacing=8),
                ft.Text(feedback.get("summary", ""), size=13),
                *bullets("What went well", feedback.get("went_well"), ft.Icons.CHECK, Palette.DONE),
                *bullets("To work on", feedback.get("to_improve"), ft.Icons.TRENDING_UP, Palette.PAIN),
                label("Next time", size=12),
                ft.Text(feedback.get("next_time", ""), size=13),
                label("Written {}".format(generated) if generated else "", size=11, weight=ft.FontWeight.W_500),
            ], spacing=6, tight=True),
            padding=12, border_radius=14, bgcolor=Palette.CARD_HIGH,
        )

    async def _ask_coach(self, e):
        if self._busy:
            return
        self._busy = True
        self._render_feedback()
        self.app.page.update()
        result = await asyncio.to_thread(self.app.service.session_feedback, self._event.record_id)
        self._busy = False
        error = None
        if result.ok:
            self._feedback = result.feedback
            if not result.saved:
                error = "the feedback could not be saved, so it will not be shown next time"
            if self._on_change:
                self._on_change()
        else:
            error = result.error
        self._render_feedback(error)
        self.app.page.update()

    def actions(self):
        return self._extra_actions + super().actions()


class StravaHelpDialog(BaseDialog):
    """The '?' next to the Strava import: where activities.csv comes from."""

    STEPS = (
        "On a computer, sign in at strava.com. Exporting only works on the website, not in the phone app.",
        "Click your profile picture (top right), then Settings, then My Account.",
        "Under 'Download or Delete Your Account', click Get Started, then 'Request your archive'.",
        "Strava emails you a download link. This can take a few hours, sometimes longer.",
        "Download the .zip file and unzip it. Inside is a file called activities.csv: upload that file here.",
    )

    def title(self):
        return "Where do I find activities.csv?"

    def content(self):
        steps = [
            ft.Row([
                ft.Container(ft.Text(str(number), size=12, weight=ft.FontWeight.W_700, color=Palette.ON_ACCENT),
                             width=24, height=24, border_radius=12, bgcolor=Palette.ACCENT,
                             alignment=ft.Alignment.CENTER),
                ft.Text(text, size=13, expand=True),
            ], vertical_alignment=ft.CrossAxisAlignment.START, spacing=10)
            for number, text in enumerate(self.STEPS, start=1)
        ]
        return ft.Column([
            label("Your Strava archive holds every activity you have recorded.", size=12,
                  weight=ft.FontWeight.W_500),
            *steps,
            ft.Divider(color=Palette.BORDER),
            label("Uploading a newer archive later only adds the activities you do not have yet. "
                  "Imported activities are not sent to the AI coach unless you ask for feedback on one.",
                  size=12, weight=ft.FontWeight.W_500),
            ft.OutlinedButton("Open Strava's export guide", icon=ft.Icons.OPEN_IN_NEW,
                              action=ft.OpenUrl(STRAVA_HELP_URL, target=ft.UrlTarget.BLANK)),
        ], spacing=12, tight=True)
