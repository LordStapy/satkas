
import os

from kivy.lang import Builder
from kivy.metrics import dp
from kivymd.uix.screen import MDScreen
from kivymd.uix.button import MDButton, MDButtonText
from kivymd.uix.widget import MDWidget
from kivymd.uix.dialog import MDDialog, MDDialogButtonContainer, MDDialogHeadlineText, MDDialogSupportingText
from kivy.properties import StringProperty, BooleanProperty, ListProperty
import asyncio
from functools import partial


Builder.load_file(os.path.join(os.path.dirname(__file__), 'ln_wallet_setup_page.kv'))


class LnWalletSetupPage(MDScreen):
    """
    Lightning Network wallet setup page for the setup wizard.
    Allows users to select and configure their LN wallet implementation.
    """

    step_title = StringProperty("Lightning Wallet Setup")
    selected_wallet_type = StringProperty('external')
    wallet_types = ListProperty([
        {'name': 'External Wallet', 'value': 'external', 'description': 'Use an external Lightning Network wallet'},
        {'name': 'LNBits', 'value': 'lnbits', 'description': 'Connect to an LNBits server for Lightning payments'},
        {'name': 'LNCLI (LND)', 'value': 'lncli', 'description': 'Connect to a local LND node via lncli'},
    ])

    # LNBits configuration fields
    base_url = StringProperty('')
    user_id = StringProperty('')
    read_api_key = StringProperty('')
    admin_api_key = StringProperty('')
    wallet_id = StringProperty('')

    # LND / lncli configuration fields
    lncli_bin = StringProperty('')
    rpc_server = StringProperty('')

    is_validating = BooleanProperty(False)
    validation_message = StringProperty('')
    is_configured = BooleanProperty(False)
    show_fallback_prompt = BooleanProperty(False)
    lnbits_create_disabled = BooleanProperty(False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.on_back = None  # Callback to navigate to previous page
        self.on_next = None  # Callback to navigate to next page
        self.service = None
        self.service_manager = None

    def on_enter(self):
        """Called when the page is entered."""
        # Get the service manager
        from kivy.app import App
        app = App.get_running_app()
        self.service_manager = app.service_manager

        # Get current LN wallet service and type
        self.service = self.service_manager.ln_wallet_service
        self.selected_wallet_type = self._get_wallet_type_from_service()

        # Load configuration based on wallet type
        self._load_wallet_config()

        # Reset validation state
        self.is_validating = False
        self.validation_message = ''
        self.is_configured = False
        self.show_fallback_prompt = False

    def _get_wallet_type_from_service(self):
        """Determine wallet type from current service."""
        service_type = type(self.service).__name__
        if 'LNBits' in service_type:
            return 'lnbits'
        elif 'Lncli' in service_type:
            return 'lncli'
        else:
            return 'external'

    def _load_wallet_config(self):
        """Load configuration for the selected wallet type."""
        if self.selected_wallet_type == 'external':
            # External wallet has no configuration
            self.base_url = ''
            self.user_id = ''
            self.read_api_key = ''
            self.admin_api_key = ''
            self.wallet_id = ''
            self.lncli_bin = ''
            self.rpc_server = ''
        elif self.selected_wallet_type == 'lncli':
            self.lncli_bin = getattr(self.service, 'lncli_bin', '') or ''
            self.rpc_server = getattr(self.service, 'rpc_server', '') or ''
            self.base_url = ''
            self.user_id = ''
            self.read_api_key = ''
            self.admin_api_key = ''
            self.wallet_id = ''
        elif self.selected_wallet_type == 'lnbits':
            self.base_url = getattr(self.service, 'base_url', '')
            self.user_id = getattr(self.service, 'user_id', '')
            self.read_api_key = getattr(self.service, 'read_api_key', '')
            self.admin_api_key = getattr(self.service, 'admin_api_key', '')
            self.wallet_id = getattr(self.service, 'wallet_id', '')
            self.lncli_bin = ''
            self.rpc_server = ''
        self._sync_lnbits_create_button()

    def _sync_lnbits_create_button(self):
        self.lnbits_create_disabled = bool(self.user_id and self.wallet_id)

    def on_wallet_type_selected(self, wallet_type):
        """Called when user selects a wallet type."""
        print(f"DEBUG: on_wallet_type_selected called with: {wallet_type}")
        print(f"DEBUG: current selected_wallet_type: {self.selected_wallet_type}")
        if self.selected_wallet_type != wallet_type:
            self.selected_wallet_type = wallet_type
        # Update service manager preference
        self.service_manager.set_preferred_ln_wallet(wallet_type)
        # Get the new service
        self.service = self.service_manager.ln_wallet_service
        # Load its configuration
        self._load_wallet_config()

    def lnbits_create_account(self):
        """Called when create account button is pressed."""
        self.lnbits_create_disabled = True
        task = asyncio.create_task(self.service.create_new_account())
        task.add_done_callback(partial(self.lnbits_account_created_cb, self))

    @staticmethod
    def lnbits_account_created_cb(cls, task):
        """Called when account creation is complete."""
        cls.user_id = cls.service.user_id
        cls.read_api_key = cls.service.read_api_key
        cls.admin_api_key = cls.service.admin_api_key
        cls.wallet_id = cls.service.wallet_id
        cls._sync_lnbits_create_button()

    def on_base_url_text(self, text):
        """Called when base URL text changes."""
        self.base_url = text
        if self.selected_wallet_type != 'lnbits':
            return
        self.service.base_url = text
        task = asyncio.create_task(self.service.detect())
        task.add_done_callback(partial(self.service_detection_cb, self))
        # ??? should we add a progress indicator somewhere?

    @staticmethod
    def service_detection_cb(cls, task):
        """Called when service detection is complete."""
        res = task.result()
        print(f"Service detection result: {res}")
        if res:
            pass
            # ToDo: enable other fields (currently displayed at all times)
        else:
            # provide some error message to the user
            pass

    def on_user_id_text(self, text):
        """Called when user ID text changes."""
        self.user_id = text

    def on_read_api_key_text(self, text):
        """Called when read API key text changes."""
        self.read_api_key = text

    def on_admin_api_key_text(self, text):
        """Called when admin API key text changes."""
        self.admin_api_key = text

    def on_wallet_id_text(self, text):
        """Called when wallet ID text changes."""
        self.wallet_id = text

    def on_lncli_bin_text(self, text):
        self.lncli_bin = text

    def on_rpc_server_text(self, text):
        self.rpc_server = text

    def use_defaults(self):
        """Use default configuration for the selected wallet type."""
        if self.selected_wallet_type == 'lnbits':
            self.base_url = getattr(self.service, 'default_base_url', 'http://127.0.0.1:5000')
        elif self.selected_wallet_type == 'lncli':
            self.lncli_bin = getattr(self.service, 'default_lncli', 'lncli')
            self.rpc_server = getattr(self.service, 'default_rpc_server', '127.0.0.1:10009')

    def use_fallback(self):
        """Use fallback LNBits server (third-party hosted)."""
        self.base_url = getattr(self.service, 'fallback_base_url', 'https://lnbits.satkas.com')
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
                    text="Use Hosted LNBits Server?"
                ),
                MDDialogSupportingText(
                    text="Would you like to connect to a hosted LNBits server?\nYour IP address will be visible to the third party."
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
                            text="Use Hosted Instance"
                        ),
                        on_release=lambda x: self.use_fallback()
                    ),
                    spacing=dp(15),
                )
            )

        self.fallback_dialog.open()

    async def validate_configuration(self):
        """Validate the current wallet configuration."""
        if self.is_validating:
            return

        self.is_validating = True
        self.validation_message = 'Validating wallet configuration...'

        try:
            # Update service configuration based on selected type
            if self.selected_wallet_type == 'lncli':
                if hasattr(self.service, 'lncli_bin'):
                    self.service.lncli_bin = self.lncli_bin
                if hasattr(self.service, 'rpc_server'):
                    self.service.rpc_server = self.rpc_server

            elif self.selected_wallet_type == 'lnbits':
                if hasattr(self.service, 'base_url'):
                    self.service.base_url = self.base_url
                if hasattr(self.service, 'user_id'):
                    self.service.user_id = self.user_id
                if hasattr(self.service, 'read_api_key'):
                    self.service.read_api_key = self.read_api_key
                if hasattr(self.service, 'admin_api_key'):
                    self.service.admin_api_key = self.admin_api_key
                if hasattr(self.service, 'wallet_id'):
                    self.service.wallet_id = self.wallet_id

            # Attempt detection
            detected = await self.service.detect()
            validated = await self.service.validate()
            if detected and validated:
                self.validation_message = f'{self.service.service_name} connected and ready!'
                self.is_configured = True
                # self.service.save_config()
            else:
                self.validation_message = f'{self.service.service_name} not accessible with current settings.'
                # Check if we should offer fallback for base_url
                if (self.selected_wallet_type == 'lnbits' and
                    not self.base_url and
                    hasattr(self.service, 'fallback_base_url')):
                    self.show_fallback_prompt = True
                    self.show_fallback_dialog()
                    self.validation_message += ' Would you like to use a hosted LNBits server?'
                else:
                    self.is_configured = False

        except Exception as e:
            self.validation_message = f'Validation failed: {str(e)}'
            self.is_configured = False
        finally:
            self.is_validating = False

    def validate_and_complete(self):
        """Validate configuration and complete setup if successful."""
        asyncio.create_task(self._validate_and_complete())

    async def _validate_and_complete(self):
        """Async helper for validate_and_complete."""
        await self.validate_configuration()
        if self.is_configured and self.on_next:
            self.on_next()

    def go_back(self):
        """Navigate to previous page."""
        if self.on_back:
            self.on_back()
