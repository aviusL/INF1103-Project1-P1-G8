"""Pop-up dialogs: log a session, reject a suggestion, confirm, reset, session details."""
import inspect

import flet as ft

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
        has_pace = plan.get("target_pace_min_per_km") is not None
        self.track_pace = ft.Switch(label="I tracked my pace", value=has_pace, active_color=Palette.ON_ACCENT,
                                    active_track_color=Palette.ACCENT,
                                    on_change=self._pace_toggled)
        self.pace = LabeledSlider("Average pace", 3.0, 15.0, plan.get("target_pace_min_per_km") or 6.0,
                                  step=0.1, unit="min/km")
        self.pace.control.visible = has_pace
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
        return {
            "duration_min": self.duration.value,
            "avg_pace_min_per_km": self.pace.value if self.track_pace.value else None,
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


class SessionDetailDialog(BaseDialog):
    """Details of any CalendarEvent (completed or planned) via its shared interface."""

    def __init__(self, app, event, extra_actions=None):
        super().__init__(app)
        self._event = event
        self._extra_actions = extra_actions or []

    def title(self):
        return self._event.title.capitalize()

    def content(self):
        event = self._event
        color = STATUS_COLORS[event.status]
        return ft.Column([
            ft.Row([activity_badge(event.activity, size=40),
                    ft.Column([heading(event.day.strftime("%A %d %B"), size=15),
                               pill(STATUS_LABELS[event.status], color)], spacing=4)], spacing=12),
            ft.Divider(color=Palette.BORDER),
            detail_rows(event.details()),
        ], spacing=12, tight=True)

    def actions(self):
        return self._extra_actions + super().actions()
