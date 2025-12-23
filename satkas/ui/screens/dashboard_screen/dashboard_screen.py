import asyncio
import os

from kivy.app import App
from kivy.lang import Builder
from kivymd.uix.card import MDCard
from kivy.properties import ObjectProperty, StringProperty, BooleanProperty, AliasProperty

from satkas.ui.screens.base_responsive_layout import (
    BaseResponsiveLayout,
    DesktopLayout,
    TabletLayout,
    MobileLayout
)


Builder.load_file(os.path.join(os.path.dirname(__file__), 'dashboard_screen.kv'))


class DashboardScreenDesktop(DesktopLayout):
    pass


class DashboardScreenTablet(TabletLayout):
    pass


class DashboardScreenMobile(MobileLayout):
    pass


class ServiceCard(MDCard):
    service = ObjectProperty(
        type('Service', (), {
            'service_name': '',
            'service_status_string': '',
            'is_enabled': False,
            'is_detected': False,
        }),        
        rebind=True)

    service_name = AliasProperty(
        lambda self: getattr(self.service, 'service_name', ''),
        lambda self, value: setattr(self.service, 'service_name', value),
        bind=['service'],
        cache=False,
    )
    service_status_string = AliasProperty(
        lambda self: getattr(self.service, 'service_status_string', ''),
        lambda self, value: setattr(self.service, 'service_status_string', value),
        bind=['service'],
        cache=False,
    )
    service_is_enabled = AliasProperty(
        lambda self: getattr(self.service, 'is_enabled', False),
        lambda self, value: setattr(self.service, 'is_enabled', value),
        bind=['service'],
        cache=False,
    )
    service_is_detected = AliasProperty(
        lambda self: getattr(self.service, 'is_detected', False),
        lambda self, value: setattr(self.service, 'is_detected', value),
        bind=['service'],
        cache=False,
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._service_observer = None

    def on_service(self, *args):
        # Unsubscribe from previous service
        if self._service_observer and hasattr(self, 'service') and hasattr(self.service, 'unregister_observer'):
            try:
                self.service.unregister_observer(self._service_observer)
            except Exception:
                pass

        # Define observer callback
        def _observer(changed_fields):
            field_map = {
                'service_name': 'service_name',
                'service_status_string': 'service_status_string',
                'is_enabled': 'service_is_enabled',
                'is_detected': 'service_is_detected',
            }
            for f in changed_fields or []:
                prop_name = field_map.get(f)
                if prop_name:
                    try:
                        self.property(prop_name).dispatch(self)
                    except Exception:
                        pass

        self._service_observer = _observer
        # Subscribe to new service
        if hasattr(self.service, 'register_observer'):
            try:
                self.service.register_observer(self._service_observer)
            except Exception:
                pass

    def refresh_service(self, *args):
        asyncio.create_task(self.service.update_status(run_once=True))

    def enable_service(self, *args):
        if self.service.is_enabled:
            print("Service already enabled")
            return
        self.service.enable()   


class DashboardScreen(BaseResponsiveLayout):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.app = App.get_running_app()

        self.mobile_view = DashboardScreenMobile()
        self.tablet_view = DashboardScreenTablet()
        self.desktop_view = DashboardScreenDesktop()


        self.active_task = None

    def on_enter(self):
        self.app.root.get_screen('main_screen').children[0].ids.top_bar_title.text = "Dashboard"

    def on_leave(self):
        if self.active_task:
            self.active_task.cancel()
            self.active_task = None
