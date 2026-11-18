"""Calendar tab: month grid with completed (past) and planned (upcoming) sessions."""
import calendar
import datetime

import flet as ft

from gui.base_view import AppView
from gui.dialogs import SessionDetailDialog
from gui.theme import STATUS_COLORS, STATUS_LABELS, Palette
from gui.widgets import activity_badge, heading, label, panel, pill, section, stat

WEEKDAYS = ("M", "T", "W", "T", "F", "S", "S")      # Monday first, as calendar.Calendar numbers them


class CalendarView(AppView):
    label = "Calendar"
    icon = ft.Icons.CALENDAR_MONTH_OUTLINED
    selected_icon = ft.Icons.CALENDAR_MONTH

    def __init__(self, app):
        super().__init__(app)
        today = self.service.today
        self._month = today.replace(day=1)
        self._selected = today
        self._pain_only = False
        self._hard_only = False

    # ------------------------------------------------------------------ layout
    def build(self):
        events = self.service.calendar_events(self._month.year, self._month.month,
                                              pain_only=self._pain_only,
                                              min_rpe=8 if self._hard_only else None)
        return [
            self._header(),
            panel(ft.Column([self._weekday_row(), *self._grid(events), ft.Divider(color=Palette.BORDER),
                             self._legend()], spacing=6), padding=12),
            self._filters(),
            self._month_stats(events),
            self._day_details(events.get(self._selected, [])),
            ft.Container(height=8),
        ]

    def _header(self):
        return ft.Row([
            heading(self._month.strftime("%B %Y"), size=24),
            ft.Row([
                ft.IconButton(ft.Icons.CHEVRON_LEFT, on_click=lambda e: self._shift(-1), tooltip="Previous month"),
                ft.TextButton("Today", on_click=self._jump_today),
                ft.IconButton(ft.Icons.CHEVRON_RIGHT, on_click=lambda e: self._shift(1), tooltip="Next month"),
            ], spacing=0),
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)

    def _first_weekday(self):
        """0 = Monday, 6 = Sunday, from Settings > Preferences."""
        return 6 if self.service.settings.week_starts_on == "sunday" else 0

    def _weekday_row(self):
        first = self._first_weekday()
        days = WEEKDAYS[first:] + WEEKDAYS[:first]
        return ft.Row([ft.Container(label(day, size=11), expand=1, alignment=ft.Alignment.CENTER)
                       for day in days])

    def _grid(self, events):
        weeks = calendar.Calendar(firstweekday=self._first_weekday()).monthdatescalendar(
            self._month.year, self._month.month)
        return [ft.Row([self._cell(day, events.get(day, [])) for day in week], spacing=4) for week in weeks]

    def _cell(self, day, day_events):
        in_month = day.month == self._month.month
        is_today = day == self.service.today
        is_selected = day == self._selected
        dots = [ft.Container(width=6, height=6, border_radius=3, bgcolor=STATUS_COLORS[event.status])
                for event in day_events[:3]]
        return ft.Container(
            expand=1, height=50, border_radius=12,
            bgcolor=Palette.ACCENT if is_selected else (Palette.CARD_HIGH if day_events and in_month else None),
            border=ft.Border.all(1.5, Palette.ACCENT) if is_today and not is_selected else None,
            alignment=ft.Alignment.CENTER,
            on_click=(lambda e, d=day: self._select(d)),
            content=ft.Column([
                ft.Text(str(day.day), size=14,
                        weight=ft.FontWeight.W_700 if is_today or is_selected else ft.FontWeight.W_500,
                        color=Palette.ON_ACCENT if is_selected else (Palette.TEXT if in_month else Palette.BORDER)),
                ft.Row(dots, spacing=3, alignment=ft.MainAxisAlignment.CENTER, height=6),
            ], spacing=3, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER, tight=True),
        )

    def _legend(self):
        items = []
        for status in ("done", "pain", "planned"):
            items.append(ft.Row([ft.Container(width=8, height=8, border_radius=4, bgcolor=STATUS_COLORS[status]),
                                 label(STATUS_LABELS[status], size=11, weight=ft.FontWeight.W_500)], spacing=6))
        return ft.Row(items, alignment=ft.MainAxisAlignment.SPACE_AROUND, wrap=True)

    def _filters(self):
        return ft.Row([
            ft.Checkbox(label="Pain only", value=self._pain_only, fill_color=Palette.ACCENT,
                        on_change=self._toggle_pain),
            ft.Checkbox(label="Hard sessions (RPE 8+)", value=self._hard_only, fill_color=Palette.ACCENT,
                        on_change=self._toggle_hard),
        ], wrap=True, spacing=4)

    def _month_stats(self, events):
        completed = [event for day_events in events.values() for event in day_events if event.status != "planned"]
        minutes = sum(event.actual.get("duration_min") or 0 for event in completed)
        efforts = [event.actual.get("rpe") for event in completed if isinstance(event.actual.get("rpe"), (int, float))]
        avg = "-" if not efforts else "{:.1f}".format(sum(efforts) / len(efforts))
        return panel(ft.Row([
            stat(len(completed), "Sessions", ft.Icons.CHECK_CIRCLE_OUTLINE),
            stat(int(minutes), "Minutes", ft.Icons.TIMER_OUTLINED),
            stat(avg, "Avg effort", ft.Icons.LOCAL_FIRE_DEPARTMENT_OUTLINED),
        ], alignment=ft.MainAxisAlignment.SPACE_AROUND))

    def _day_details(self, day_events):
        title = self._selected.strftime("%A %d %B")
        if not day_events:
            if self._selected < self.service.today:
                text = "Rest day - nothing logged."
            else:
                text = "Nothing planned yet."
            rows = [ft.Text(text, color=Palette.MUTED, size=13)]
            if self._selected >= self.service.today and self.service.has_profile and not self.service.pending_session:
                rows.append(ft.TextButton("Plan a session", icon=ft.Icons.BOLT,
                                          on_click=self._plan_from_calendar))
        else:
            rows = [self._event_row(event) for event in day_events]
        return section(title, *rows)

    def _event_row(self, event):
        color = STATUS_COLORS[event.status]
        return ft.Container(
            content=ft.Row([
                activity_badge(event.activity, size=38),
                ft.Column([ft.Text(event.title.capitalize(), weight=ft.FontWeight.W_600),
                           label(event.summary(), weight=ft.FontWeight.W_500)], spacing=2, expand=True),
                pill(STATUS_LABELS[event.status], color),
            ], spacing=12),
            padding=10, border_radius=14, bgcolor=Palette.CARD_HIGH,
            on_click=lambda e, ev=event: self._open_event(ev),
        )

    # ------------------------------------------------------------------ actions
    def _shift(self, months):
        year, month = self._month.year, self._month.month + months
        year, month = year + (month - 1) // 12, (month - 1) % 12 + 1
        self._month = datetime.date(year, month, 1)
        self._selected = self._month if (year, month) != self.service.today.timetuple()[:2] else self.service.today
        self.refresh()

    def _jump_today(self, e):
        self.show_today()

    def show_today(self):
        """Go back to the current month with today selected (also used after a time zone change)."""
        self._month = self.service.today.replace(day=1)
        self._selected = self.service.today
        self.refresh()

    def _select(self, day):
        self._selected = day
        if day.month != self._month.month:
            self._month = day.replace(day=1)
        self.refresh()

    def _toggle_pain(self, e):
        self._pain_only = bool(e.control.value)
        self.refresh()

    def _toggle_hard(self, e):
        self._hard_only = bool(e.control.value)
        self.refresh()

    def _open_event(self, event):
        home = self.app.views[self.app.HOME]
        extra = []
        if event.status == "planned":
            extra = [
                ft.TextButton("Reschedule", icon=ft.Icons.EDIT_CALENDAR,
                              on_click=lambda e: (self.app.close_dialog(), home.reschedule_pending())),
                ft.TextButton("Log it", icon=ft.Icons.CHECK,
                              on_click=lambda e: (self.app.close_dialog(), home.log_pending())),
            ]
        SessionDetailDialog(self.app, event, extra).open()

    async def _plan_from_calendar(self, e):
        self.app.go(self.app.HOME)
        await self.app.views[self.app.HOME].start_suggestion()
