
import os

from kivy.lang import Builder
from kivy.metrics import dp
from kivymd.uix.screen import MDScreen
from kivymd.uix.button import MDButton, MDButtonText
from kivymd.uix.widget import MDWidget
from kivymd.uix.dialog import MDDialog, MDDialogButtonContainer, MDDialogContentContainer, MDDialogHeadlineText, MDDialogSupportingText
from kivy.properties import StringProperty, BooleanProperty


Builder.load_file(os.path.join(os.path.dirname(__file__), 'password_setup_page.kv'))


class PasswordSetupPage(MDScreen):
    """
    Password setup page for the setup wizard.
    Allows users to optionally set a password for the application.
    """

    step_title = StringProperty("Set Application Password")
    password = StringProperty('')
    confirm_password = StringProperty('')
    show_password_error = BooleanProperty(False)
    password_error_message = StringProperty('')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.on_back = None  # Callback to navigate to previous page
        self.on_next = None  # Callback to navigate to next page

    def on_enter(self):
        """Called when the page is entered."""
        # Reset error state
        self.show_password_error = False
        self.password_error_message = ''

    def on_password_text(self, text):
        """Called when password text changes."""
        self.password = text
        self._validate_passwords()

    def on_confirm_password_text(self, text):
        """Called when confirm password text changes."""
        self.confirm_password = text
        self._validate_passwords()

    def _validate_passwords(self):
        """Validate password fields."""
        if not self.password and not self.confirm_password:
            # Both empty is valid (optional password)
            self.show_password_error = False
            self.password_error_message = ''
            return

        if self.password != self.confirm_password:
            self.show_password_error = True
            self.password_error_message = 'Passwords do not match'
        else:
            self.show_password_error = False
            self.password_error_message = ''

    def go_next(self):
        """Navigate to next page after saving password."""
        # Save password if valid
        if not self.show_password_error:
            if self.on_next:
                self.on_next()

    def go_back(self):
        """Navigate to previous page."""
        if self.on_back:
            self.on_back()
