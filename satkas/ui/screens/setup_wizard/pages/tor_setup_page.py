
import os

from kivy.lang import Builder
from kivymd.uix.screen import MDScreen
from kivy.properties import StringProperty, BooleanProperty
import asyncio


Builder.load_file(os.path.join(os.path.dirname(__file__), 'tor_setup_page.kv'))


class TorSetupPage(MDScreen):
    """
    Tor setup page for the setup wizard.
    Allows users to configure Tor connection settings.
    """

    step_title = StringProperty("Tor Setup")
    host = StringProperty('')
    port = StringProperty('')
    is_validating = BooleanProperty(False)
    validation_message = StringProperty('')
    is_configured = BooleanProperty(False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.on_next = None  # Callback to navigate to next page
        self.on_back = None  # Callback to navigate to previous page
        self.service = None

    def on_enter(self):
        """Called when the page is entered."""
        # Get the Tor service from the app's service manager
        from kivy.app import App
        app = App.get_running_app()
        self.service = app.service_manager.tor_service

        # Load current configuration
        self.host = str(self.service.host)
        self.port = str(self.service.port)

        # Reset validation state
        self.is_validating = False
        self.validation_message = ''
        self.is_configured = False

    def on_host_text(self, text):
        """Called when host text changes."""
        self.host = text

    def on_port_text(self, text):
        """Called when port text changes."""
        self.port = text

    def use_defaults(self):
        """Use default Tor configuration."""
        self.host = str(self.service.default_host)
        self.port = str(self.service.default_port)

    async def validate_configuration(self):
        """Validate the current Tor configuration."""
        if self.is_validating:
            return

        self.is_validating = True
        self.validation_message = 'Validating Tor connection...'

        try:
            # Update service configuration
            self.service.host = self.host
            self.service.port = int(self.port)

            # Attempt detection
            detected = await self.service.detect()
            if not detected:
                self.validation_message = 'Tor not detected at the specified address.'
                self.is_configured = False
                return

            # Attempt validation
            validated = await self.service.validate()
            if validated:
                self.validation_message = 'Tor connection validated successfully!'
                self.is_configured = True
                # self.service.save_config()
            else:
                self.validation_message = 'Tor detected but unable to connect to test service.'
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
