import os

from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.label import MDLabel
from kivymd.uix.screen import MDScreen
from kivy.metrics import dp
from kivy.clock import Clock
from kivy.app import App
from kivy.lang import Builder


Builder.load_file(os.path.join(os.path.dirname(__file__), 'tablet_layout.kv'))


class TabletLayout(MDScreen):
    """Tablet layout with simple label."""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.app = App.get_running_app()
    
    def on_layout_activated(self):
        """Called when this layout becomes active."""
        # print("📱 Tablet layout activated - setting navigation drawer to modal")
        # Setup navigation drawer when layout becomes active
        self._setup_navigation_drawer()
    
    def _setup_navigation_drawer(self):
        """Setup navigation drawer for tablet layout."""
        # Get the app instance
        
        if self.app and hasattr(self.app, 'root') and hasattr(self.app.root, 'ids') and 'nav_drawer' in self.app.root.ids:
            nav_drawer = self.app.root.ids.nav_drawer
            # print(f"{nav_drawer.drawer_type} {nav_drawer.state}")
            # Set drawer type to modal for tablet
            nav_drawer.set_state("close")
            nav_drawer.drawer_type = "modal"
            # Close the drawer for tablet
            # print(f"📱 Tablet layout: Navigation drawer set to modal type and closed (current type: {nav_drawer.drawer_type})")
        else:
            print("❌ Tablet layout: Could not access navigation drawer")
