"""Profile tab: editable profile (sliders, toggles, checkboxes, chips), Strava history import and data reset."""
import asyncio

import flet as ft

from coach_service import (
    ACTIVITY_OPTIONS, EQUIPMENT_OPTIONS, FITNESS_LEVELS, PROFILE_LIMITS, SEX_OPTIONS,
)
from gui.base_view import AppView
from gui.dialogs import ConfirmDialog, ResetDialog, StravaHelpDialog
from gui.theme import Palette, activity_icon
from gui.widgets import LabeledSlider, field_border, heading, label, panel, primary_button, section

DEFAULTS = {
    "name": "", "age": 30, "sex": None, "height_cm": 170, "weight_kg": 70, "fitness_level": "beginner",
    "health_concerns": "", "fitness_goal": "", "days_per_week": 3, "session_duration_min": 30,
    "equipment": [], "preferred_activities": [],
}


def bmi(height_cm, weight_kg):
    try:
        return weight_kg / (height_cm / 100) ** 2
    except (TypeError, ZeroDivisionError):
        return None


def bmi_band(value):
    if value is None:
        return "-"
    for limit, name in ((18.5, "underweight"), (25, "healthy range"), (30, "overweight")):
        if value < limit:
            return name
    return "obese range"


class ProfileView(AppView):
    label = "Profile"
    icon = ft.Icons.PERSON_OUTLINE
    selected_icon = ft.Icons.PERSON

    def __init__(self, app):
        super().__init__(app)
        self._form = None          # the editable controls, kept between tab switches until saved
        self._importing = False
        self._last_import = None   # ImportSummary of the latest upload, shown under the button
        self._import_body = ft.Column(spacing=10, tight=True)

    def build(self):
        if self._form is None:
            self._build_form(self.service.profile or {})
        return self._form

    def discard_edits(self):
        """Throw away unsaved edits; the next refresh rebuilds the form from saved data."""
        self._form = None

    def restyle(self):
        """New colours: rebuild the form, keeping anything typed but not saved yet."""
        if self._form is not None:
            self._build_form(self.current_profile(), is_new=not self.service.has_profile)

    # -------------------------------------------------------------------- form
    def _build_form(self, saved, is_new=None):
        values = dict(DEFAULTS, **saved)
        is_new = not saved if is_new is None else is_new
        limits = PROFILE_LIMITS

        self.name = ft.TextField(label="Name (optional)", value=values.get("name") or "", border=field_border(),
                                 prefix_icon=ft.Icons.BADGE_OUTLINED, on_change=self._changed)
        self.age = LabeledSlider("Age", *limits["age"], values["age"], unit="years", on_change=self._changed)
        self.height = LabeledSlider("Height", *limits["height_cm"], values["height_cm"], unit="cm",
                                    on_change=self._changed)
        self.weight = LabeledSlider("Weight", *limits["weight_kg"], values["weight_kg"], step=0.5, unit="kg",
                                    on_change=self._changed)
        self.sex = ft.SegmentedButton(
            segments=[ft.Segment(value=option, label=option.capitalize()) for option in SEX_OPTIONS],
            selected=[values["sex"]] if values.get("sex") in SEX_OPTIONS else [],
            allow_empty_selection=True, allow_multiple_selection=False, show_selected_icon=False,
        )
        self.level = ft.SegmentedButton(
            segments=[ft.Segment(value=option, label=option.capitalize()) for option in FITNESS_LEVELS],
            selected=[values["fitness_level"]] if values.get("fitness_level") in FITNESS_LEVELS else ["beginner"],
            allow_multiple_selection=False, show_selected_icon=False,
        )
        self.days = LabeledSlider("Training days per week", *limits["days_per_week"], values["days_per_week"],
                                  unit="days")
        self.minutes = LabeledSlider("Minutes per session", *limits["session_duration_min"],
                                     values["session_duration_min"], step=5, unit="min")
        self.goal = ft.TextField(label="Fitness goal", hint_text="e.g. run 5k in 30 min",
                                 value=values.get("fitness_goal") or "", border=field_border(),
                                 prefix_icon=ft.Icons.FLAG_OUTLINED)
        concerns = values.get("health_concerns") or ""
        self.concerns = ft.TextField(label="Health concerns / injuries", hint_text="Leave empty if none",
                                     value="" if concerns.strip().lower() == "none" else concerns,
                                     multiline=True, min_lines=1, max_lines=3, border=field_border(),
                                     prefix_icon=ft.Icons.HEALING)
        owned = set(values.get("equipment") or [])
        self.equipment = {
            item: ft.Checkbox(label=item.capitalize(), value=item in owned, fill_color=Palette.ACCENT,
                              check_color=Palette.ON_ACCENT, on_change=self._changed)
            for item in EQUIPMENT_OPTIONS
        }
        chosen = set(values.get("preferred_activities") or [])
        self.activities = {
            item: ft.Chip(label=item.capitalize(), leading=ft.Icon(activity_icon(item), size=16),
                          selected=item in chosen, on_select=self._changed,
                          selected_color=Palette.ACCENT_SOFT, check_color=Palette.ACCENT, show_checkmark=False)
            for item in ACTIVITY_OPTIONS
        }
        self._notes = ft.Column(spacing=4)
        self._errors = ft.Text(color=Palette.ERROR, size=13, visible=False)
        self._bmi_text = ft.Text(size=20, weight=ft.FontWeight.W_800)
        self._bmi_band = label("", size=11)
        self._maxhr_text = ft.Text(size=20, weight=ft.FontWeight.W_800)
        self._title = heading("", size=22)
        self._update_summary()

        self._form = [
            self._summary_card(is_new),
            section("About you", self.name, self.age.control,
                    ft.Column([label("Sex", size=13, weight=ft.FontWeight.W_500), self.sex], spacing=6),
                    self.height.control, self.weight.control),
            section("Training", ft.Column([label("Fitness level", size=13, weight=ft.FontWeight.W_500), self.level],
                                          spacing=6),
                    self.days.control, self.minutes.control, self.goal, self.concerns),
            section("Equipment you have",
                    label("Bodyweight exercises are always included.", size=12, weight=ft.FontWeight.W_500),
                    ft.ResponsiveRow([ft.Container(box, col={"xs": 6, "sm": 4}) for box in self.equipment.values()],
                                     spacing=0, run_spacing=0)),
            section("Activities you want to do",
                    ft.Row(list(self.activities.values()), wrap=True, spacing=8, run_spacing=8),
                    self._notes),
            self._import_section(),
            self._errors,
            primary_button("Create profile" if is_new else "Save changes", ft.Icons.SAVE_OUTLINED,
                           on_click=self._save, expand=True),
            ft.TextButton("Undo unsaved changes", icon=ft.Icons.UNDO, on_click=self._undo, visible=not is_new),
            self._danger_zone(is_new),
            ft.Container(height=8),
        ]
        self._refresh_notes()

    def _summary_card(self, is_new):
        return panel(ft.Column([
            ft.Row([
                ft.CircleAvatar(content=ft.Icon(ft.Icons.PERSON, color=Palette.ON_ACCENT), bgcolor=Palette.ACCENT,
                                radius=30),
                ft.Column([self._title,
                           label("Set up your profile to get your first session." if is_new
                                 else "Changes apply to your next suggested session.", size=12,
                                 weight=ft.FontWeight.W_500)], spacing=2, expand=True),
            ], spacing=14),
            ft.Divider(color=Palette.BORDER),
            ft.Row([
                ft.Column([self._bmi_text, label("BMI"), self._bmi_band], spacing=0,
                          horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Column([self._maxhr_text, label("Est. max HR (bpm)"), label("220 − age", size=11)], spacing=0,
                          horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            ], alignment=ft.MainAxisAlignment.SPACE_AROUND),
        ], spacing=12), border_color=Palette.ACCENT if is_new else None)

    # ---------------------------------------------------------- Strava import
    def _import_section(self):
        self._render_import()
        return section("Training history", self._import_body,
                       trailing=ft.IconButton(ft.Icons.HELP_OUTLINE, tooltip="Where do I find this file?",
                                              icon_color=Palette.ACCENT,
                                              on_click=lambda e: StravaHelpDialog(self.app).open()))

    def _render_import(self):
        count, first, last = self.service.imported_overview()
        if count:
            status = "{} {} imported from Strava, {} to {}. They appear on the calendar.".format(
                count, "activity" if count == 1 else "activities", first.strftime("%d %b %Y"),
                last.strftime("%d %b %Y"))
        else:
            status = ("Already training on Strava? Upload activities.csv from your Strava archive to see "
                      "your past activities and their stats on the calendar.")
        controls = [ft.Row([ft.Icon(ft.Icons.HISTORY, size=18, color=Palette.IMPORTED),
                            ft.Text(status, size=13, color=Palette.MUTED, expand=True)],
                           vertical_alignment=ft.CrossAxisAlignment.START)]
        if self._importing:
            controls.append(ft.Row([ft.ProgressRing(width=18, height=18, stroke_width=2, color=Palette.ACCENT),
                                    label("Reading your activities...", size=12, weight=ft.FontWeight.W_500)],
                                   spacing=10))
        else:
            self.upload_button = ft.OutlinedButton(
                "Upload Strava activities.csv", icon=ft.Icons.UPLOAD_FILE,
                action=ft.PickFiles(self.app.file_picker, dialog_title="Choose activities.csv",
                                    file_type=ft.FilePickerFileType.CUSTOM, allowed_extensions=["csv"],
                                    with_data=True, cancel_upload_on_window_blur=False),
                style=ft.ButtonStyle(color=Palette.ACCENT, side=ft.BorderSide(1, Palette.ACCENT)))
            controls.append(self.upload_button)
        if self._last_import is not None:
            summary = self._last_import
            controls.append(ft.Text(summary.message(), size=12,
                                    color=Palette.ERROR if not summary.ok else Palette.TEXT))
            for row, reason in summary.skipped[:3]:
                controls.append(label("Row {}: {}".format(row, reason), size=11, weight=ft.FontWeight.W_500))
        if count and not self._importing:
            controls.append(ft.TextButton("Remove imported activities", icon=ft.Icons.DELETE_SWEEP_OUTLINED,
                                          on_click=self._confirm_clear,
                                          style=ft.ButtonStyle(color=Palette.MUTED)))
        self._import_body.controls = controls

    async def handle_picked_files(self, e):
        """FilePicker.on_result: the browser has read the chosen file and sent its bytes."""
        files = list(getattr(e, "files", None) or [])
        if not files:
            return                                    # the user closed the file dialog
        chosen = files[0]
        if not str(chosen.name or "").lower().endswith(".csv"):
            self.app.notify("Choose the activities.csv file from inside the archive, not the .zip itself.",
                            error=True)
            return
        if chosen.bytes is None:
            self.app.notify("The browser could not read that file. Please try again.", error=True)
            return
        self._importing = True
        self._render_import()
        self.app.page.update()
        try:
            summary = await asyncio.to_thread(self.service.import_history, chosen.bytes, "strava")
        finally:
            self._importing = False
        self._last_import = summary
        self._render_import()
        self.app.notify(summary.message(), error=not summary.ok)

    def _confirm_clear(self, e):
        def clear():
            if self.service.clear_imported():
                self._last_import = None
                self._render_import()
                self.app.notify("Imported activities removed. Your logged sessions are kept.")
            else:
                self.app.notify("Could not update your data file. Check file permissions.", error=True)
        ConfirmDialog(self.app, "Remove imported activities?",
                      "This removes every activity imported from Strava, and the coach feedback on them. "
                      "Sessions you logged in AegisFit are kept. You can import the file again later.",
                      "Remove", clear).open()

    def _danger_zone(self, is_new):
        if is_new:
            return ft.Container()
        return section("Danger zone",
                       ft.Text("Delete your profile and every logged session.", color=Palette.MUTED, size=13),
                       ft.OutlinedButton("Reset all data", icon=ft.Icons.DELETE_FOREVER_OUTLINED, on_click=self._reset,
                                         style=ft.ButtonStyle(color=Palette.ERROR,
                                                              side=ft.BorderSide(1, Palette.ERROR))))

    # ----------------------------------------------------------------- reading
    def current_profile(self):
        """The profile as currently shown in the form."""
        return {
            "name": (self.name.value or "").strip(),
            "age": self.age.value,
            "sex": self.sex.selected[0] if self.sex.selected else None,
            "height_cm": float(self.height.value),
            "weight_kg": float(self.weight.value),
            "fitness_level": self.level.selected[0] if self.level.selected else None,
            "health_concerns": (self.concerns.value or "").strip() or "none",
            "fitness_goal": (self.goal.value or "").strip(),
            "days_per_week": self.days.value,
            "session_duration_min": self.minutes.value,
            "equipment": [item for item, box in self.equipment.items() if box.value],
            "preferred_activities": [item for item, chip in self.activities.items() if chip.selected],
        }

    # ------------------------------------------------------------------ events
    def _changed(self, *_):
        self._update_summary()
        self._refresh_notes()

    def _update_summary(self):
        name = (self.name.value or "").strip()
        self._title.value = name or "Your profile"
        value = bmi(self.height.value, self.weight.value)
        self._bmi_text.value = "-" if value is None else "{:.1f}".format(value)
        self._bmi_band.value = bmi_band(value)
        self._maxhr_text.value = str(220 - self.age.value)

    def _refresh_notes(self):
        notes = self.service.equipment_notes(self.current_profile())
        self._notes.controls = [ft.Row([ft.Icon(ft.Icons.INFO_OUTLINE, size=16, color=Palette.PAIN),
                                        ft.Text(note, size=12, color=Palette.PAIN, expand=True)])
                                for note in notes]

    def _save(self, e):
        was_new = not self.service.has_profile
        errors = self.service.save_profile(self.current_profile())
        if errors:
            self._errors.value = "\n".join("• " + error[0].upper() + error[1:] for error in errors)
            self._errors.visible = True
            return
        self._errors.visible = False
        self.discard_edits()
        if was_new:
            self.app.notify("Profile created! Let's plan your first session.", action="Plan it",
                            on_action=lambda ev: self.app.page.run_task(self._plan_first))
            self.app.go(self.app.HOME)
        else:
            self.app.notify("Profile saved.")
            self.refresh()

    async def _plan_first(self):
        self.app.go(self.app.HOME)
        await self.app.views[self.app.HOME].start_suggestion()

    def _undo(self, e):
        self.discard_edits()
        self.refresh()

    def _reset(self, e):
        def do_reset():
            if self.service.reset():
                self.discard_edits()
                self.app.notify("All data deleted.")
                self.app.go(self.app.PROFILE)
            else:
                self.app.notify("Could not delete all data files. Check file permissions.", error=True)
        ResetDialog(self.app, do_reset).open()
