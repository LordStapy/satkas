
import os

from kivy.lang import Builder
from kivy.metrics import dp
from kivymd.uix.screen import MDScreen
from kivymd.uix.button import MDButton, MDButtonText
from kivymd.uix.widget import MDWidget
from kivymd.uix.dialog import MDDialog, MDDialogButtonContainer, MDDialogContentContainer, MDDialogHeadlineText, MDDialogSupportingText
from kivy.properties import StringProperty, BooleanProperty, ObjectProperty
import asyncio


Builder.load_file(os.path.join(os.path.dirname(__file__), 'kaspad_setup_page.kv'))


class KaspadSetupPage(MDScreen):
    """
    Kaspad setup page for the setup wizard.
    Allows users to configure Kaspa node connection settings.
    """

    step_title = StringProperty("Kaspa Node Setup")
    host = StringProperty('')
    port = StringProperty('')
    is_validating = BooleanProperty(False)
    validation_message = StringProperty('')
    is_configured = BooleanProperty(False)
    show_fallback_prompt = BooleanProperty(False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.on_next = None  # Callback to navigate to next page
        self.on_back = None  # Callback to navigate to previous page
        self.service = None

    def on_enter(self):
        """Called when the page is entered."""
        # Get the Kaspad service from the app's service manager
        from kivy.app import App
        app = App.get_running_app()
        self.service = app.service_manager.kaspad_service

        # Load current configuration
        self.host = str(self.service.host)
        self.port = str(self.service.port)

        # Reset validation state
        self.is_validating = False
        self.validation_message = ''
        self.is_configured = False
        self.show_fallback_prompt = False

    def on_host_text(self, text):
        """Called when host text changes."""
        self.host = text

    def on_port_text(self, text):
        """Called when port text changes."""
        self.port = text

    def use_defaults(self):
        """Use default Kaspad configuration."""
        self.host = str(self.service.default_host)
        self.port = str(self.service.default_port)

    def use_fallback(self):
        """Use fallback Kaspad configuration (third-party hosted)."""
        self.host = str(self.service.fallback_host)
        self.port = str(self.service.fallback_port)
        self.show_fallback_prompt = False
        if hasattr(self, 'fallback_dialog') and self.fallback_dialog:
            self.fallback_dialog.dismiss()

    def cancel_fallback(self):
        """Cancel using fallback configuration."""
        self.show_fallback_prompt = False
        if hasattr(self, 'fallback_dialog') and self.fallback_dialog:
            self.fallback_dialog.dismiss()

    def show_fallback_dialog(self):
        """Show the fallback dialog programmatically."""
        if MDDialog is None:
            # Simple fallback - just use the configuration
            self.use_fallback()
            return

        if not hasattr(self, 'fallback_dialog') or not self.fallback_dialog:
            self.fallback_dialog = MDDialog(
                MDDialogHeadlineText(
                    text="Use Remote Kaspa Node?"
                ),
                MDDialogSupportingText(
                    text="Would you like to connect to a remote Kaspa node hosted by a third party? Your IP address will be visible to the third party."
                ),
                MDDialogButtonContainer(
                    MDWidget(),
                    MDButton(
                        MDButtonText(
                            text="Cancel"
                        ),
                        on_release=lambda x: self.cancel_fallback()
                    ),
                    MDButton(
                        MDButtonText(
                            text="Use Remote Node"
                        ),
                        on_release=lambda x: self.use_fallback()
                    ),
                    spacing=dp(15),
                )
            )

        self.fallback_dialog.open()

    async def validate_configuration(self):
        """Validate the current Kaspad configuration."""
        if self.is_validating:
            return

        self.is_validating = True
        self.validation_message = 'Detecting Kaspa node...'

        try:
            # Update service configuration
            self.service.host = self.host
            self.service.port = int(self.port)

            # Attempt detection
            detected = await self.service.detect()
            if detected:
                self.validation_message = 'Kaspa node detected and validated!'
                self.is_configured = True
                # self.service.save_config()
            else:
                self.validation_message = 'Kaspa node not found at the specified address.'
                # Check if we should offer fallback
                if (self.host == str(self.service.default_host) and
                    self.port == str(self.service.default_port) and
                    self.service.fallback_host is not None):
                    self.show_fallback_prompt = True
                    self.validation_message += ' Would you like to try a remote node?'
                else:
                    self.is_configured = False

        except Exception as e:
            self.validation_message = f'Validation failed: {str(e)}'
            self.is_configured = False
        finally:
            self.is_validating = False

    def validate_and_continue(self):
        """Validate configuration and continue if successful."""
        asyncio.create_task(self._validate_and_continue())

    async def _validate_and_continue(self):
        """Async helper for validate_and_continue."""
        await self.validate_configuration()
        if self.is_configured and self.on_next:
            self.on_next()

    def go_back(self):
        """Navigate to previous page."""
        if self.on_back:
            self.on_back()
