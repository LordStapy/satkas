"""
Bitcoin monitor setup page for the setup wizard (bitcoind / mempool flavors).
"""

import os
import asyncio

from kivy.lang import Builder
from kivy.properties import StringProperty, BooleanProperty, ListProperty
from kivymd.uix.screen import MDScreen

Builder.load_file(os.path.join(os.path.dirname(__file__), 'bitcoin_setup_page.kv'))


class BitcoinSetupPage(MDScreen):
    step_title = StringProperty("Bitcoin Monitor Setup")
    selected_type = StringProperty('bitcoind')
    monitor_types = ListProperty([
        {
            'name': 'Bitcoind',
            'value': 'bitcoind',
            'description': 'Watch-only monitoring via a local or remote bitcoind node',
        },
        {
            'name': 'Mempool',
            'value': 'mempool',
            'description': 'Monitor via a mempool.space API instance',
        },
    ])

    host = StringProperty('')
    port = StringProperty('')
    rpc_user = StringProperty('')
    rpc_pass = StringProperty('')
    wallet_name = StringProperty('')
    min_confirmations = StringProperty('1')
    base_url = StringProperty('')

    is_validating = BooleanProperty(False)
    validation_message = StringProperty('')
    is_configured = BooleanProperty(False)

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
        self.service = self.service_manager.bitcoin_service
        self.selected_type = self._type_from_service()
        self._load_config()
        self.is_validating = False
        self.validation_message = ''
        self.is_configured = False

    def _type_from_service(self):
        name = type(self.service).__name__
        if 'Mempool' in name:
            return 'mempool'
        return 'bitcoind'

    def _load_config(self):
        if self.selected_type == 'bitcoind':
            self.host = str(getattr(self.service, 'host', '') or '')
            self.port = str(getattr(self.service, 'port', '') or '')
            self.rpc_user = getattr(self.service, 'rpc_user', '') or ''
            self.rpc_pass = getattr(self.service, 'rpc_pass', '') or ''
            self.wallet_name = getattr(self.service, 'wallet_name', '') or ''
            self.min_confirmations = str(getattr(self.service, 'min_confirmations', 1) or 1)
            self.base_url = ''
        else:
            self.base_url = getattr(self.service, 'base_url', '') or ''
            self.min_confirmations = str(getattr(self.service, 'min_confirmations', 1) or 1)
            self.host = ''
            self.port = ''
            self.rpc_user = ''
            self.rpc_pass = ''
            self.wallet_name = ''

    def on_type_selected(self, monitor_type):
        if self.selected_type != monitor_type:
            self.selected_type = monitor_type
        self.service_manager.set_preferred_bitcoin_monitor(monitor_type)
        self.service = self.service_manager.bitcoin_service
        self._load_config()

    def _apply_fields_to_service(self):
        if self.selected_type == 'bitcoind':
            if hasattr(self.service, 'host'):
                self.service.host = self.host
            if hasattr(self.service, 'port'):
                try:
                    self.service.port = int(self.port) if self.port else self.service.port
                except ValueError:
                    pass
            if hasattr(self.service, 'rpc_user'):
                self.service.rpc_user = self.rpc_user
            if hasattr(self.service, 'rpc_pass'):
                self.service.rpc_pass = self.rpc_pass
            if hasattr(self.service, 'wallet_name'):
                self.service.wallet_name = self.wallet_name
            if hasattr(self.service, 'min_confirmations'):
                try:
                    self.service.min_confirmations = int(self.min_confirmations or 1)
                except ValueError:
                    self.service.min_confirmations = 1
        else:
            if hasattr(self.service, 'base_url'):
                self.service.base_url = self.base_url
            if hasattr(self.service, 'min_confirmations'):
                try:
                    self.service.min_confirmations = int(self.min_confirmations or 1)
                except ValueError:
                    self.service.min_confirmations = 1

    async def validate_configuration(self):
        if self.is_validating:
            return
        self.is_validating = True
        self.validation_message = 'Validating Bitcoin monitor...'
        try:
            self._apply_fields_to_service()
            detected = await self.service.detect()
            validated = await self.service.validate()
            if detected and validated:
                self.validation_message = f'{self.service.service_name} connected and ready!'
                self.is_configured = True
            else:
                self.validation_message = f'{self.service.service_name} not accessible with current settings.'
                self.is_configured = False
        except Exception as e:
            self.validation_message = f'Validation failed: {e}'
            self.is_configured = False
        finally:
            self.is_validating = False

    def validate_and_complete(self):
        asyncio.create_task(self._validate_and_complete())

    async def _validate_and_complete(self):
        await self.validate_configuration()
        if self.is_configured and self.on_next:
            self.on_next()

    def go_back(self):
        if self.on_back:
            self.on_back()
