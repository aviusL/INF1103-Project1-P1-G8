"""The AegisFit application shell: splash screen, bottom navigation and shared helpers."""
import asyncio
import datetime
import logging

import flet as ft

from coach_service import APP_NAME, APP_TAGLINE, CoachService, LoadStatus
from gui.calendar_view import CalendarView
from gui.dialogs import DayPickerDialog
from gui.home_view import HomeView
from gui.profile_view import ProfileView
from gui.progress_view import ProgressView
from gui.settings_view import SettingsView
from gui.theme import ASSETS_DIR, FONTS, HEADING_FONT, Palette, build_theme, theme_mode

logger = logging.getLogger("gui")

SPLASH_MIN_SECONDS = 1.6
CONTENT_MAX_WIDTH = 640
SWITCH_MS, SWITCH_REVERSE_MS = 220, 120     # tab fade, when animations are on


class CoachGUI:
    """One instance per browser tab / desktop window. Owns its views (composition)."""

    HOME, CALENDAR, PROGRESS, PROFILE, SETTINGS = range(5)

    def __init__(self, page, service):
        self.page = page
        self.service = service
        Palette.apply(service.settings.theme)     # before any control is built
        self.views = [HomeView(self), CalendarView(self), ProgressView(self), ProfileView(self), SettingsView(self)]
        # One file picker per page (a Flet service). Kept here so it is not dropped as unused; the
        # Profile tab's upload button opens it inside the click, as browsers require.
        self.file_picker = ft.FilePicker(on_result=self._on_files_picked)
        self._index = self.HOME
        self._body = ft.AnimatedSwitcher(
            content=ft.Container(), transition=ft.AnimatedSwitcherTransition.FADE, expand=True,
        )
        self._nav = ft.NavigationBar(
            destinations=[ft.NavigationBarDestination(icon=v.icon, selected_icon=v.selected_icon, label=v.label)
                          for v in self.views],
            selected_index=self._index,
            label_behavior=ft.NavigationBarLabelBehavior.ALWAYS_SHOW,
            on_change=self._on_nav_change,
        )
        self.apply_preferences(update=False)

    @property
    def animations(self):
        return self.service.settings.animations

    # ---------------------------------------------------------------- start-up
    def start(self):
        page = self.page
        page.title = APP_NAME
        page.fonts = FONTS
        page.padding = 0
        self._style_page()
        if not page.web:
            page.window.width = 460
            page.window.height = 920
            page.window.min_width = 380
        page.add(self._splash())
        page.update()
        page.run_task(self._boot)

    def _style_page(self):
        """Push the current Palette to the page-level theme."""
        self.page.theme = build_theme()
        self.page.dark_theme = build_theme()
        self.page.theme_mode = theme_mode()
        self.page.bgcolor = Palette.BG
        self._nav.bgcolor = Palette.SURFACE
        self._nav.indicator_color = Palette.ACCENT_SOFT

    def _logo(self, size=120):
        """AegisFit mark: a shield (the safety rules) around a runner."""
        return ft.Stack([
            ft.Icon(ft.Icons.SHIELD, size=size * 0.62, color=Palette.ON_ACCENT),
            ft.Container(ft.Icon(ft.Icons.DIRECTIONS_RUN, size=size * 0.30, color=Palette.ACCENT),
                         padding=ft.Padding.only(bottom=size * 0.03)),
        ], alignment=ft.Alignment.CENTER)

    def _splash(self):
        animate = self.animations and self.service.settings.show_splash
        self._splash_logo = ft.Container(
            content=self._logo(),
            width=120, height=120, border_radius=60, bgcolor=Palette.ACCENT,
            alignment=ft.Alignment.CENTER, scale=0.6 if animate else 1.0, opacity=0.0 if animate else 1.0,
            animate_scale=ft.Animation(700, ft.AnimationCurve.EASE_OUT_BACK) if animate else None,
            animate_opacity=ft.Animation(500, ft.AnimationCurve.EASE_OUT) if animate else None,
            shadow=ft.BoxShadow(blur_radius=40, color=ft.Colors.with_opacity(0.45, Palette.ACCENT)),
        )
        self._splash_status = ft.Text("Loading your training history...", color=Palette.MUTED, size=13)
        return ft.Container(
            expand=True,
            gradient=ft.LinearGradient(begin=ft.Alignment.TOP_CENTER, end=ft.Alignment.BOTTOM_CENTER,
                                       colors=[Palette.ACCENT_SOFT, Palette.BG]),
            alignment=ft.Alignment.CENTER,
            content=ft.Column([
                self._splash_logo,
                ft.Container(height=12),
                ft.Text(APP_NAME.upper(), size=34, font_family=HEADING_FONT, color=Palette.TEXT),
                ft.Text(APP_TAGLINE, color=Palette.MUTED, size=14, italic=True),
                ft.Container(height=28),
                ft.ProgressRing(width=26, height=26, stroke_width=3, color=Palette.ACCENT),
                self._splash_status,
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, alignment=ft.MainAxisAlignment.CENTER,
                spacing=8, tight=True),
        )

    async def _boot(self):
        started = asyncio.get_running_loop().time()
        await asyncio.sleep(0.05)
        self._splash_logo.scale = 1.0
        self._splash_logo.opacity = 1.0
        self.page.update()
        status = await asyncio.to_thread(self.service.load)
        if self.service.settings.show_splash:
            remaining = SPLASH_MIN_SECONDS - (asyncio.get_running_loop().time() - started)
            if remaining > 0:
                await asyncio.sleep(remaining)
        self._show_shell()
        if status == LoadStatus.CORRUPT:
            self.notify("Your data file was corrupt. It was backed up and a fresh one started.", error=True)
        elif self.service.settings_status == LoadStatus.CORRUPT:
            self.notify("Your settings file was unreadable. It was backed up and defaults restored.", error=True)
        if not self.service.has_profile:
            self.go(self.PROFILE)

    def _show_shell(self):
        self.page.clean()
        self.page.navigation_bar = self._nav
        self.page.add(ft.SafeArea(
            expand=True,
            content=ft.Container(
                expand=True, alignment=ft.Alignment.TOP_CENTER,
                padding=ft.Padding.only(left=16, right=16, top=12),
                content=ft.Container(content=self._body, expand=True, max_width=CONTENT_MAX_WIDTH),
            ),
        ))
        self.go(self._index)

    # -------------------------------------------------------------- navigation
    def _on_nav_change(self, e):
        self.go(e.control.selected_index)

    def go(self, index):
        """Show a tab, rebuilding it from the latest data."""
        self._index = index
        self._nav.selected_index = index
        view = self.views[index]
        view.refresh()
        self._body.content = ft.Container(content=view.root, expand=True, key="view-{}".format(index))
        self.page.update()

    def refresh_current(self):
        self.go(self._index)

    # ---------------------------------------------------------------- settings
    def apply_appearance(self):
        """Use the saved colour theme now: restyle the page, rebuild every screen, redraw."""
        Palette.apply(self.service.settings.theme)
        self._style_page()
        for view in self.views:
            view.restyle()
        self.go(self._index)

    def apply_preferences(self, update=True):
        """Use the saved display preferences (screens read the rest when they rebuild)."""
        on = self.animations
        self._body.duration = SWITCH_MS if on else 0
        self._body.reverse_duration = SWITCH_REVERSE_MS if on else 0
        if update:
            self.page.update()

    async def _on_files_picked(self, e):
        await self.views[self.PROFILE].handle_picked_files(e)

    # ------------------------------------------------------------------ helpers
    def notify(self, message, error=False, action=None, on_action=None):
        self.page.show_dialog(ft.SnackBar(
            content=ft.Text(message, color=Palette.ON_ERROR if error else Palette.TEXT),
            bgcolor=Palette.ERROR if error else Palette.CARD_HIGH,
            behavior=ft.SnackBarBehavior.FLOATING,
            action=action, on_action=on_action,
            duration=4000,
        ))
        self.page.update()

    def close_dialog(self):
        self.page.pop_dialog()
        self.page.update()

    def pick_date(self, on_pick, initial=None, first=None, last=None, help_text="Choose a day"):
        """Open the day picker and call on_pick(date) with the chosen day when the user confirms.
        Days are plain dates in the user's time zone (see Settings), today by default."""
        today = self.service.today
        DayPickerDialog(self, on_pick, initial or today, first or today,
                        last or today + datetime.timedelta(days=60), help_text).open()


def _app_main(page: ft.Page):
    CoachGUI(page, CoachService.create()).start()


def launch(web=False, port=8550):
    """Start the GUI as a desktop window, or as a web app when web=True
    (FLET_FORCE_WEB_SERVER=true, as set in the Dockerfile, also forces web mode)."""
    if web:
        ft.run(_app_main, view=ft.AppView.WEB_BROWSER, port=port, assets_dir=ASSETS_DIR)
    else:
        ft.run(_app_main, assets_dir=ASSETS_DIR)
