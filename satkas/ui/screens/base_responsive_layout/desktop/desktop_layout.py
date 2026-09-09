import os
from kivy.lang import Builder
from kivymd.uix.screen import MDScreen
from kivymd.uix.boxlayout import MDBoxLayout
from kivy.app import App
from kivy.metrics import dp


Builder.load_file(os.path.join(os.path.dirname(__file__), 'desktop_layout.kv'))

class MainDesktopColumn(MDBoxLayout):
    pass


class RightDesktopColumn(MDBoxLayout):
    pass


class DesktopLayout(MDScreen):
    """Desktop layout."""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.app = App.get_running_app()
    
    def _setup_navigation_drawer(self):
        """Setup navigation drawer for desktop layout."""

        # Navigate through the new ScreenManager structure
        if self.app and hasattr(self.app, 'root'):
            main_screen = self.app.root.get_screen("main_screen")
            if main_screen and main_screen.children:
                main_widget = main_screen.children[0]
                if hasattr(main_widget, 'ids') and 'nav_drawer' in main_widget.ids:
                    nav_drawer = main_widget.ids.nav_drawer
                    # Set drawer type to standard for desktop (not modal)
                    nav_drawer.drawer_type = "standard"
                    # Open the drawer for desktop
                    # nav_drawer.set_state("open")
                    # print(f"🖥️ Desktop layout: Navigation drawer set to standard type")
                else:
                    print("❌ Desktop layout: Could not access navigation drawer")
            else:
                print("❌ Desktop layout: Main screen not ready")
        else:
            print("❌ Desktop layout: Could not access app root")
    
    def on_layout_activated(self):
        """Called when this layout becomes active."""
        # print("🖥️ Desktop layout activated - setting up navigation drawer")
        # Setup navigation drawer when layout becomes active
        # self._setup_navigation_drawer()
        self.bind(size=self.on_size)

    def on_size(self, *args):
        size = self.app.root.width
        # Navigate through the new ScreenManager structure: RootScreenManager -> MainScreen -> MainWidget
        main_screen = self.app.root.get_screen("main_screen")
        if main_screen and main_screen.children:
            main_widget = main_screen.children[0]
            nav_drawer = main_widget.ids.nav_drawer
            top_left_button = main_widget.ids.menu_button
        else:
            # Fallback for cases where the structure isn't ready yet
            return
        if size > 1200 + nav_drawer.width:
            if not top_left_button.disabled:
                nav_drawer.drawer_type = 'standard'
                nav_drawer.radius = (0, 0, 0, 0)
                # nav_drawer.md_bg_color = self.app.theme_cls.primaryContainerColor
                nav_drawer.set_state("open")
                top_left_button.opacity = 0
                top_left_button.disabled = True
        elif nav_drawer.state == "open":
            nav_drawer.set_state("close")
            nav_drawer.radius = (0, dp(16), dp(16), 0)
            if top_left_button.disabled:
                top_left_button.opacity = 1
            top_left_button.disabled = False
            nav_drawer.drawer_type = 'modal'

