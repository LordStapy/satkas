import asyncio
import os

from kivy.animation import Animation
from kivy.app import App
from kivy.lang import Builder
from kivy.metrics import dp
from kivy.properties import AliasProperty, ColorProperty, NumericProperty, ObjectProperty
from kivymd.uix.card import MDCard
from kivymd.uix.gridlayout import MDGridLayout
from kivymd.uix.boxlayout import MDBoxLayout

from satkas.ui.screens.base_responsive_layout import (
    BaseResponsiveLayout,
    DesktopLayout,
    TabletLayout,
    MobileLayout
)


_SERVICE_KEYS = (
    "tor_service",
    "kaspad_service",
    "kaspa_wallet_service",
    "ln_wallet_service",
    "bitcoin_service",
    "btc_wallet_service",
)

ICON_STYLES = {
    "tor": (0.49, 0.27, 0.60, 1),
    "kaspa": (0.29, 0.92, 0.80, 1),
    "bitcoin": (0.97, 0.58, 0.10, 1),
    "lightning": (1.00, 0.69, 0.13, 1),
    "lnbits": (0.94, 0.07, 0.75, 1),
}

_DOT_IDLE = (0.55, 0.55, 0.55, 1)
_DOT_ERROR = (0.8, 0, 0, 1)
_DOT_OK_DIM = (0.18, 0.62, 0.28, 1)
_DOT_OK_BRIGHT = (0.22, 1.0, 0.32, 1)
_PULSE_S = 1.5


class Dashboard(MDBoxLayout):
    tor_service = ObjectProperty(None)
    kaspad_service = ObjectProperty(None)
    kaspa_wallet_service = ObjectProperty(None)
    ln_wallet_service = ObjectProperty(None)
    bitcoin_service = ObjectProperty(None)
    btc_wallet_service = ObjectProperty(None)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._sm = App.get_running_app().service_manager
        self._sm.register_observer(self._on_services_changed)
        self._pull_services()

    def _on_services_changed(self, changed):
        self._pull_services(changed)

    def _pull_services(self, keys=None):
        for key in keys or _SERVICE_KEYS:
            setattr(self, key, getattr(self._sm, key))


class AdaptiveCardGrid(MDGridLayout):
    """Grid that sizes itself to N capped cards and recenters in the parent."""

    max_card_width = NumericProperty(dp(360))
    max_cols = NumericProperty(3)

    def on_parent(self, instance, parent):
        # Must watch the parent: size_hint_x is None, so our own width is
        # assigned here and cannot be the resize signal (that would cycle).
        if parent:
            parent.bind(width=self._reflow)
            self._reflow()

    def _reflow(self, *_args):
        parent = self.parent
        if not parent:
            return
        spacing = self.spacing[0]
        avail = parent.width
        cols = min(
            max(len(self.children), 1),
            int(self.max_cols),
            max(1, int((avail + spacing) // (self.max_card_width + spacing))),
        )
        self.cols = cols
        self.width = min(avail, cols * self.max_card_width + (cols - 1) * spacing)


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
            'can_refresh': False,
            'service_icon': 'help-circle-outline',
            'icon_style': '',
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
    service_can_refresh = AliasProperty(
        lambda self: bool(getattr(self.service, 'can_refresh', False)),
        bind=['service'],
        cache=False,
    )
    service_icon = AliasProperty(
        lambda self: getattr(self.service, 'service_icon', 'help-circle-outline'),
        bind=['service'],
        cache=False,
    )
    service_icon_color = AliasProperty(
        lambda self: ICON_STYLES.get(
            getattr(self.service, 'icon_style', ''),
            (1, 1, 1, 1),
        ),
        bind=['service'],
        cache=False,
    )
    status_dot_icon = AliasProperty(
        lambda self: 'circle' if self.service_is_enabled else 'circle-outline',
        bind=['service_is_enabled'],
        cache=False,
    )
    status_dot_display_color = ColorProperty(_DOT_IDLE)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._service_observer = None
        self._status_pulse_anim = None

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
                'can_refresh': 'service_can_refresh',
                'service_icon': 'service_icon',
                'icon_style': 'service_icon_color',
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
        self._sync_status_pulse()

    def on_service_is_enabled(self, *args):
        self._sync_status_pulse()

    def on_service_is_detected(self, *args):
        self._sync_status_pulse()

    def _stop_status_pulse(self):
        if self._status_pulse_anim is not None:
            self._status_pulse_anim.cancel(self)
            self._status_pulse_anim = None

    def _sync_status_pulse(self, *args):
        self._stop_status_pulse()
        if "External" in getattr(self.service, "service_name", ""):
            self.status_dot_display_color = _DOT_IDLE
        elif self.service_is_enabled and self.service_is_detected:
            self.status_dot_display_color = _DOT_OK_DIM
            anim = (
                Animation(
                    status_dot_display_color=_DOT_OK_BRIGHT,
                    duration=_PULSE_S,
                    t="in_out_sine",
                )
                + Animation(
                    status_dot_display_color=_DOT_OK_DIM,
                    duration=_PULSE_S,
                    t="in_out_sine",
                )
            )
            anim.repeat = True
            self._status_pulse_anim = anim
            anim.start(self)
        elif self.service_is_enabled:
            self.status_dot_display_color = _DOT_ERROR
        else:
            self.status_dot_display_color = _DOT_IDLE

    def refresh_service(self, *args):
        asyncio.create_task(self.service.update_status(run_once=True))


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
