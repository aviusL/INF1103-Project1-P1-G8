"""Colours, fonts and icons used across the GUI, kept in one place so screens stay consistent.

The user picks four colours in Settings (accent, background, cards, text; see
settings_manager.ThemeColors). Palette.apply() derives every other shade from them, so any
theme that passes the contrast checks stays readable. Screens read Palette.* when they build
their controls, which is why the shell rebuilds every screen after the theme changes.
"""
import os

import flet as ft

from settings_manager import DEFAULT_THEME, mix, readable_on

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
HEADING_FONT = "Montserrat"
FONTS = {HEADING_FONT: "fonts/Montserrat-ExtraBoldItalic.ttf"}   # path is relative to ASSETS_DIR

# Status colours keep their meaning whatever the theme, with a darker set for light backgrounds.
_STATUS_DARK = {"DONE": "#2DD4BF", "PAIN": "#F5B83D", "PLANNED": "#60A5FA", "IMPORTED": "#C084FC", "ERROR": "#FF6B6B"}
_STATUS_LIGHT = {"DONE": "#0F766E", "PAIN": "#B45309", "PLANNED": "#1D4ED8", "IMPORTED": "#7E22CE", "ERROR": "#C62828"}


class Palette:
    """The colours currently in use. Change them only through Palette.apply(theme)."""

    ACCENT = ACCENT_SOFT = ON_ACCENT = ""
    BG = SURFACE = CARD = CARD_HIGH = BORDER = ""
    TEXT = MUTED = ""
    DONE = PAIN = PLANNED = IMPORTED = ERROR = ON_ERROR = ""
    IS_DARK = True

    @classmethod
    def apply(cls, theme):
        """Use a settings_manager.ThemeColors. Assumes it passed ThemeColors.problems()."""
        cls.ACCENT, cls.BG, cls.CARD, cls.TEXT = theme.accent, theme.background, theme.card, theme.text
        cls.IS_DARK = theme.is_dark
        cls.SURFACE = mix(theme.background, theme.card, 0.55)       # nav bar, dialogs
        cls.CARD_HIGH = mix(theme.card, theme.text, 0.08)           # highlighted rows, snackbars
        cls.BORDER = mix(theme.card, theme.text, 0.15)              # dividers, outlines, empty bars
        cls.MUTED = mix(theme.text, theme.background, 0.40)         # captions and secondary text
        cls.ACCENT_SOFT = mix(theme.background, theme.accent, 0.20)  # tinted badges, nav indicator
        cls.ON_ACCENT = readable_on(theme.accent)                   # text and icons on accent
        for name, value in (_STATUS_DARK if cls.IS_DARK else _STATUS_LIGHT).items():
            setattr(cls, name, value)
        cls.ON_ERROR = readable_on(cls.ERROR)
        # dicts other modules imported by reference: update in place
        STATUS_COLORS.update({"done": cls.DONE, "pain": cls.PAIN, "planned": cls.PLANNED, "imported": cls.IMPORTED})
        INTENSITY_COLORS.update({"low": cls.DONE, "moderate": cls.PAIN, "high": cls.ERROR})


# calendar marker colour for each CalendarEvent.status
STATUS_COLORS = {}
STATUS_LABELS = {"done": "Completed", "pain": "Pain reported", "planned": "Planned", "imported": "Imported"}
INTENSITY_COLORS = {}

Palette.apply(DEFAULT_THEME)

ACTIVITY_ICONS = {
    "running": ft.Icons.DIRECTIONS_RUN,
    "cycling": ft.Icons.DIRECTIONS_BIKE,
    "swimming": ft.Icons.POOL,
    "walking": ft.Icons.DIRECTIONS_WALK,
    "strength training": ft.Icons.FITNESS_CENTER,
    "hiit": ft.Icons.LOCAL_FIRE_DEPARTMENT,
    "rowing": ft.Icons.ROWING,
    "yoga": ft.Icons.SELF_IMPROVEMENT,
    "workout": ft.Icons.FITNESS_CENTER,
}


def activity_icon(activity):
    return ACTIVITY_ICONS.get(str(activity or "").strip().lower(), ft.Icons.SPORTS_GYMNASTICS)


def on_color(background):
    """Black or white text for a filled shape of the given colour."""
    return readable_on(background)


def theme_mode():
    return ft.ThemeMode.DARK if Palette.IS_DARK else ft.ThemeMode.LIGHT


def build_theme():
    """A Flet theme from the current Palette (used for both page.theme and page.dark_theme)."""
    return ft.Theme(
        color_scheme_seed=Palette.ACCENT,
        color_scheme=ft.ColorScheme(
            primary=Palette.ACCENT,
            on_primary=Palette.ON_ACCENT,
            primary_container=Palette.ACCENT_SOFT,
            on_primary_container=Palette.TEXT,
            secondary=Palette.ACCENT,
            on_secondary=Palette.ON_ACCENT,
            secondary_container=Palette.ACCENT_SOFT,     # selected segments, nav indicator
            on_secondary_container=Palette.TEXT,
            surface=Palette.SURFACE,
            on_surface=Palette.TEXT,
            on_surface_variant=Palette.MUTED,
            surface_container_lowest=Palette.BG,
            surface_container_low=Palette.SURFACE,
            surface_container=Palette.CARD,
            surface_container_high=Palette.CARD_HIGH,
            surface_container_highest=Palette.CARD_HIGH,
            outline=Palette.BORDER,
            outline_variant=Palette.BORDER,
            error=Palette.ERROR,
            on_error=Palette.ON_ERROR,
            inverse_primary=Palette.ACCENT,               # snackbar action buttons
        ),
        scaffold_bgcolor=Palette.BG,
        use_material3=True,
    )
