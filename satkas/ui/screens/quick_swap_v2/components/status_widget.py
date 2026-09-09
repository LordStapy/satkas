"""
Status Widget Component - Status display with refund and cancel
"""

# import asyncio  # unused after phase 5: refund trigger commented out
from kivy.animation import Animation
from kivy.properties import StringProperty, BooleanProperty, NumericProperty
from kivy.metrics import dp
from kivymd.uix.card import MDCard
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.label import MDLabel
from kivymd.uix.button import MDIconButton, MDButton, MDButtonIcon, MDButtonText
from kivymd.uix.widget import MDWidget
from kivymd.uix.divider import MDDivider


class StatusWidget(MDCard):
    """Status display with countdown, refund, and cancel."""
    
    # Status info
    status_text = StringProperty("Initializing...")
    countdown_text = StringProperty("")
    
    # Swap state tracking
    swap_direction = StringProperty("kas2sat")
    is_expired = BooleanProperty(False)
    is_funded = BooleanProperty(False)
    txid = StringProperty("")
    
    # Refund state
    show_refund = BooleanProperty(False)
    refund_enabled = BooleanProperty(False)
    refund_triggered = BooleanProperty(False)
    refund_countdown_text = StringProperty("")
    
    # Visibility
    is_visible = BooleanProperty(False)
    is_focused = BooleanProperty(False)
    
    # Reference to parent screen
    screen = None
    
    def show(self):
        """Show status widget with animation."""
        self.is_visible = True
        anim = Animation(opacity=1, duration=0.3)
        anim.start(self)
    
    def hide(self):
        """Hide status widget with animation."""
        self.is_visible = False
        self.reset()
        anim = Animation(height=0, opacity=0, duration=0.3)
        anim.start(self)
    
    def reset(self):
        """Reset all properties to default values."""
        self.status_text = "Initializing..."
        self.countdown_text = ""
        self.swap_direction = "kas2sat"
        self.is_expired = False
        self.is_funded = False
        self.show_refund = False
        self.refund_enabled = False
        self.refund_triggered = False
        self.refund_countdown_text = ""
        self.txid = ""
        self.is_visible = False
        self.is_focused = False
        self.ids.status_info_label.text_color = self.screen.app.theme_cls.primaryColor
    
    def update_refund_visibility(self):
        """Refund is automatic in the orchestrator; the button stays hidden.

        Commented during phase 5 of the taker/controller integration:
        show refund only if kas2sat + expired + funded.
        """
        self.show_refund = False
        # self.show_refund = (
        #     self.swap_direction == "kas2sat" and
        #     self.is_expired and
        #     self.is_funded and
        #     not self.refund_triggered
        # )

    def trigger_refund(self):
        """No-op: refund is automatic. Kept so the KV binding does not break."""
        # Commented during phase 5 of the taker/controller integration.
        # if not self.refund_enabled or self.refund_triggered:
        #     return
        # asyncio.create_task(self._trigger_refund())
        return

    # async def _trigger_refund(self):
    #     await self.screen.handle_output_address(refund=True)
    #     self.refund_triggered = True
    #     self.refund_enabled = False
    #     self.status_text = "Refunding..."
    #     if self.screen:
    #         self.screen.refund_contract(self.screen.output_address)

