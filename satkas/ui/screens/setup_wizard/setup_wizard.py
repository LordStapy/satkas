
import os
from functools import partial

from kivy.lang import Builder
from kivymd.uix.screen import MDScreen
from kivymd.uix.screenmanager import MDScreenManager
from kivy.properties import StringProperty, ObjectProperty
from kivy.clock import Clock

from satkas.ui.screens.setup_wizard.pages import (
    LandingPage,
    TorSetupPage,
    KaspadSetupPage,
    KaspaWalletSetupPage,
    LnWalletSetupPage,
    PasswordSetupPage
)
from satkas.core.swapper.taker import Taker
from satkas.core.db.models import Setting


Builder.load_file(os.path.join(os.path.dirname(__file__), 'setup_wizard.kv'))


class SetupWizard(MDScreen):
    """
    Multi-page setup wizard that guides users through application configuration.
    Uses a ScreenManager to navigate between different setup steps.
    """

    screen_manager = ObjectProperty(None)
    current_step = StringProperty('landing')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.name = "setup_wizard"
        self.screen_manager = None
        self.app = None
        self.service_manager = None

        # Initialize pages
        self.pages = {
            'landing': LandingPage(name='landing'),
            'tor_setup': TorSetupPage(name='tor_setup'),
            'kaspad_setup': KaspadSetupPage(name='kaspad_setup'),
            'kaspa_wallet_setup': KaspaWalletSetupPage(name='kaspa_wallet_setup'),
            'ln_wallet_setup': LnWalletSetupPage(name='ln_wallet_setup'),
            'password_setup': PasswordSetupPage(name='password_setup')
        }

        # Set up navigation callbacks
        self.pages['landing'].on_next = self.go_to_tor_setup
        self.pages['tor_setup'].on_next = self.go_to_kaspad_setup
        self.pages['tor_setup'].on_back = self.go_to_landing
        self.pages['kaspad_setup'].on_next = self.go_to_kaspa_wallet_setup
        self.pages['kaspad_setup'].on_back = partial(self.go_to_tor_setup, direction='right')
        self.pages['kaspa_wallet_setup'].on_next = self.go_to_ln_wallet_setup
        self.pages['kaspa_wallet_setup'].on_back = partial(self.go_to_kaspad_setup, direction='right')
        self.pages['ln_wallet_setup'].on_next = self.complete_setup  #self.go_to_password_setup  # <- disabled for now, password isn't ready yet
        self.pages['ln_wallet_setup'].on_back = partial(self.go_to_kaspa_wallet_setup, direction='right')
        # password setup currently disabled
        self.pages['password_setup'].on_next = self.complete_setup
        self.pages['password_setup'].on_back = partial(self.go_to_ln_wallet_setup, direction='right')

    def on_enter(self):
        """Called when the wizard screen is entered."""
        from kivy.app import App
        self.app = App.get_running_app()
        self.service_manager = self.app.service_manager

        # Initialize screen manager with pages
        if not self.screen_manager:
            self.screen_manager = self.ids.screen_manager
            for page in self.pages.values():
                self.screen_manager.add_widget(page)

        # Start with landing page
        self.current_step = 'landing'
        self.screen_manager.current = 'landing'

    def go_to_landing(self, direction='right'):
        """Navigate to landing page."""
        self.current_step = 'landing'
        self.screen_manager.transition.direction = direction
        self.screen_manager.current = 'landing'
        self._update_step_indicator()

    def go_to_tor_setup(self, direction='left'):
        """Navigate to Tor setup page."""
        self.current_step = 'tor_setup'
        self.screen_manager.transition.direction = direction
        self.screen_manager.current = 'tor_setup'
        self._update_step_indicator()

    def go_to_kaspad_setup(self, direction='left'):
        """Navigate to Kaspad setup page."""
        self.current_step = 'kaspad_setup'
        self.screen_manager.transition.direction = direction
        self.screen_manager.current = 'kaspad_setup'
        self._update_step_indicator()

    def go_to_kaspa_wallet_setup(self, direction='left'):
        """Navigate to Kaspa wallet setup page."""
        self.current_step = 'kaspa_wallet_setup'
        self.screen_manager.transition.direction = direction
        self.screen_manager.current = 'kaspa_wallet_setup'
        self._update_step_indicator()

    def go_to_ln_wallet_setup(self, direction='left'):
        """Navigate to LN wallet setup page."""
        self.current_step = 'ln_wallet_setup'
        self.screen_manager.transition.direction = direction
        self.screen_manager.current = 'ln_wallet_setup'
        self._update_step_indicator()

    def go_to_password_setup(self, direction='left'):
        """Navigate to password setup page."""
        self.current_step = 'password_setup'
        self.screen_manager.transition.direction = direction
        self.screen_manager.current = 'password_setup'
        self._update_step_indicator()

    def _update_step_indicator(self):
        """Update the step indicator."""
        if hasattr(self, 'ids') and 'step_indicator' in self.ids:
            current_page = self.get_current_page()
            if current_page:
                self.ids.step_indicator.text = current_page.step_title
            else:
                self.ids.step_indicator.text = "Welcome"

    def complete_setup(self):
        """Complete the setup wizard and navigate to dashboard."""
        # Save all service configurations
        self._save_all_service_configs()

        # Enable all services
        self.app.service_manager.enable_all_services()

        # we don't need some stuff if we are re-doing the Wizard after the app was already initialized
        if not isinstance(self.app.taker, Taker):
            print(f"Instantiating new taker in setup_wizard.complete_setup")
            self.app.taker = self.app.taker(wallet_passwd='')
        
            # Add screens to main screen
            self.manager.get_screen('main_screen').children[0].ids.screen_manager.add_widget(
                self.app.quick_swap_v2_screen
            )
            #self.manager.get_screen('main_screen').children[0].ids.screen_manager.add_widget(
            #    self.app.quick_swap_screen
            #)
            self.manager.get_screen('main_screen').children[0].ids.screen_manager.add_widget(
                self.app.dashboard_screen
            )

        # switch to main screen
        self.manager.get_screen('main_screen').children[0].ids.menu_button.opacity = 1
        self.manager.get_screen('main_screen').children[0].ids.menu_button.disabled = False
        self.manager.current = 'main_screen'

    def _save_all_service_configs(self):
        """Save configurations for all services."""
        # Get password if set  <- not yet implemented
        password = None
        password_page = self.pages.get('password_setup')
        if password_page and password_page.password:
            password = password_page.password

        # Save all service configurations
        service_manager = self.app.service_manager

        # Save Tor service config
        if hasattr(service_manager, 'tor_service'):
            service_manager.tor_service.save_config()

        # Save Kaspad service config
        if hasattr(service_manager, 'kaspad_service'):
            service_manager.kaspad_service.save_config()

        # Save Kaspa wallet service config
        if hasattr(service_manager, 'kaspa_wallet_service'):
            service_manager.kaspa_wallet_service.save_config()

        # Save LN wallet service config
        if hasattr(service_manager, 'ln_wallet_service'):
            service_manager.ln_wallet_service.save_config()

        # Save wallet preferences through service manager
        service_manager.save_wallet_preferences()

        # Set db_initialized to True
        Setting.set_value('db_initialized', True, 'bool')



    def get_current_page(self):
        """Get the currently active page."""
        return self.pages.get(self.current_step)
