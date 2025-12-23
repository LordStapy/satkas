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


class PaymentField(MDCard):
    """
    Unified payment field for both swap directions.
    
    Shows appropriate payment buttons based on:
    - swap_direction: "kas2sat" (pay KAS) or "sat2kas" (pay BTC)
    - is_wallet_external: True = only external button, False = both buttons
    """
    
    swap_direction = StringProperty("kas2sat")
    is_wallet_external = BooleanProperty(False)
    payment_enabled = BooleanProperty(False)
    is_visible = BooleanProperty(False)
    buttons_row_visible = BooleanProperty(True)
    
    title_text = StringProperty("Payment")
    info_text = StringProperty("")
    
    # Reference to parent screen
    screen = None
    
    def show(self):
        """Show payment field with animation."""
        self.is_visible = True
        anim = Animation(opacity=1, duration=0.3)
        anim.start(self)
    
    def hide(self):
        """Hide payment field with animation."""
        self.is_visible = False
        self.payment_enabled = False
        anim = Animation(height=0, opacity=0, duration=0.3)
        anim.start(self)
    
    def pay_with_internal_wallet(self):
        """Pay with internal wallet (KAS or BTC depending on direction)."""
        if not self.screen:
            return
        
        if self.swap_direction == "kas2sat":
            # Pay KAS to contract
            self.screen.pay_with_kaspa_wallet()
        else:
            # Pay BTC invoice
            self.screen.pay_with_ln_wallet()
    
    def pay_with_external_wallet(self):
        """Pay with external wallet (KAS or BTC depending on direction)."""
        if not self.screen:
            return
        
        if self.swap_direction == "kas2sat":
            # Open external Kaspa wallet
            self.screen.pay_with_external_kaspa_wallet()
        else:
            # Open external LN wallet
            self.screen.pay_with_external_ln_wallet()
    
    def reset(self):
        """Reset all properties to default values."""
        self.swap_direction = "kas2sat"
        self.is_wallet_external = False
        self.payment_enabled = False
        self.is_visible = False
        self.title_text = "Payment"
        self.info_text = ""
        self.ids.payment_info_label.text_color = self.screen.app.theme_cls.primaryColor
        self.buttons_row_visible = True

