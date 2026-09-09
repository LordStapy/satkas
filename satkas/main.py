
import os
import logging

# Must be set before any kivy import. KIVY_NO_CONSOLELOG only skips Kivy's
# ConsoleHandler; ~/.kivy/config.ini still resets Logger to INFO unless we
# override via KCFG_* (takes precedence over config.ini).
os.environ["KIVY_NO_CONSOLELOG"] = "1"
os.environ["KCFG_KIVY_LOG_LEVEL"] = "error"

from satkas.core.services import ServiceManager
service_manager = ServiceManager()
from satkas.core.swapper.taker import Taker
import asyncio

import traceback

from kivy.config import Config
Config.set('input', 'mouse', 'mouse,disable_multitouch')

from kivy.lang import Builder
from kivy.base import ExceptionManager, ExceptionHandler
from kivymd.uix.screen import MDScreen
from kivy.uix.screenmanager import ScreenManager, FadeTransition
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.theming import OptionProperty, ThemeManager
from kivy.animation import Animation
from kivy.clock import Clock
from kivymd.app import MDApp

from kivy.app import App

from satkas.core.db.models import Setting
from satkas.ui.screens.setup_wizard import SetupWizard
from satkas.ui.screens.dashboard_screen import DashboardScreen
from satkas.ui.screens.quick_swap_v2 import QuickSwapV2Screen
from kivy.utils import hex_colormap

logging.getLogger('taker').setLevel(logging.INFO)
logging.getLogger('atomic_swap').setLevel(logging.INFO)
hex_colormap['kaspa'] = '#70C7BA'


class ExceptionLogger(ExceptionHandler):
    def handle_exception(self, inst):
        print('Exception managed by ExceptionLogger')
        print(''.join(traceback.format_exception(inst)))
        return ExceptionManager.PASS


ExceptionManager.add_handler(ExceptionLogger())


class KaspaThemeManager(ThemeManager):
    primary_palette = OptionProperty(
        None,
        options=[color.capitalize() for color in hex_colormap.keys()]
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.primary_palette = 'Kaspa'


class LoadingScreen(MDScreen):
    """Loading screen that displays while the app initializes."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.name = "loading_screen"

    def on_enter(self):
        """Called when the screen is entered."""
        self._start_loading_animation()

    def _start_loading_animation(self):
        """Start the loading animation."""
        logo_label = None
        for child in self.children[0].children:
            if hasattr(child, 'text') and child.text == "SATKAS":
                logo_label = child
                break

        if logo_label:
            # Subtle pulsing animation for the logo
            self.logo_anim = Animation(opacity=0.7, duration=1.5, t='in_out_sine')
            self.logo_anim += Animation(opacity=1.0, duration=1.5, t='in_out_sine')
            self.logo_anim.repeat = True
            self.logo_anim.start(logo_label)

        self.ids.progress_bar.start()

    # def fade_out(self, callback=None):
    #     """Fade out the loading screen."""
    #     # if hasattr(self, 'progress_anim'):
    #     #     self.progress_anim.stop(self.progress_bar)
    #     if hasattr(self, 'logo_anim'):
    #         self.logo_anim.stop()

    #     anim = Animation(opacity=0, duration=0.5, t='out_quad')
    #     if callback:
    #         anim.bind(on_complete=lambda *args: callback())
    #     anim.start(self)


class MainScreen(MDScreen):
    """Main screen containing the app's primary interface."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.name = "main_screen"
        self._nav_layout_stable = False
        self._initialized = False

        # Create the main content using the KV-defined MainWidget layout
        main_layout = MainWidget()
        self.add_widget(main_layout)

        # Mark as initialized after a short delay
        Clock.schedule_once(self._mark_initialized, 0.5)

    def _mark_initialized(self, dt):
        """Mark the screen as initialized."""
        self._initialized = True
        self._nav_layout_stable = True

    def on_enter(self):
        """Called when the screen is entered."""
        # Enable menu button now that we're in the main screen
        main_widget = self.children[0] if self.children else None
        if main_widget and hasattr(main_widget, 'ids') and 'menu_button' in main_widget.ids:
            main_widget.ids.menu_button.opacity = 1
            main_widget.ids.menu_button.disabled = False


class MainWidget(MDBoxLayout):
    """Main widget defined by KV file."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = 'vertical'

    def toggle_nav_drawer(self):
        """Toggle the navigation drawer open/closed."""
        nav_drawer = self.ids.nav_drawer
        nav_drawer.set_state("open" if nav_drawer.state == "close" else "close")

    def select_nav_screen(self, screen_name):
        """Switch content screen; close the drawer only when it is modal.

        Desktop keeps a standard (always-open) drawer; closing that on every
        nav tap would collapse the rail until the window is resized.
        """
        self.ids.screen_manager.current = screen_name
        nav_drawer = self.ids.nav_drawer
        if nav_drawer.drawer_type == "modal":
            nav_drawer.set_state("close")


class RootScreenManager(ScreenManager):
    """Root screen manager that handles switching between loading and main screens."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.transition = FadeTransition()
        self._db_initialized = Setting.get_value("db_initialized", False)
        self._initialization_complete = False

        # Add screens
        self.add_widget(LoadingScreen())

        self.add_widget(SetupWizard())
        self.add_widget(MainScreen())

        self.app = App.get_running_app()

        # Start initialization check
        Clock.schedule_once(self._start_initialization_check, 0.5)

    def _start_initialization_check(self, dt):
        """Start checking for initialization completion."""
        Clock.schedule_once(self._check_initialization_complete, 2)

        asyncio.create_task(self.load_settings())

    async def load_settings(self):
        if self._db_initialized:
            # initialize services
            self.app.service_manager.initialize_services()
            await self.app.service_manager.detect_all_services()
            self.app.service_manager.enable_all_services()
        self._initialization_complete = True

    def _check_initialization_complete(self, dt):
        """Check if all components are ready."""
        main_screen = self.get_screen("main_screen")
        if main_screen._nav_layout_stable and main_screen._initialized:
            if self._initialization_complete:
                self._on_initialization_complete()
                return

        # Continue checking
        Clock.schedule_once(self._check_initialization_complete, 0.5)

    def _on_initialization_complete(self):
        """Called when all initialization is complete."""

        # Check if database was already set up, if not, show setup wizard
        if self._db_initialized:
            # Instantiate the Taker with the app's ServiceManager so
            # orchestrators can reach kaspad/bitcoin/wallets via self.sm.
            self.app.taker = self.app.taker(
                wallet_passwd='',
                service_manager=self.app.service_manager,
            )
            # add screens to main screen
            main_screen = self.get_screen("main_screen")
            main_screen.children[0].ids.screen_manager.add_widget(
                self.app.quick_swap_v2_screen
            )
            # main_screen.children[0].ids.screen_manager.add_widget(
            #     self.app.quick_swap_screen
            # )
            main_screen.children[0].ids.screen_manager.add_widget(
                self.app.dashboard_screen
            )
            self.current = "main_screen"
        else:
            self.current = "setup_wizard"

    def toggle_nav_drawer(self):
        """Toggle the navigation drawer open/closed."""
        nav_drawer = self.ids.nav_drawer
        nav_drawer.set_state("open" if nav_drawer.state == "close" else "close")


class SatKasApp(MDApp):
    def __init__(self, **kwargs):
        # Window.size = (1280, 720)
        super().__init__(**kwargs)
        self.theme_cls = KaspaThemeManager()
        self.service_manager = service_manager
        self.taker = Taker  # NOTE: we are not instanciating the taker yet, later we'll call self.taker() with the correct parameters
        self.maker = None
        self._dashboard_screen = None
        self._quick_swap_screen = None
        self._quick_swap_v2_screen = None

    @property
    def dashboard_screen(self):
        """Lazy-load the dashboard screen."""
        if self._dashboard_screen is None:
            self._dashboard_screen = DashboardScreen(name='dashboard_screen')
        return self._dashboard_screen

    @property
    def quick_swap_v2_screen(self):
        """Lazy-load the quick swap v2 screen."""
        if self._quick_swap_v2_screen is None:
            self._quick_swap_v2_screen = QuickSwapV2Screen(name='quick_swap_v2_screen')
        return self._quick_swap_v2_screen

    def build(self):
        """Build the app with the responsive layout."""
        self.theme_cls.theme_style = "Dark"
        self.theme_cls.primary_palette = "Kaspa"

        # Load the KV file
        Builder.load_file(os.path.join(os.path.dirname(__file__), 'main_app.kv'))

        # Return the main widget
        manager = RootScreenManager()
        from kivy.core.window import Window
        Window.size = (1280, 720)
        return manager

    def on_stop(self):
        self.service_manager.disable_all_services()


def main():
    loop = asyncio.get_event_loop()
    loop.run_until_complete(SatKasApp().async_run(async_lib='asyncio'))
    # wait for all tasks to complete
    all_tasks = asyncio.all_tasks(loop=loop)
    [t.cancel() for t in all_tasks]
    loop.run_until_complete(asyncio.gather(*all_tasks, return_exceptions=True))
    loop.close()


if __name__ == "__main__":
    main()
