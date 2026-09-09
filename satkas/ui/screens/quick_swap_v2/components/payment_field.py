"""
Payment Field Component - Unified payment actions for both swap directions
"""

from kivy.animation import Animation
from kivy.properties import StringProperty, BooleanProperty
from kivy.metrics import dp
from kivymd.uix.card import MDCard
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.label import MDLabel
from kivymd.uix.widget import MDWidget
from kivymd.uix.button import MDButton, MDButtonIcon, MDButtonText

from satkas.ui.screens.quick_swap_v2.components.attention_pulse import AttentionPulseMixin


class PaymentField(AttentionPulseMixin, MDCard):
    """
    Unified payment field for both swap directions.

    Shows appropriate payment buttons based on:
    - swap_direction: kas2sat/kas2btc (pay KAS), sat2kas (pay LN), btc2kas (pay on-chain BTC)
    - is_wallet_external: True = only external button, False = both buttons
    """

    swap_direction = StringProperty("kas2sat")
    is_wallet_external = BooleanProperty(False)
    payment_enabled = BooleanProperty(False)
    is_visible = BooleanProperty(False)
    buttons_row_visible = BooleanProperty(True)

    title_text = StringProperty("Payment")
    info_text = StringProperty("")
    internal_button_text = StringProperty("Pay with Wallet")
    external_button_text = StringProperty("Pay External")
    internal_button_icon = StringProperty("wallet")

    # Reference to parent screen
    screen = None

    def show(self):
        """Show payment field with animation."""
        self.is_visible = True
        anim = Animation(opacity=1, duration=0.3)
        anim.start(self)

    def hide(self):
        """Hide payment field with animation."""
        self.stop_attention_pulse()
        self.is_visible = False
        self.payment_enabled = False
        anim = Animation(height=0, opacity=0, duration=0.3)
        anim.start(self)

    def configure_for_direction(self, direction: str, is_external: bool):
        """Set labels/icons for the active swap direction."""
        self.swap_direction = direction
        self.is_wallet_external = is_external
        if direction in ("kas2sat", "kas2btc"):
            self.internal_button_text = "Pay with Kaspa Wallet"
            self.external_button_text = "Pay External (KAS)"
            self.internal_button_icon = "wallet"
        elif direction == "sat2kas":
            self.internal_button_text = "Pay with LN Wallet"
            self.external_button_text = "Pay External (BTC)"
            self.internal_button_icon = "lightning-bolt"
        elif direction == "btc2kas":
            self.internal_button_text = "Pay with BTC Wallet"
            self.external_button_text = "Pay External (BTC)"
            self.internal_button_icon = "bitcoin"
        elif direction == "kas2btc":
            # redeem is handled separately; funding is KAS
            pass

    def pay_with_internal_wallet(self):
        """Pay with internal wallet (KAS, LN, or BTC depending on direction)."""
        if not self.screen:
            return

        if self.swap_direction in ("kas2sat", "kas2btc"):
            self.screen.pay_with_kaspa_wallet()
        elif self.swap_direction == "sat2kas":
            self.screen.pay_with_ln_wallet()
        elif self.swap_direction == "btc2kas":
            self.screen.pay_with_btc_wallet()

    def pay_with_external_wallet(self):
        """Pay with external wallet (KAS or BTC depending on direction)."""
        if not self.screen:
            return

        if self.swap_direction in ("kas2sat", "kas2btc"):
            self.screen.pay_with_external_kaspa_wallet()
        elif self.swap_direction == "sat2kas":
            self.screen.pay_with_external_ln_wallet()
        elif self.swap_direction == "btc2kas":
            self.screen.pay_with_external_btc_wallet()

    def reset(self):
        """Reset all properties to default values."""
        self.stop_attention_pulse()
        self.swap_direction = "kas2sat"
        self.is_wallet_external = False
        self.payment_enabled = False
        self.is_visible = False
        self.title_text = "Payment"
        self.info_text = ""
        self.internal_button_text = "Pay with Wallet"
        self.external_button_text = "Pay External"
        self.internal_button_icon = "wallet"
        self.ids.payment_info_label.text_color = self.screen.app.theme_cls.primaryColor
        self.buttons_row_visible = True
