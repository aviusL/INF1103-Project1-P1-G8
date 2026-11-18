"""Home tab: this week's progress, the next session, and the AI suggestion flow."""
import asyncio
import datetime

import flet as ft

from coach_service import CompletedSession
from gui.base_view import AppView
from gui.dialogs import ConfirmDialog, LogSessionDialog, RejectDialog
from gui.theme import INTENSITY_COLORS, Palette, on_color
from gui.widgets import (
    activity_badge, heading, intensity_pill, label, panel, pill, primary_button, secondary_button,
    section, session_plan_column, session_stats_row,
)


def nice_day(day, today):
    if day == today:
        return "Today"
    if day == today + datetime.timedelta(days=1):
        return "Tomorrow"
    return day.strftime("%a %d %b")


class HomeView(AppView):
    label = "Home"
    icon = ft.Icons.HOME_OUTLINED
    selected_icon = ft.Icons.HOME_ROUNDED

    def __init__(self, app):
        super().__init__(app)
        self._draft = None            # SuggestionResult waiting for accept / reject
        self._busy = False
        self._busy_text = ft.Text(color=Palette.MUTED, text_align=ft.TextAlign.CENTER)

    # ------------------------------------------------------------------ layout
    def restyle(self):
        self._busy_text = ft.Text(self._busy_text.value, color=Palette.MUTED, text_align=ft.TextAlign.CENTER)

    def build(self):
        controls = [self._header()]
        if not self.service.has_profile:
            controls.append(self._onboarding())
            return controls
        controls.append(self._week_card())
        if self._busy:
            controls.append(self._thinking_card())
        elif self._draft:
            controls.append(self._suggestion_card())
        elif self.service.pending_session:
            controls.append(self._up_next_card())
        else:
            controls.append(self._plan_card())
        controls.append(self._recent_activity())
        controls.append(ft.Container(height=8))
        return controls

    def _header(self):
        profile = self.service.profile or {}
        name = str(profile.get("name") or "").strip()
        initials = "".join(part[0] for part in name.split()[:2]).upper() or None
        today = self.service.today
        return ft.Row([
            ft.Column([
                label(today.strftime("%A %d %B").upper(), size=11),
                heading("Hi {}".format(name.split()[0]) if name else "Today", size=26),
            ], spacing=2),
            ft.Container(
                content=ft.CircleAvatar(
                    content=ft.Text(initials) if initials else ft.Icon(ft.Icons.PERSON),
                    bgcolor=Palette.ACCENT, color=Palette.ON_ACCENT, radius=22),
                on_click=lambda e: self.app.go(self.app.PROFILE), tooltip="Your profile",
            ),
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)

    def _onboarding(self):
        return panel(ft.Column([
            ft.Icon(ft.Icons.WAVING_HAND_OUTLINED, color=Palette.ACCENT, size=36),
            heading("Welcome!", size=22),
            ft.Text("Tell the coach about yourself and it will plan your first session, "
                    "then adapt every session to how the last one went.", color=Palette.MUTED),
            primary_button("Create my profile", ft.Icons.ARROW_FORWARD,
                           on_click=lambda e: self.app.go(self.app.PROFILE)),
        ], spacing=12))

    def _week_card(self):
        target = self.service.weekly_target()
        planned = target["sessions_per_week"]
        done = min(target["session_number"] - 1, planned)
        minutes = sum((s.get("actual") or {}).get("duration_min") or 0 for s in self.service.history()
                      if self._in_this_week(s))
        steps = []
        for number, intensity in enumerate(target["pattern"]):
            finished = number < done
            current = number == done and not target["week_complete"]
            color = INTENSITY_COLORS.get(intensity, Palette.MUTED)
            steps.append(ft.Container(
                content=ft.Icon(ft.Icons.CHECK, size=14, color=on_color(Palette.DONE)) if finished
                else ft.Text(intensity[0].upper(), size=11, weight=ft.FontWeight.W_800,
                             color=on_color(color) if current else color),
                width=30, height=30, border_radius=15, alignment=ft.Alignment.CENTER,
                bgcolor=Palette.DONE if finished else (color if current else ft.Colors.with_opacity(0.12, color)),
                tooltip="Session {}: {}".format(number + 1, intensity),
            ))
        if target["week_complete"]:
            next_text = "Week complete! Extra sessions are light recovery."
        else:
            next_text = "Next: session {} of {}, {} intensity".format(target["session_number"], planned,
                                                                       target["intensity"])
        return panel(ft.Row([
            ft.Stack([
                ft.ProgressRing(value=done / planned if planned else 0, width=72, height=72, stroke_width=7,
                                color=Palette.ACCENT, bgcolor=Palette.BORDER),
                ft.Container(ft.Text("{}/{}".format(done, planned), size=18, weight=ft.FontWeight.W_800),
                             width=72, height=72, alignment=ft.Alignment.CENTER),
            ], width=72, height=72),
            ft.Column([
                label("THIS WEEK", size=11),
                ft.Text("{} sessions · {} min".format(done, int(minutes)), size=16, weight=ft.FontWeight.W_700),
                ft.Row(steps, spacing=6, wrap=True),
                label(next_text, size=12, weight=ft.FontWeight.W_500),
            ], spacing=6, expand=True),
        ], spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER))

    def _in_this_week(self, record):
        try:
            day = datetime.date.fromisoformat(record.get("date"))
        except (TypeError, ValueError):
            return False
        return day.isocalendar()[:2] == self.service.today.isocalendar()[:2]

    def _plan_card(self):
        return panel(ft.Column([
            ft.Row([ft.Icon(ft.Icons.AUTO_AWESOME, color=Palette.ACCENT),
                    ft.Text("Ready for your next session?", size=16, weight=ft.FontWeight.W_700)]),
            ft.Text("Your coach plans it from your profile and your last sessions, "
                    "then the safety rules check it before you see it.", color=Palette.MUTED, size=13),
            primary_button("Plan my next session", ft.Icons.BOLT, on_click=self._on_plan_click, expand=True),
        ], spacing=12))

    def _thinking_card(self):
        return panel(ft.Column([
            ft.ProgressRing(width=36, height=36, color=Palette.ACCENT),
            self._busy_text,
        ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=14), padding=28)

    def _session_title(self, plan):
        return ft.Row([
            activity_badge(plan.get("activity")),
            ft.Column([
                ft.Text(str(plan.get("exercise_type", "Session")).capitalize(), size=17, weight=ft.FontWeight.W_700),
                ft.Row([label(str(plan.get("activity", "")).capitalize()), intensity_pill(plan.get("intensity"))],
                       spacing=8),
            ], spacing=4, expand=True),
        ], spacing=12)

    def _suggestion_card(self):
        plan, decision = self._draft.suggestion, self._draft.decision
        if decision["outcome"] == "flag":
            verdict = pill("Score {}/100".format(decision["score"]), Palette.PAIN)
        else:
            verdict = pill("Passed safety check", Palette.DONE)
        body = [
            ft.Row([label("SUGGESTED SESSION", size=11), verdict], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            self._session_title(plan),
            session_stats_row(plan),
        ]
        if decision["reasons"]:
            body.append(ft.Container(
                content=ft.Column([ft.Row([ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color=Palette.PAIN, size=16),
                                           ft.Text(reason, size=12, color=Palette.PAIN, expand=True)])
                                   for reason in decision["reasons"]], spacing=6),
                bgcolor=ft.Colors.with_opacity(0.08, Palette.PAIN), border_radius=12, padding=12))
        if self._draft.revisions:
            body.append(label("Revised {} time(s) to pass the safety rules.".format(self._draft.revisions),
                              size=11, weight=ft.FontWeight.W_500))
        body += [
            ft.Divider(color=Palette.BORDER),
            session_plan_column(plan),
            ft.Row([
                secondary_button("Not for me", ft.Icons.THUMB_DOWN_OUTLINED, on_click=self._on_reject_click, expand=True),
                primary_button("Accept", ft.Icons.CHECK, on_click=self._on_accept_click, expand=True),
            ], spacing=12),
        ]
        return panel(ft.Column(body, spacing=14), border_color=Palette.ACCENT)

    def _up_next_card(self):
        plan = self.service.pending_session
        day = self.service.pending_date()
        today = self.service.today
        when = nice_day(day, today).upper()
        if day < today:
            when = "OVERDUE · " + day.strftime("%a %d %b").upper()
        return panel(ft.Column([
            ft.Row([label("UP NEXT · " + when, size=11, color=Palette.ERROR if day < today else Palette.MUTED),
                    ft.Row([
                        ft.IconButton(ft.Icons.EDIT_CALENDAR, tooltip="Reschedule", icon_color=Palette.MUTED,
                                      on_click=lambda e: self.reschedule_pending()),
                        ft.IconButton(ft.Icons.DELETE_OUTLINE, tooltip="Discard this session",
                                      icon_color=Palette.MUTED, on_click=self._on_discard_click),
                    ], spacing=0)],
                   alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            self._session_title(plan),
            session_stats_row(plan),
            ft.ExpansionTile(title=ft.Text("Session plan", size=14, weight=ft.FontWeight.W_600),
                             controls=[ft.Container(session_plan_column(plan), padding=ft.Padding.only(bottom=12))],
                             tile_padding=0, collapsed_icon_color=Palette.MUTED, icon_color=Palette.ACCENT,
                             shape=ft.RoundedRectangleBorder(radius=0),
                             collapsed_shape=ft.RoundedRectangleBorder(radius=0)),
            primary_button("Log this session", ft.Icons.CHECK_CIRCLE_OUTLINE,
                           on_click=lambda e: self.log_pending(), expand=True),
        ], spacing=12))

    def _recent_activity(self):
        records = list(reversed(self.service.history()))[:3]
        if not records:
            rows = [ft.Text("No sessions logged yet. Your finished sessions will show up here.",
                            color=Palette.MUTED, size=13)]
        else:
            rows = []
            for record in records:
                try:
                    day = datetime.date.fromisoformat(record.get("date"))
                except (TypeError, ValueError):
                    continue
                event = CompletedSession(day, record)
                rows.append(ft.Row([
                    activity_badge(event.activity, size=36),
                    ft.Column([ft.Text(event.title.capitalize(), weight=ft.FontWeight.W_600, size=14),
                               label(nice_day(day, self.service.today) + " · " + event.summary(),
                                     weight=ft.FontWeight.W_500)], spacing=2, expand=True),
                ], spacing=12))
        return section("Recent activity", *rows,
                       trailing=ft.TextButton("Calendar", on_click=lambda e: self.app.go(self.app.CALENDAR)))

    # ------------------------------------------------------------------ actions
    async def _on_plan_click(self, e):
        await self.start_suggestion()

    async def start_suggestion(self, feedback=None):
        """Ask the coach for a session without freezing the window (the API call runs in a thread)."""
        if self._busy:
            return
        loop = asyncio.get_running_loop()
        self._busy, self._draft = True, None
        self._busy_text.value = ("Re-planning with your feedback..." if feedback
                                 else "Your coach is planning the next session...")
        self.app.refresh_current()

        def on_revision(reasons):        # runs on the worker thread
            loop.call_soon_threadsafe(self._set_busy_text,
                                      "A draft broke a safety rule ({}). Asking the coach to revise...".format(reasons[0]))

        result = await asyncio.to_thread(self.service.suggest_session, feedback, None, on_revision)
        self._busy = False
        if result.ok:
            self._draft = result
        else:
            self.app.notify(result.error[0].upper() + result.error[1:] + ".", error=True)
        self.app.refresh_current()

    def _set_busy_text(self, text):
        self._busy_text.value = text
        self.app.page.update()

    def _on_accept_click(self, e):
        today = self.service.today
        self.app.pick_date(self._accept_on, initial=today, last=today + datetime.timedelta(days=14),
                           help_text="When will you do this session?")

    def _accept_on(self, day):
        if self.service.accept_session(self._draft.suggestion, day):
            self._draft = None
            self.app.notify("Scheduled for {}. Log it when you're done!".format(day.strftime("%A %d %b")))
        else:
            self.app.notify("Could not save the session to disk.", error=True)
        self.app.refresh_current()

    def _on_reject_click(self, e):
        RejectDialog(self.app, on_submit=self._rejected).open()

    async def _rejected(self, reason):
        await self.start_suggestion(feedback=reason)

    def _on_discard_click(self, e):
        def discard():
            self.service.discard_pending()
            self.app.notify("Session discarded.")
            self.app.refresh_current()
        ConfirmDialog(self.app, "Discard this session?",
                      "It will not be logged. You can ask the coach for a new one.", "Discard", discard).open()

    def reschedule_pending(self):
        today = self.service.today

        def moved(day):
            if self.service.reschedule_pending(day):
                self.app.notify("Moved to {}.".format(day.strftime("%A %d %b")))
            self.app.refresh_current()
        self.app.pick_date(moved, initial=max(self.service.pending_date(), today), help_text="Move session to")

    def log_pending(self):
        LogSessionDialog(self.app, self.service.pending_session, on_saved=self._logged).open()

    def _logged(self):
        self.app.notify("Session logged. Nice work!", action="Plan next",
                        on_action=lambda e: self.app.page.run_task(self._plan_next))
        self.app.refresh_current()

    async def _plan_next(self):
        self.app.go(self.app.HOME)
        await self.start_suggestion()
