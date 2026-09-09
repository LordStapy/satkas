
import os

from kivy.lang import Builder
from kivy.metrics import dp
from kivymd.uix.screen import MDScreen
from kivymd.uix.button import MDButton, MDButtonText
from kivymd.uix.widget import MDWidget
from kivymd.uix.dialog import MDDialog, MDDialogButtonContainer, MDDialogHeadlineText, MDDialogSupportingText
from kivy.properties import StringProperty, BooleanProperty, ListProperty
import asyncio


Builder.load_file(os.path.join(os.path.dirname(__file__), 'kaspad_setup_page.kv'))


class KaspadSetupPage(MDScreen):
    """
    Kaspa monitor setup page for the setup wizard.
    Choice between a kaspad node and the Kaspa explorer REST API.
    """

    step_title = StringProperty("Kaspa Node Setup")
    selected_type = StringProperty('kaspad')
    monitor_types = ListProperty([
        {
            'name': 'Kaspad',
            'value': 'kaspad',
            'description': 'Connect to a local or remote kaspad node via gRPC',
        },
        {
            'name': 'Explorer',
            'value': 'explorer',
            'description': 'Use the Kaspa explorer REST API (api.kaspa.org or self-hosted)',
        },
    ])

    host = StringProperty('')
    port = StringProperty('')
    base_url = StringProperty('')
    is_validating = BooleanProperty(False)
    validation_message = StringProperty('')
    is_configured = BooleanProperty(False)
    show_fallback_prompt = BooleanProperty(False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.on_next = None
        self.on_back = None
        self.service = None
        self.service_manager = None

    def on_enter(self):
        from kivy.app import App
        app = App.get_running_app()
        self.service_manager = app.service_manager
        self.service = self.service_manager.kaspad_service
        self.selected_type = self._type_from_service()
        self._load_config()
        self.is_validating = False
        self.validation_message = ''
        self.is_configured = False
        self.show_fallback_prompt = False

    def _type_from_service(self):
        name = type(self.service).__name__
        if 'Explorer' in name:
            return 'explorer'
        return 'kaspad'

    def _load_config(self):
        if self.selected_type == 'kaspad':
            self.host = str(getattr(self.service, 'host', '') or '')
            self.port = str(getattr(self.service, 'port', '') or '')
            self.base_url = ''
        else:
            self.base_url = getattr(self.service, 'base_url', '') or ''
            self.host = ''
            self.port = ''

    def on_type_selected(self, monitor_type):
        if self.selected_type != monitor_type:
            self.selected_type = monitor_type
        self.service_manager.set_preferred_kaspa_monitor(monitor_type)
        self.service = self.service_manager.kaspad_service
        self._load_config()

    def on_host_text(self, text):
        self.host = text

    def on_port_text(self, text):
        self.port = text

    def on_base_url_text(self, text):
        self.base_url = text

    def use_defaults(self):
        if self.selected_type == 'kaspad':
            self.host = str(self.service.default_host)
            self.port = str(self.service.default_port)
        else:
            self.base_url = getattr(self.service, 'default_base_url', 'https://api.kaspa.org')

    def use_fallback(self):
        self.host = str(self.service.fallback_host)
        self.port = str(self.service.fallback_port)
        self.show_fallback_prompt = False
        if hasattr(self, 'fallback_dialog') and self.fallback_dialog:
            self.fallback_dialog.dismiss()

    def cancel_fallback(self):
        self.show_fallback_prompt = False
        if hasattr(self, 'fallback_dialog') and self.fallback_dialog:
            self.fallback_dialog.dismiss()

    def show_fallback_dialog(self):
        if MDDialog is None:
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

    def _apply_fields_to_service(self):
        if self.selected_type == 'kaspad':
            self.service.host = self.host
            try:
                self.service.port = int(self.port) if self.port else self.service.port
            except ValueError:
                pass
        else:
            base_url = self.base_url
            if hasattr(self.service, '_normalize_base_url'):
                base_url = self.service._normalize_base_url(base_url)
            self.service.base_url = base_url

    async def validate_configuration(self):
        if self.is_validating:
            return

        self.is_validating = True
        label = 'Kaspa node' if self.selected_type == 'kaspad' else 'Kaspa explorer'
        self.validation_message = f'Detecting {label}...'

        try:
            self._apply_fields_to_service()
            detected = await self.service.detect()
            if detected:
                self.validation_message = f'{self.service.service_name} detected and validated!'
                self.is_configured = True
            else:
                err = getattr(self.service, '_last_detect_error', None) or ''
                if 'full capacity' in err.lower() or 'resource_exhausted' in err.lower():
                    self.validation_message = (
                        'Kaspa node is reachable but at full gRPC capacity '
                        '(too many connections). Try again later or use another node.'
                    )
                elif err:
                    self.validation_message = f'{label} not reachable: {err}'
                else:
                    self.validation_message = f'{label} not found at the specified address.'
                if (self.selected_type == 'kaspad' and
                    self.host == str(getattr(self.service, 'default_host', '')) and
                    self.port == str(getattr(self.service, 'default_port', '')) and
                    getattr(self.service, 'fallback_host', None) is not None):
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
        asyncio.create_task(self._validate_and_continue())

    async def _validate_and_continue(self):
        await self.validate_configuration()
        if self.is_configured and self.on_next:
            self.on_next()

    def go_back(self):
        if self.on_back:
            self.on_back()
