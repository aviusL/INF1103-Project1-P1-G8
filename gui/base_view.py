"""Base class for the tab screens."""
from abc import ABC, abstractmethod

import flet as ft


class AppView(ABC):
    """One tab of the app. The shell only calls refresh() and reads root, so a new tab is a
    new subclass and nothing in the shell changes (Open/Closed, polymorphism)."""

    label = "View"
    icon = ft.Icons.CIRCLE_OUTLINED
    selected_icon = ft.Icons.CIRCLE

    def __init__(self, app):
        self.app = app                # association: the shell outlives every view
        self.service = app.service
        self.root = ft.Column(spacing=16, scroll=ft.ScrollMode.AUTO, expand=True)

    def refresh(self):
        """Rebuild the screen from the current service state."""
        self.root.controls = self.build()

    def restyle(self):
        """Called after the colour theme changes, before the next refresh(). A screen that keeps
        controls between refreshes rebuilds them here so they pick up the new colours."""

    @abstractmethod
    def build(self):
        """Return the list of controls that make up the screen."""
