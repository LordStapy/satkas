"""
Contract Address Field Component - Address display with status and payment
"""

from kivy.animation import Animation
from kivy.properties import StringProperty, BooleanProperty, NumericProperty
from kivy.metrics import dp
from kivymd.uix.card import MDCard
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.label import MDLabel
from kivymd.uix.widget import MDWidget
from kivymd.uix.button import MDIconButton


class ContractAddressField(MDCard):
    """Contract address display with status and QR code."""
    
    contract_address = StringProperty("")
    contract_address_display = StringProperty("")  # Multi-row formatted
    title_text = StringProperty("Contract Address")
    status_text = StringProperty("Waiting...")
    funded_amount = NumericProperty(0)
    is_confirmed = BooleanProperty(False)
    is_visible = BooleanProperty(False)
    
    # Reference to parent screen
    screen = None
    
    def show(self):
        """Show contract field with animation."""
        self.is_visible = True
        anim = Animation(opacity=1, duration=0.3)
        anim.start(self)
    
    def hide(self):
        """Hide contract field with animation."""
        self.is_visible = False
        anim = Animation(height=0, opacity=0, duration=0.3)
        anim.start(self)
    
    def show_qr_popup(self):
        """Show contract address QR code."""
        if self.screen and self.contract_address:
            self.screen.show_contract_qr_popup(self.contract_address)
    
    def format_address_for_display(self, address):
        """Split address into 2 rows."""
        if not address:
            return "N/A"
        offset = len(address) // 2 + 1
        return address[0:offset] + "\n" + address[offset:]
    
    def on_contract_address(self, instance, value):
        """Update display format when address changes."""
        self.contract_address_display = value  #self.format_address_for_display(value)
    
    def reset(self):
        """Reset all properties to default values."""
        self.contract_address = ""
        self.contract_address_display = ""
        self.title_text = "Contract Address"
        self.status_text = "Waiting..."
        self.funded_amount = 0
        self.is_confirmed = False
        self.is_visible = False
        self.ids.contract_status_label.text_color = self.screen.app.theme_cls.primaryColor

