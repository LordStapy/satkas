
import os

from kivy.lang import Builder
from kivymd.uix.screen import MDScreen
from kivy.properties import StringProperty, BooleanProperty, ListProperty
import asyncio

Builder.load_file(os.path.join(os.path.dirname(__file__), 'kaspa_wallet_setup_page.kv'))


class KaspaWalletSetupPage(MDScreen):
    """
    Kaspa wallet setup page for the setup wizard.
    Allows users to select and configure their Kaspa wallet implementation.
    """

    step_title = StringProperty("Kaspa Wallet Setup")
    selected_wallet_type = StringProperty('external')
    wallet_types = ListProperty([
        {'name': 'External Wallet', 'value': 'external', 'description': 'Use an external Kaspa wallet application'},
        {'name': 'Go Wallet (kaspawallet)', 'value': 'go', 'description': 'Use the official Go implementation'},
        # {'name': 'Rusty Kaspa Wallet', 'value': 'rusty', 'description': 'Use the Rust implementation - DO NOT USE THIS, IMPLEMENTATION IS INCOMPLETE AND UNTESTED'},
        {'name': 'Internal Kaspa Wallet', 'value': 'internal', 'description': 'Sign with a private key from env (INTERNAL_KASPA_PRIVKEY) — testnet'},
    ])

    # Configuration fields
    binary_path = StringProperty('')
    wallet_file_path = StringProperty('')
    wallet_name = StringProperty('')
    wallet_password = StringProperty('')
    daemon_host = StringProperty('')
    daemon_port = StringProperty('')

    is_validating = BooleanProperty(False)
    validation_message = StringProperty('')
    is_configured = BooleanProperty(False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.on_next = None  # Callback to navigate to next page
        self.on_back = None  # Callback to navigate to previous page
        self.service = None
        self.service_manager = None

    def on_enter(self):
        """Called when the page is entered."""
        # Get the service manager
        from kivy.app import App
        app = App.get_running_app()
        self.service_manager = app.service_manager

        # Get current wallet service and type
        self.service = self.service_manager.kaspa_wallet_service
        self.selected_wallet_type = self._get_wallet_type_from_service()

        # Load configuration based on wallet type
        self._load_wallet_config()

        # Reset validation state
        self.is_validating = False
        self.validation_message = ''
        self.is_configured = False

    def _get_wallet_type_from_service(self):
        """Determine wallet type from current service."""
        service_type = type(self.service).__name__
        if 'External' in service_type:
            return 'external'
        elif 'Internal' in service_type:
            return 'internal'
        # elif 'Rusty' in service_type:
        #     return 'rusty'
        else:
            return 'go'

    def _load_wallet_config(self):
        """Load configuration for the selected wallet type."""
        if self.selected_wallet_type == 'external':
            # External wallet has no configuration
            self.binary_path = ''
            self.wallet_file_path = ''
            self.wallet_name = ''
            self.wallet_password = ''
            self.daemon_host = ''
            self.daemon_port = ''
        elif self.selected_wallet_type == 'internal':
            # Env-driven (INTERNAL_KASPA_PRIVKEY); clear go fields
            self.binary_path = ''
            self.wallet_file_path = ''
            self.wallet_name = ''
            self.wallet_password = ''
            self.daemon_host = ''
            self.daemon_port = ''
        elif self.selected_wallet_type == 'go':
            self.binary_path = getattr(self.service, 'binary_path', '')
            self.wallet_file_path = getattr(self.service, 'wallet_file_path', '')
            self.wallet_password = getattr(self.service, 'wallet_password', '')
            self.daemon_host = getattr(self.service, 'daemon_host', '')
            self.daemon_port = str(getattr(self.service, 'daemon_port', ''))
            self.wallet_name = ''  # Not used in Go wallet
        # elif self.selected_wallet_type == 'rusty':
        #     self.binary_path = getattr(self.service, 'binary_path', '')
        #     self.wallet_name = getattr(self.service, 'wallet_name', '')
        #     self.wallet_password = getattr(self.service, 'wallet_password', '')
        #     self.wallet_file_path = ''  # Not used in Rusty wallet
        #     self.daemon_host = ''  # Not used in Rusty wallet
        #     self.daemon_port = ''  # Not used in Rusty wallet

    def on_wallet_type_selected(self, wallet_type):
        """Called when user selects a wallet type."""
        if self.selected_wallet_type != wallet_type:
            self.selected_wallet_type = wallet_type
            # Update service manager preference
            preference_map = {
                'external': 'external',
                'go': 'go',
                # 'rusty': 'rusty',
                'internal': 'internal',
            }
            self.service_manager.set_preferred_kaspa_wallet(preference_map[wallet_type])
            # Get the new service
            self.service = self.service_manager.kaspa_wallet_service
            # Load its configuration
            self._load_wallet_config()

    def on_binary_path_text(self, text):
        """Called when binary path text changes."""
        self.binary_path = text

    def on_wallet_file_path_text(self, text):
        """Called when wallet file path text changes."""
        self.wallet_file_path = text

    def on_wallet_name_text(self, text):
        """Called when wallet name text changes."""
        self.wallet_name = text

    def on_wallet_password_text(self, text):
        """Called when wallet password text changes."""
        self.wallet_password = text

    def on_daemon_host_text(self, text):
        """Called when daemon host text changes."""
        self.daemon_host = text

    def on_daemon_port_text(self, text):
        """Called when daemon port text changes."""
        self.daemon_port = text

    def use_defaults(self):
        """Use default configuration for selected wallet type."""
        if self.selected_wallet_type == 'go':
            self.binary_path = 'kaspawallet'
            self.wallet_file_path = ''
            self.wallet_password = ''
            self.daemon_host = str(self.service.default_daemon_host)
            self.daemon_port = str(self.service.default_daemon_port)
        # elif self.selected_wallet_type == 'rusty':
        #     self.binary_path = 'kaspa-wallet'
        #     self.wallet_name = ''
        #     self.wallet_password = ''

    async def validate_configuration(self):
        """Validate the current wallet configuration."""
        if self.is_validating:
            return

        self.is_validating = True
        self.validation_message = 'Validating wallet configuration...'

        try:
            # Update service configuration based on selected type
            if self.selected_wallet_type == 'internal':
                # Env-driven; detect/validate below
                pass

            elif self.selected_wallet_type == 'go':
                if hasattr(self.service, 'binary_path'):
                    self.service.binary_path = self.binary_path
                if hasattr(self.service, 'wallet_file_path'):
                    self.service.wallet_file_path = self.wallet_file_path
                if hasattr(self.service, 'wallet_password'):
                    self.service.wallet_password = self.wallet_password
                if hasattr(self.service, 'daemon_host'):
                    self.service.daemon_host = self.daemon_host
                if hasattr(self.service, 'daemon_port'):
                    self.service.daemon_port = int(self.daemon_port)

            # elif self.selected_wallet_type == 'rusty':
            #     if hasattr(self.service, 'binary_path'):
            #         self.service.binary_path = self.binary_path
            #     if hasattr(self.service, 'wallet_name'):
            #         self.service.wallet_name = self.wallet_name
            #     if hasattr(self.service, 'wallet_password'):
            #         self.service.wallet_password = self.wallet_password

            # Attempt detection
            detected = await self.service.detect()
            if detected:
                self.validation_message = f'{self.service.service_name} detected and ready!'
                self.is_configured = True
                # self.service.save_config()
            else:
                self.validation_message = f'{self.service.service_name} not found or not configured correctly.'
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
