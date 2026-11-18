"""Progress tab: the weekly progress report (Phase 2 new feature) plus an AI coach's note."""
import asyncio

import flet as ft

from gui.base_view import AppView
from gui.theme import INTENSITY_COLORS, Palette
from gui.widgets import heading, label, panel, pill, primary_button, section, stat
from io_manager import ReportFormatter

CHART_HEIGHT = 110


class ProgressView(AppView):
    label = "Progress"
    icon = ft.Icons.INSIGHTS_OUTLINED
    selected_icon = ft.Icons.INSIGHTS

    def __init__(self, app):
        super().__init__(app)
        self._note = None             # WeeklyReportResult from the last "ask the coach"
        self._loading = False

    def build(self):
        if not self.service.has_profile:
            return [heading("Progress", size=26),
                    panel(ft.Text("Create your profile first - your weekly report will appear here.",
                                  color=Palette.MUTED))]
        report = self.service.weekly_report(with_coach_note=False).report
        return [
            ft.Column([heading("Your week", size=26), label(ReportFormatter.heading(report), size=12)], spacing=2),
            self._tiles(report),
            self._training_load(),
            self._comparison(report),
            self._mix(report),
            self._observations(report),
            self._coach_note(report),
            ft.Container(height=8),
        ]

    # ------------------------------------------------------------------ pieces
    def _tiles(self, report):
        this, last = report.this_week, report.last_week
        adherence = this.sessions / this.planned_sessions if this.planned_sessions else 0

        def tile(content):
            return ft.Container(panel(content, padding=14), col={"xs": 6, "md": 3})

        return ft.ResponsiveRow([
            tile(ft.Row([
                ft.ProgressRing(value=min(adherence, 1), width=40, height=40, stroke_width=5,
                                color=Palette.ACCENT, bgcolor=Palette.BORDER),
                stat("{}/{}".format(this.sessions, this.planned_sessions), "Sessions"),
            ], spacing=12)),
            tile(stat(ReportFormatter.amount(this.minutes), "Minutes · last wk {}".format(
                ReportFormatter.amount(last.minutes)), ft.Icons.TIMER_OUTLINED)),
            tile(stat("-" if this.avg_rpe is None else ReportFormatter.amount(this.avg_rpe),
                      "Avg effort (RPE)", ft.Icons.LOCAL_FIRE_DEPARTMENT_OUTLINED)),
            tile(stat(this.pain_sessions, "Pain sessions", ft.Icons.HEALING,
                      color=Palette.PAIN if this.pain_sessions else Palette.TEXT)),
        ], spacing=10, run_spacing=10)

    def _training_load(self):
        load = self.service.training_load(weeks=6)
        peak = max((minutes for _, minutes, _ in load), default=0) or 1
        bars = []
        for index, (week_start, minutes, sessions) in enumerate(load):
            current = index == len(load) - 1
            bars.append(ft.Column([
                label(ReportFormatter.amount(minutes) if minutes else "", size=10),
                ft.Container(
                    height=max(4, CHART_HEIGHT * minutes / peak), width=26, border_radius=8,
                    bgcolor=Palette.ACCENT if current else ft.Colors.with_opacity(0.35, Palette.ACCENT),
                    tooltip="{} session(s), {} min".format(sessions, ReportFormatter.amount(minutes)),
                    animate_size=400,
                ),
                label(week_start.strftime("%d %b"), size=10, weight=ft.FontWeight.W_500),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, alignment=ft.MainAxisAlignment.END,
                spacing=4, height=CHART_HEIGHT + 40))
        return section("Training load · minutes per week",
                       ft.Row(bars, alignment=ft.MainAxisAlignment.SPACE_AROUND,
                              vertical_alignment=ft.CrossAxisAlignment.END))

    def _comparison(self, report):
        def row(cells, bold=False, color=None):
            weight = ft.FontWeight.W_700 if bold else ft.FontWeight.W_500
            return ft.Row([
                ft.Text(cells[0], expand=4, size=13, color=Palette.MUTED, weight=weight),
                ft.Text(cells[1], expand=4, size=13, color=color or Palette.TEXT, weight=weight),
                ft.Text(cells[2], expand=4, size=13, color=Palette.MUTED, weight=weight),
            ])
        rows = [row(("", "This week", "Last week"), bold=True)]
        rows += [row(cells) for cells in ReportFormatter.rows(report)]
        return section("This week vs last week", *rows)

    def _mix(self, report):
        this = report.this_week
        if not this.sessions:
            return ft.Container()
        intensity = [pill("{} × {}".format(count, name), INTENSITY_COLORS.get(name, Palette.MUTED))
                     for name, count in this.intensity_counts.items()]
        activity = [pill("{} × {}".format(count, name), Palette.PLANNED) for name, count in this.activity_counts.items()]
        return section("What you did",
                       ft.Row(intensity, wrap=True, spacing=6, run_spacing=6),
                       ft.Row(activity, wrap=True, spacing=6, run_spacing=6))

    def _observations(self, report):
        rows = [ft.Row([ft.Icon(ft.Icons.TIPS_AND_UPDATES_OUTLINED, color=Palette.ACCENT, size=18),
                        ft.Text(text, size=13, expand=True)], vertical_alignment=ft.CrossAxisAlignment.START)
                for text in report.insights]
        return section("Observations", *(rows or [ft.Text("Nothing to flag this week.", color=Palette.MUTED)]))

    def _coach_note(self, report):
        if self._loading:
            body = [ft.Row([ft.ProgressRing(width=20, height=20, stroke_width=3, color=Palette.ACCENT),
                            ft.Text("The coach is reading your week...", color=Palette.MUTED)], spacing=12)]
        elif self._note and self._note.coach_note:
            note = self._note.coach_note
            body = [ft.Text(note["summary"], size=14),
                    ft.Container(ft.Row([ft.Icon(ft.Icons.FLAG_OUTLINED, color=Palette.ACCENT, size=18),
                                         ft.Text(note["focus_next_week"], size=13, expand=True)]),
                                 bgcolor=Palette.ACCENT_SOFT, border_radius=12, padding=12)]
        else:
            body = []
            if self._note and self._note.note_error:
                body.append(ft.Text("Coach's note unavailable: {}.".format(self._note.note_error),
                                    color=Palette.ERROR, size=12))
            if report.has_sessions:
                body.append(primary_button("Ask the coach about my week", ft.Icons.AUTO_AWESOME,
                                           on_click=self._ask_coach, expand=True))
            else:
                body.append(ft.Text("Log a session and the coach can comment on your week.", color=Palette.MUTED))
        return section("Coach's note", *body)

    async def _ask_coach(self, e):
        self._loading = True
        self.app.refresh_current()
        self._note = await asyncio.to_thread(self.service.weekly_report, True)
        self._loading = False
        self.app.refresh_current()
