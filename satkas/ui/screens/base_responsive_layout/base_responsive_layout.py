from kivy.clock import Clock
from kivymd.uix.responsivelayout import MDResponsiveLayout
from kivymd.uix.screen import MDScreen
from .desktop import DesktopLayout
from .tablet import TabletLayout
from .mobile import MobileLayout


class BaseResponsiveLayout(MDResponsiveLayout, MDScreen):
    """
    Base class for responsive layouts using KivyMD's MDResponsiveLayout.
    Automatically switches between desktop, tablet, and mobile layouts
    based on screen size.
    """
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.mobile_view = MobileLayout()
        self.tablet_view = TabletLayout()
        self.desktop_view = DesktopLayout()

        self._initialized = False
        Clock.schedule_once(self._finish_initialization, 1.5)

    def _finish_initialization(self, *args):
        self._initialized = True
        self.set_screen()

    def on_change_screen_type(self, *args):
        if not self._initialized:
            return
        # print(f"Layout switched: {args[0].upper()} ({self.size[0]}x{self.size[1]})")
        match args[0]:
            case 'mobile':
                layout = self.mobile_view
            case 'tablet':
                layout = self.tablet_view
            case 'desktop':
                layout = self.desktop_view
            case _:
                # print(args)
                return
        layout.on_layout_activated()

    @property
    def current_view(self):
        match self.real_device_type:
            case 'mobile':
                cls = self.mobile_view
            case 'tablet':
                cls = self.tablet_view
            case 'desktop':
                cls = self.desktop_view
            case _:
                cls = None
        return cls

