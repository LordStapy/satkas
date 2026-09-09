import os

from kivymd.uix.screen import MDScreen
from kivy.app import App
from kivy.lang import Builder


Builder.load_file(os.path.join(os.path.dirname(__file__), 'mobile_layout.kv'))


class MobileLayout(MDScreen):
    """Mobile layout with simple label."""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.app = App.get_running_app()
    
    def on_layout_activated(self):
        """Called when this layout becomes active."""
        print("📱 Mobile layout activated - setting navigation drawer to modal")
        # Setup navigation drawer when layout becomes active
        self._setup_navigation_drawer()
    
    def _setup_navigation_drawer(self):
        """Setup navigation drawer for mobile layout."""
        
        if self.app and hasattr(self.app, 'root') and hasattr(self.app.root, 'ids') and 'nav_drawer' in self.app.root.ids:
            nav_drawer = self.app.root.ids.nav_drawer
            # Set drawer type to modal for mobile
                
            nav_drawer.drawer_type = "modal"
            # Close the drawer for mobile
            nav_drawer.set_state("close")
            # print(f"📱 Mobile layout: Navigation drawer set to modal type and closed (current type: {nav_drawer.drawer_type})")
        else:
            print("❌ Mobile layout: Could not access navigation drawer")
