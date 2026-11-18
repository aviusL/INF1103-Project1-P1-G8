"""Small reusable building blocks for the screens.

Colour arguments default to None and are looked up when the control is built, never as
Python default values: those would freeze the colours of the theme active at import time.
"""
import flet as ft

from gui.theme import HEADING_FONT, INTENSITY_COLORS, Palette, activity_icon, on_color


def field_border():
    """Rounded outline for text fields (a new object per field)."""
    return ft.OutlineInputBorder(border_radius=12)


def heading(text, size=22, color=None):
    """Bold italic display heading."""
    return ft.Text(text, size=size, font_family=HEADING_FONT, color=color or Palette.TEXT,
                   weight=ft.FontWeight.W_800)


def label(text, size=12, color=None, weight=ft.FontWeight.W_600):
    return ft.Text(text, size=size, color=color or Palette.MUTED, weight=weight)


def panel(content, padding=16, bgcolor=None, on_click=None, border_color=None):
    """Rounded card used for every section."""
    return ft.Container(
        content=content,
        padding=padding,
        bgcolor=bgcolor or Palette.CARD,
        border_radius=18,
        border=ft.Border.all(1, border_color) if border_color else None,
        on_click=on_click,
    )


def section(title, *controls, trailing=None):
    """A titled panel."""
    header = [label(title.upper(), size=11)]
    if trailing is not None:
        header = [ft.Row([header[0], trailing], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)]
    return panel(ft.Column(header + list(controls), spacing=12))


def pill(text, color=None, filled=False):
    color = color or Palette.ACCENT
    return ft.Container(
        content=ft.Text(text, size=11, weight=ft.FontWeight.W_700,
                        color=on_color(color) if filled else color),
        bgcolor=color if filled else ft.Colors.with_opacity(0.15, color),
        padding=ft.Padding.symmetric(horizontal=10, vertical=4),
        border_radius=20,
    )


def stat(value, caption, icon=None, color=None):
    """Big number with a caption, e.g. '32 min' / 'Duration'."""
    top = [ft.Text(str(value), size=20, weight=ft.FontWeight.W_800, color=color or Palette.TEXT)]
    if icon:
        top.insert(0, ft.Icon(icon, size=16, color=Palette.MUTED))
    return ft.Column([ft.Row(top, spacing=6), label(caption, size=11)], spacing=2, tight=True)


def activity_badge(activity, size=44):
    return ft.Container(
        content=ft.Icon(activity_icon(activity), color=Palette.ACCENT, size=size * 0.55),
        width=size, height=size, border_radius=size / 2,
        bgcolor=Palette.ACCENT_SOFT, alignment=ft.Alignment.CENTER,
    )


def intensity_pill(intensity):
    return pill(str(intensity or "-").capitalize(), INTENSITY_COLORS.get(intensity, Palette.MUTED))


def detail_rows(pairs):
    """(label, value) pairs as a two-column list."""
    return ft.Column([
        ft.Row([
            ft.Container(label(name, size=12), width=96),
            ft.Text(str(value), size=13, color=Palette.TEXT, expand=True),
        ], vertical_alignment=ft.CrossAxisAlignment.START)
        for name, value in pairs
    ], spacing=8)


def session_plan_column(plan):
    """Warm-up, main set and cool-down of a planned session, with the coach's reasoning."""
    blocks = []
    for title, key, icon in (("Warm-up", "warmup", ft.Icons.WB_SUNNY_OUTLINED),
                             ("Main set", "main_set", ft.Icons.BOLT),
                             ("Cool-down", "cooldown", ft.Icons.AC_UNIT),
                             ("Why this session", "rationale", ft.Icons.LIGHTBULB_OUTLINE)):
        if plan.get(key):
            blocks.append(ft.Row([
                ft.Icon(icon, size=18, color=Palette.ACCENT),
                ft.Column([label(title, size=11), ft.Text(plan[key], size=13, color=Palette.TEXT)],
                          spacing=2, expand=True),
            ], vertical_alignment=ft.CrossAxisAlignment.START))
    return ft.Column(blocks, spacing=12)


def session_stats_row(plan):
    pace = plan.get("target_pace_min_per_km")
    return ft.Row([
        stat("{} min".format(plan.get("duration_min", "?")), "Duration", ft.Icons.TIMER_OUTLINED),
        stat("{}".format(plan.get("target_hr_bpm", "-")), "Target bpm", ft.Icons.MONITOR_HEART_OUTLINED),
        stat("-" if pace is None else "{}".format(pace), "Pace min/km", ft.Icons.SPEED),
    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)


def primary_button(text, icon=None, on_click=None, expand=False, disabled=False):
    return ft.FilledButton(
        content=text, icon=icon, on_click=on_click, expand=expand, disabled=disabled, height=48,
        style=ft.ButtonStyle(bgcolor=Palette.ACCENT, color=Palette.ON_ACCENT,
                             shape=ft.RoundedRectangleBorder(radius=24)),
    )


def secondary_button(text, icon=None, on_click=None, expand=False):
    return ft.OutlinedButton(
        content=text, icon=icon, on_click=on_click, expand=expand, height=48,
        style=ft.ButtonStyle(color=Palette.TEXT, shape=ft.RoundedRectangleBorder(radius=24),
                             side=ft.BorderSide(1, Palette.BORDER)),
    )


class LabeledSlider:
    """A slider with its title and live value, e.g. 'Weight   72.5 kg'."""

    def __init__(self, title, low, high, value, step=1, unit="", on_change=None, describe=None):
        self._unit = unit
        self._step = step
        self._describe = describe
        self._on_change = on_change
        self._value_text = ft.Text(weight=ft.FontWeight.W_700, color=Palette.TEXT)
        self.slider = ft.Slider(
            min=low, max=high, value=self._clamp(value, low, high),
            divisions=max(1, int(round((high - low) / step))),
            active_color=Palette.ACCENT, inactive_color=Palette.BORDER,
            on_change=self._changed,
        )
        self._refresh_text()
        self.control = ft.Column([
            ft.Row([label(title, size=13, color=Palette.MUTED, weight=ft.FontWeight.W_500), self._value_text],
                   alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            self.slider,
        ], spacing=0)

    @staticmethod
    def _clamp(value, low, high):
        try:
            return min(max(float(value), low), high)
        except (TypeError, ValueError):
            return low

    @property
    def value(self):
        raw = round(self.slider.value / self._step) * self._step
        return int(raw) if float(self._step).is_integer() else round(raw, 1)

    @value.setter
    def value(self, new_value):
        self.slider.value = self._clamp(new_value, self.slider.min, self.slider.max)
        self._refresh_text()

    def _refresh_text(self):
        text = "{} {}".format(self.value, self._unit).strip()
        if self._describe:
            text += "  ·  " + self._describe(self.value)
        self._value_text.value = text

    def _changed(self, e):
        self._refresh_text()
        if self._on_change:
            self._on_change(self.value)
