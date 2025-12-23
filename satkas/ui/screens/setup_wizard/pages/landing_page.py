
import os

from kivy.lang import Builder
from kivymd.uix.screen import MDScreen
from kivy.properties import StringProperty

Builder.load_file(os.path.join(os.path.dirname(__file__), 'landing_page.kv'))


class LandingPage(MDScreen):
    """
    Landing page for the setup wizard.
    Welcomes users and provides an entry point to begin setup.
    """

    step_title = StringProperty("Welcome")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.on_next = None  # Callback to navigate to next page

    def start_setup(self):
        """Called when user clicks the start button."""
        if self.on_next:
            self.on_next()
