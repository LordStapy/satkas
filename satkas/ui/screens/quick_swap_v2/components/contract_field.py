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

    # Full address only (or ""). Do not slice, wrap, or ellipsize these:
    # the label's allow_copy copies .text, which is contract_address_display.
    contract_address = StringProperty("")
    contract_address_display = StringProperty("")
    chain = StringProperty("kas")  # "kas" or "btc"; snackbar wording
    title_text = StringProperty("Contract Address")
    description_text = StringProperty("")  # Who funds this contract
    status_text = StringProperty("Waiting...")
    funded_amount = NumericProperty(0)
    is_confirmed = BooleanProperty(False)
    is_visible = BooleanProperty(False)
    
    # Reference to parent screen
    screen = None
    _copy_flash_anim = None
    
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
            self.screen.show_contract_qr_popup(
                self.contract_address, chain=self.chain,
            )
    
    # def format_address_for_display(self, address):
    #     """Split address into 2 rows."""
    #     if not address:
    #         return "N/A"
    #     offset = len(address) // 2 + 1
    #     return address[0:offset] + "\n" + address[offset:]
    
    def on_contract_address(self, instance, value):
        """Keep display text identical to the full address (allow_copy source)."""
        self.contract_address_display = value

    def on_address_copied(self, *args):
        """Flash + snackbar after MDLabel.allow_copy has written the clipboard."""
        if not self.contract_address:
            return
        self._flash_address_label()
        if self.screen:
            self.screen.show_address_copied_snackbar(self.chain)

    def _flash_address_label(self):
        label = self.ids.contract_address_label
        theme = self.screen.app.theme_cls if self.screen else None
        if theme is None:
            return
        if self._copy_flash_anim is not None:
            self._copy_flash_anim.cancel(label)
            self._copy_flash_anim = None
            label.theme_text_color = "Secondary"
        rest = tuple(label.color)
        label.theme_text_color = "Custom"
        label.text_color = theme.primaryColor
        anim = Animation(text_color=rest, duration=0.4)

        def _restore(*_):
            self._copy_flash_anim = None
            label.theme_text_color = "Secondary"

        anim.bind(on_complete=_restore)
        self._copy_flash_anim = anim
        anim.start(label)

    def reset(self):
        """Reset all properties to default values."""
        self.contract_address = ""
        self.contract_address_display = ""
        self.title_text = "Contract Address"
        self.description_text = ""
        self.status_text = "Waiting..."
        self.funded_amount = 0
        self.is_confirmed = False
        self.is_visible = False
        self.ids.contract_status_label.text_color = self.screen.app.theme_cls.primaryColor
