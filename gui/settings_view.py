"""Settings tab: Claude API key and model, colour theme, display preferences, about."""
import asyncio
import os
from dataclasses import replace

import flet as ft

from ai_manager import MODEL_CHOICES
from coach_service import APP_NAME, APP_VERSION
from gui.base_view import AppView
from gui.dialogs import ConfirmDialog
from gui.theme import Palette
from gui.widgets import detail_rows, field_border, heading, label, panel, primary_button, secondary_button, section
from settings_manager import (
    DEFAULT_THEME, MIN_ACCENT_CONTRAST, MIN_TEXT_CONTRAST, THEME_PRESETS, ThemeColors, clean_api_key,
    contrast_ratio, mix, normalize_hex, readable_on, timezone_choices,
)

AUTO_ZONE = "(server clock)"      # dropdown key for "no time zone chosen"

HEX_CHARACTERS = ft.InputFilter(regex_string=r"[#0-9a-fA-F]", allow=True)
MODEL_HINTS = {model_id: hint for model_id, _, hint in MODEL_CHOICES}


def sentence(text):
    """'could not connect' -> 'Could not connect.'"""
    text = str(text or "").strip()
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    return text if text[-1] in ".!?" else text + "."


class SettingsView(AppView):
    label = "Settings"
    icon = ft.Icons.SETTINGS_OUTLINED
    selected_icon = ft.Icons.SETTINGS

    def __init__(self, app):
        super().__init__(app)
        self._form = None          # kept between tab switches, so a half-typed colour is not lost
        self._testing = False

    def build(self):
        if self._form is None:
            self._build_form()
        return self._form

    def restyle(self):
        self._form = None          # rebuilt from the saved settings in the new colours

    # ------------------------------------------------------------------ layout
    def _build_form(self):
        current = self.service.settings
        self._form = [
            ft.Column([heading("Settings", size=26), label("{} {}".format(APP_NAME, APP_VERSION), size=12)],
                      spacing=2),
            self._ai_section(current),
            self._appearance_section(current.theme),
            self._preferences_section(current),
            self._about_section(),
            ft.Container(height=8),
        ]
        self._refresh_key_status()
        self._colours_changed()

    # ---- AI coach ----
    def _ai_section(self, current):
        self._key_status = ft.Row(spacing=8, vertical_alignment=ft.CrossAxisAlignment.START)
        self._key_field = ft.TextField(
            label="Claude API key", hint_text="sk-ant-...", password=True, can_reveal_password=True,
            autocorrect=False, enable_suggestions=False, border=field_border(), prefix_icon=ft.Icons.KEY,
            on_change=self._key_typed, on_submit=self._save_ai,
        )
        self._model = ft.SegmentedButton(
            segments=[ft.Segment(value=model_id, label=name) for model_id, name, _ in MODEL_CHOICES],
            selected=[current.model], allow_multiple_selection=False, allow_empty_selection=False,
            show_selected_icon=False, on_change=self._model_changed,
        )
        self._model_hint = label(MODEL_HINTS.get(current.model, ""), size=12, weight=ft.FontWeight.W_500)
        self._ai_message = ft.Text(size=12, visible=False)
        self._test_button = secondary_button("Test connection", ft.Icons.WIFI_TETHERING, on_click=self._test,
                                             expand=True)
        self._remove_key = ft.TextButton(
            "Remove saved key", icon=ft.Icons.KEY_OFF, on_click=self._remove_key_click,
            visible=bool(current.api_key), style=ft.ButtonStyle(color=Palette.ERROR),
        )
        where = os.path.basename(str(self.service.settings_location)) or "the settings file"
        return section(
            "AI coach",
            self._key_status,
            self._key_field,
            label("Saved in {} on this computer and never shown in full again. Create a key at "
                  "console.anthropic.com.".format(where), size=12, weight=ft.FontWeight.W_500),
            ft.Column([label("Model", size=13, weight=ft.FontWeight.W_500), self._model, self._model_hint],
                      spacing=6),
            self._ai_message,
            ft.Row([self._test_button,
                    primary_button("Save", ft.Icons.SAVE_OUTLINED, on_click=self._save_ai, expand=True)],
                   spacing=12),
            self._remove_key,
        )

    def _refresh_key_status(self):
        settings = self.service.settings
        source = self.service.api_key_source()
        if source == "settings":
            icon, color, text = ft.Icons.CHECK_CIRCLE, Palette.DONE, "Using the key saved here ({}).".format(
                settings.masked_key)
        elif source == "environment" and self.service.environment_key_problem():
            icon, color, text = (ft.Icons.ERROR_OUTLINE, Palette.PAIN,
                                 "ANTHROPIC_API_KEY is set but looks wrong ({}). Paste your key below "
                                 "instead.".format(self.service.environment_key_problem()))
        elif source == "environment":
            icon, color, text = ft.Icons.CHECK_CIRCLE, Palette.DONE, "Using ANTHROPIC_API_KEY from the environment."
        else:
            icon, color, text = (ft.Icons.ERROR_OUTLINE, Palette.PAIN,
                                 "No API key yet. The coach can't plan sessions until you add one.")
        self._key_status.controls = [ft.Icon(icon, color=color, size=18),
                                     ft.Text(text, size=13, color=color, expand=True)]
        self._remove_key.visible = bool(settings.api_key)
        self._refresh_about()

    def _show_ai_message(self, text, color):
        self._ai_message.value = text
        self._ai_message.color = color
        self._ai_message.visible = bool(text)

    def _selected_model(self):
        return self._model.selected[0] if self._model.selected else self.service.settings.model

    def _key_typed(self, e):
        self._show_ai_message("", Palette.MUTED)

    def _model_changed(self, e):
        self._model_hint.value = MODEL_HINTS.get(self._selected_model(), "")

    def _save_ai(self, e):
        current = self.service.settings
        typed = clean_api_key(self._key_field.value)
        new = replace(current, api_key=typed or current.api_key, model=self._selected_model())
        if new == current:
            self._show_ai_message("Nothing to save: paste a new key or pick another model.", Palette.MUTED)
            return
        errors = self.service.save_settings(new)
        if errors:
            self._show_ai_message("\n".join(sentence(error) for error in errors), Palette.ERROR)
            return
        self._key_field.value = ""
        self._refresh_key_status()
        self._show_ai_message("Saved. Use Test connection to check the key works.", Palette.DONE)
        self.app.notify("AI settings saved.")

    async def _test(self, e):
        if self._testing:
            return
        self._testing = True
        self._test_button.disabled = True
        self._show_ai_message("Checking with Anthropic...", Palette.MUTED)
        self.app.page.update()
        typed = clean_api_key(self._key_field.value) or None
        try:
            ok, message = await asyncio.to_thread(self.service.test_ai_connection, typed, self._selected_model())
        finally:
            self._testing = False
            self._test_button.disabled = False
        text = sentence(message)
        if ok and typed:
            text += " Press Save to keep this key."
        self._show_ai_message(text, Palette.DONE if ok else Palette.ERROR)
        self.app.page.update()

    def _remove_key_click(self, e):
        def remove():
            errors = self.service.save_settings(replace(self.service.settings, api_key=""))
            if errors:
                self.app.notify(sentence(errors[0]), error=True)
                return
            self._refresh_key_status()
            self._show_ai_message("", Palette.MUTED)
            self.app.notify("Saved API key removed.")
            self.app.refresh_current()
        ConfirmDialog(self.app, "Remove the saved API key?",
                      "The coach will use ANTHROPIC_API_KEY if it is set. Otherwise AI planning stops "
                      "until you add a key.", "Remove key", remove).open()

    # ---- appearance ----
    def _appearance_section(self, theme):
        self._preset_chips = {
            name: ft.Chip(
                label=name, show_checkmark=False, selected=preset == theme,
                leading=ft.Container(width=14, height=14, border_radius=7, bgcolor=preset.accent),
                selected_color=Palette.ACCENT_SOFT, on_select=lambda e, name=name: self._use_preset(name),
            )
            for name, preset in THEME_PRESETS.items()
        }
        self._colour_fields, self._swatches = {}, {}
        cells = []
        for name in ThemeColors.FIELDS:
            value = getattr(theme, name)
            self._swatches[name] = ft.Container(width=40, height=40, border_radius=12, bgcolor=value,
                                                border=ft.Border.all(1, Palette.BORDER))
            self._colour_fields[name] = ft.TextField(
                label=ThemeColors.LABELS[name], value=value, input_filter=HEX_CHARACTERS, dense=True,
                capitalization=ft.TextCapitalization.CHARACTERS, border=field_border(), expand=True,
                on_change=self._colours_changed, on_submit=self._apply_colours,
            )
            cells.append(ft.Container(ft.Row([self._swatches[name], self._colour_fields[name]], spacing=10),
                                      col={"xs": 12, "sm": 6}))
        self._preview = ft.Container()
        self._contrast = ft.Column(spacing=4)
        return section(
            "Appearance",
            ft.Column([label("Presets", size=13, weight=ft.FontWeight.W_500),
                       ft.Row(list(self._preset_chips.values()), wrap=True, spacing=8, run_spacing=8)], spacing=6),
            label("Or type your own RGB hex colours, e.g. #A3E635.", size=12, weight=ft.FontWeight.W_500),
            ft.ResponsiveRow(cells, spacing=10, run_spacing=12),
            label("Preview", size=13, weight=ft.FontWeight.W_500),
            self._preview,
            self._contrast,
            ft.Row([secondary_button("Default", ft.Icons.RESTART_ALT, on_click=self._reset_colours, expand=True),
                    primary_button("Apply colours", ft.Icons.PALETTE_OUTLINED, on_click=self._apply_colours,
                                   expand=True)], spacing=12),
        )

    def _typed_colours(self):
        return {name: normalize_hex(field.value) for name, field in self._colour_fields.items()}

    def _colours_changed(self, e=None):
        values = self._typed_colours()
        for name, value in values.items():
            self._swatches[name].bgcolor = value       # None (empty swatch) while the hex is incomplete
            if value:
                self._colour_fields[name].error = None
        complete = all(values.values())
        theme = ThemeColors(**values) if complete else None
        for name, chip in self._preset_chips.items():
            chip.selected = theme == THEME_PRESETS[name]
        self._preview.content = self._preview_card(theme) if theme else panel(
            label("Finish typing the colours to see a preview.", size=12), bgcolor=Palette.CARD_HIGH)
        self._contrast.controls = self._contrast_rows(theme) if theme else []

    def _preview_card(self, theme):
        """A small mock screen drawn in the typed colours (not the applied ones)."""
        muted = mix(theme.text, theme.background, 0.40)
        track = mix(theme.card, theme.text, 0.15)
        return ft.Container(
            bgcolor=theme.background, border_radius=16, padding=14, border=ft.Border.all(1, Palette.BORDER),
            content=ft.Container(bgcolor=theme.card, border_radius=14, padding=14, content=ft.Column([
                ft.Row([
                    ft.Container(ft.Icon(ft.Icons.DIRECTIONS_RUN, color=theme.accent, size=20), width=36, height=36,
                                 border_radius=18, alignment=ft.Alignment.CENTER,
                                 bgcolor=mix(theme.background, theme.accent, 0.20)),
                    ft.Column([ft.Text("Easy run", color=theme.text, size=15, weight=ft.FontWeight.W_700),
                               ft.Text("30 min · moderate intensity", color=muted, size=12)], spacing=0, expand=True),
                ], spacing=10),
                ft.Stack([ft.Container(height=6, border_radius=3, bgcolor=track),
                          ft.Container(height=6, width=150, border_radius=3, bgcolor=theme.accent)]),
                ft.Container(ft.Text("Plan my next session", color=readable_on(theme.accent), size=13,
                                     weight=ft.FontWeight.W_700),
                             bgcolor=theme.accent, border_radius=20, alignment=ft.Alignment.CENTER,
                             padding=ft.Padding.symmetric(horizontal=16, vertical=10)),
            ], spacing=12)),
        )

    @staticmethod
    def _contrast_rows(theme):
        text = min(contrast_ratio(theme.text, theme.background), contrast_ratio(theme.text, theme.card))
        accent = min(contrast_ratio(theme.accent, theme.background), contrast_ratio(theme.accent, theme.card))
        rows = []
        for name, ratio, needed in (("Text", text, MIN_TEXT_CONTRAST), ("Accent", accent, MIN_ACCENT_CONTRAST)):
            ok = ratio >= needed
            color = Palette.DONE if ok else Palette.ERROR
            rows.append(ft.Row([
                ft.Icon(ft.Icons.CHECK_CIRCLE if ok else ft.Icons.ERROR_OUTLINE, size=16, color=color),
                ft.Text("{} contrast {:.1f}:1 {}".format(name, ratio, "(good)" if ok else
                                                         "(needs at least {}:1)".format(needed)),
                        size=12, color=color),
            ], spacing=6))
        return rows

    def _use_preset(self, name):
        self._fill(THEME_PRESETS[name])

    def _fill(self, theme):
        for name, field in self._colour_fields.items():
            field.value = getattr(theme, name)
        self._colours_changed()

    def _reset_colours(self, e):
        self._fill(DEFAULT_THEME)
        self._apply_colours(e)

    def _apply_colours(self, e):
        values = self._typed_colours()
        missing = [name for name, value in values.items() if value is None]
        for name in missing:
            self._colour_fields[name].error = "Use a hex colour like #A3E635"
        if missing:
            self.app.notify("Some colours are not valid hex codes.", error=True)
            return
        theme = ThemeColors(**values)
        if theme == self.service.settings.theme:
            self.app.notify("Those colours are already in use.")
            return
        errors = self.service.save_settings(replace(self.service.settings, theme=theme))
        if errors:
            self.app.notify(sentence(errors[0]), error=True)
            return
        self.app.apply_appearance()       # restyles every screen (this one included) and redraws
        self.app.notify("Colours applied.")

    # ---- preferences ----
    def _preferences_section(self, current):
        self._week = ft.SegmentedButton(
            segments=[ft.Segment(value="monday", label="Monday"), ft.Segment(value="sunday", label="Sunday")],
            selected=[current.week_starts_on], allow_multiple_selection=False, allow_empty_selection=False,
            show_selected_icon=False, on_change=self._preferences_changed,
        )
        self._splash = self._switch("Show the splash screen at start-up", current.show_splash)
        self._animations = self._switch("Animations and screen transitions", current.animations)
        zones = [ft.DropdownOption(key=AUTO_ZONE, text="Server clock (UTC in Docker)")]
        zones += [ft.DropdownOption(key=name, text=name) for name in timezone_choices()]
        self._zone = ft.Dropdown(
            label="Time zone", value=current.timezone or AUTO_ZONE, options=zones, editable=True,
            enable_filter=True, border=field_border(), on_select=self._zone_changed, expand=True,
        )
        self._zone_note = label("", size=12, weight=ft.FontWeight.W_500)
        self._refresh_zone_note()
        return section(
            "Preferences",
            ft.Column([label("Time zone", size=13, weight=ft.FontWeight.W_500), self._zone, self._zone_note],
                      spacing=6),
            ft.Column([label("Calendar weeks start on", size=13, weight=ft.FontWeight.W_500), self._week],
                      spacing=6),
            self._splash,
            self._animations,
        )

    def _refresh_zone_note(self):
        name = self.service.timezone_name
        text = "Today is {}".format(self.service.today.strftime("%A %d %B"))
        if name:
            text += " in {}.".format(name)
        else:
            text += " by the clock of the computer running AegisFit. If that is a server or Docker container " \
                    "it is probably on UTC, so pick your own time zone to get your own date."
        self._zone_note.value = text

    def _zone_changed(self, e):
        chosen = self._zone.value
        name = "" if chosen in (None, "", AUTO_ZONE) else chosen
        errors = self.service.save_settings(replace(self.service.settings, timezone=name))
        if errors:
            self._zone.value = self.service.settings.timezone or AUTO_ZONE
            self.app.notify(sentence(errors[0]), error=True)
        else:
            self._refresh_zone_note()
            self._refresh_about()
            self.app.views[self.app.CALENDAR].show_today()
            self.app.notify("Time zone saved.")
        self.app.page.update()

    def _switch(self, text, value):
        return ft.Switch(label=text, value=value, active_color=Palette.ON_ACCENT, active_track_color=Palette.ACCENT,
                         on_change=self._preferences_changed)

    def _preferences_changed(self, e):
        current = self.service.settings
        new = replace(current,
                      week_starts_on=self._week.selected[0] if self._week.selected else current.week_starts_on,
                      show_splash=bool(self._splash.value), animations=bool(self._animations.value))
        errors = self.service.save_settings(new)
        if errors:
            self._week.selected = [current.week_starts_on]
            self._splash.value, self._animations.value = current.show_splash, current.animations
            self.app.notify(sentence(errors[0]), error=True)
            return
        self.app.apply_preferences()

    # ---- about ----
    def _about_section(self):
        self._about = ft.Container()
        return section(
            "About",
            self._about,
            label("In Docker these are paths inside the container; they map to the folder you mount with -v.",
                  size=11, weight=ft.FontWeight.W_500),
        )

    def _refresh_about(self):
        source = {"settings": "saved in Settings", "environment": "ANTHROPIC_API_KEY"}.get(
            self.service.api_key_source(), "none")
        self._about.content = detail_rows([
            ("Version", "{} {}".format(APP_NAME, APP_VERSION)),
            ("Today", "{} ({})".format(self.service.today.strftime("%a %d %b %Y"),
                                       self.service.timezone_name or "server clock")),
            ("AI model", self.service.ai_model),
            ("API key", source),
            ("Data file", self.service.data_location),
            ("Settings", self.service.settings_location),
        ])
