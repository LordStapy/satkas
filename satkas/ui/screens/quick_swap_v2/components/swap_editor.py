"""
Swap Editor Component - Dual-card editable amount selector
"""

import math
from kivy.animation import Animation
from kivy.properties import StringProperty, NumericProperty, BooleanProperty, ObjectProperty
from kivy.metrics import dp
from kivymd.uix.card import MDCard
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.label import MDLabel, MDIcon
from kivymd.uix.textfield import MDTextField, MDTextFieldHintText
from kivymd.uix.button import MDIconButton


class SwapEditor(MDCard):
    """
    Editable swap amount selector with direction toggle.
    
    Features:
    - Two editable amount fields (send/receive)
    - Auto-calculation when either field is edited
    - Direction toggle button in middle
    - Collapses to 1-row summary when swap starts
    """
    
    # Swap direction
    swap_direction = StringProperty("kas2sat")  # "kas2sat" or "sat2kas"

    # Swap amounts (formatted)
    send_amount_display = StringProperty("")
    receive_amount_display = StringProperty("")
    
    # Amounts (always stored in their respective currencies)
    kas_amount = NumericProperty(0)
    btc_amount = NumericProperty(0)  # In BTC (not sats)
    
    # Anchor field - which amount should remain fixed during rate updates
    anchor_field = StringProperty("none")  # "kas", "btc", or "none"
    
    # Rates
    kas2sat_rate = NumericProperty(0)  # sats per KAS
    sat2kas_rate = NumericProperty(0)  # sats per KAS
    
    # Summary display
    summary_display = StringProperty("")
    
    # Display state
    is_editing = BooleanProperty(True)  # True = editor mode, False = collapsed summary
    is_valid = BooleanProperty(False)  # Can start swap?
    is_fetching_rate = BooleanProperty(False)  # True when waiting for rate update
    is_collapsed = BooleanProperty(False)  # True when swap is ongoing (prevents maker queries)
    
    # Reference to parent screen
    screen = ObjectProperty(None)
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
    
    def get_current_rate(self):
        """Get rate for current direction."""
        return self.kas2sat_rate if self.swap_direction == "kas2sat" else self.sat2kas_rate
    
    def switch_direction(self):
        """Toggle swap direction and swap the fields."""
        if self.swap_direction == "kas2sat":
            self.swap_direction = "sat2kas"
        else:
            self.swap_direction = "kas2sat"
        
        # Preserve anchor_field (Option B - anchor stays fixed across direction switch)
        # This allows amounts (including those from invoices) to remain anchored
        
        # Clear invoice if switching to sat2kas (invoice was for receiving BTC in kas2sat)
        if self.screen and self.swap_direction == "sat2kas":
            invoice_field = self.screen.ids.invoice_field
            if invoice_field.invoice:
                invoice_field.invoice = ""  # Clear stored invoice
        
        # Recalculate with new direction
        self.calculate_amounts()
        self.get_send_amount_display()
        self.get_receive_amount_display()
        self.validate()
        
        # Update peer rate info and refresh rates for new direction
        if self.screen:
            peer_selector = self.screen.ids.peer_selector
            # Show cached rate immediately
            peer_selector.update_status(peer_selector.peer_status, editor=self)
            # Restart refresh to prioritize fetching current direction's rate
            self.screen.restart_rate_refresh()

    def on_amount_input(self, field, text):
        try:
            # Validate and sanitize input
            amount = float(text) if text else 0.0
            # Prevent negative values
            if amount < 0:
                amount = 0.0
        except ValueError:
            amount = 0.0
        
        if self.ids.send_input == field and field.focus:
            if self.swap_direction == "kas2sat":
                self.kas_amount = amount
                # Set anchor based on which field user is typing
                self.anchor_field = "kas" if amount > 0 else "none"
                self.calculate_btc_from_kas()
            else:
                self.btc_amount = amount
                # Set anchor based on which field user is typing
                self.anchor_field = "btc" if amount > 0 else "none"
                self.calculate_kas_from_btc()
            self.get_send_amount_display()
            self.get_receive_amount_display()
        elif self.ids.receive_input == field and field.focus:
            if self.swap_direction == "kas2sat":
                self.btc_amount = amount
                # Set anchor based on which field user is typing
                self.anchor_field = "btc" if amount > 0 else "none"
                self.calculate_kas_from_btc()
            else:
                self.kas_amount = amount
                # Set anchor based on which field user is typing
                self.anchor_field = "kas" if amount > 0 else "none"
                self.calculate_btc_from_kas()
            self.get_send_amount_display()
            self.get_receive_amount_display()
        self.validate()
        
        # Trigger debounced rate refresh for volume-based pricing (only when user is typing)
        # Skip if swap is ongoing (collapsed)
        if self.screen and self.kas_amount > 0 and field.focus and not self.is_collapsed:
            self.is_fetching_rate = True  # Immediately mark as fetching
            self.screen.debounced_rate_refresh()
    
    def calculate_btc_from_kas(self):
        """Calculate BTC amount from KAS amount."""
        rate = self.get_current_rate()
        if rate > 0 and self.kas_amount > 0:
            if self.swap_direction == "kas2sat":
                # User sends KAS, receives BTC
                sats = math.floor(self.kas_amount * rate)
            else:
                # User receives KAS, sends BTC
                sats = math.ceil(self.kas_amount * rate)
            self.btc_amount = sats / 1e8
        else:
            # Clear BTC amount if rate is invalid
            self.btc_amount = 0
    
    def calculate_kas_from_btc(self):
        """Calculate KAS amount from BTC amount."""
        rate = self.get_current_rate()
        if rate > 0 and self.btc_amount > 0:
            sats = self.btc_amount * 1e8
            if self.swap_direction == "kas2sat":
                self.kas_amount = math.ceil(sats / rate * 1000) / 1000 # we round UP to 3 decimal places to make the number more readable
            else:
                self.kas_amount = math.floor(sats / rate * 1000) / 1000 # we round DOWN to 3 decimal places to make the number more readable
        else:
            # Clear KAS amount if rate is invalid
            self.kas_amount = 0

    def calculate_amounts(self):
        """Recalculate amounts based on anchor field."""
        if self.anchor_field == "btc" and self.btc_amount > 0:
            # BTC is fixed, recalculate KAS (e.g., from invoice)
            self.calculate_kas_from_btc()
        elif self.anchor_field == "kas" and self.kas_amount > 0:
            # KAS is fixed, recalculate BTC
            self.calculate_btc_from_kas()
        elif self.kas_amount > 0:
            # Fallback: prefer KAS (original behavior)
            self.calculate_btc_from_kas()
        elif self.btc_amount > 0:
            # Last resort: use BTC
            self.calculate_kas_from_btc()
        # If both are 0, do nothing
    
    def validate(self):
        """Check if amounts are valid for starting swap."""
        has_amounts = self.kas_amount > 0 and self.btc_amount > 0
        has_rate = self.get_current_rate() > 0
        self.is_valid = has_amounts and has_rate
    
    def format_kas(self, amount):
        """Format KAS amount with stripped trailing zeros.
        
        Context: KAS is worth cents, so decimals are for transparency.
        We strip trailing zeros for cleaner display.
        """
        if amount <= 0:
            return ""
        return f"{amount:.8f}".rstrip('0').rstrip('.')
    
    def format_btc(self, amount):
        """Format BTC amount with ALL 8 decimals (sats precision).
        
        Context: Given exchange rates (1 BTC = 1-2M KAS), we're dealing with
        satoshi-range amounts. All decimals shown intentionally to prevent
        misreading and potential exploits. Dynamic precision would be dangerous.
        """
        if amount <= 0:
            return ""
        return f"{amount:.8f}"
    
    def get_send_amount_display(self):
        """Get formatted send amount."""
        if self.swap_direction == "kas2sat":
            self.send_amount_display = self.format_kas(self.kas_amount)
        else:
            self.send_amount_display = self.format_btc(self.btc_amount)
    
    def get_receive_amount_display(self):
        """Get formatted receive amount."""
        # Don't show receive amount if rate is invalid (e.g., amount exceeds maker's offer)
        if self.get_current_rate() <= 0:
            self.receive_amount_display = ""
            return
        
        if self.swap_direction == "kas2sat":
            self.receive_amount_display = self.format_btc(self.btc_amount)
        else:
            self.receive_amount_display = self.format_kas(self.kas_amount)
    
    def get_summary_display(self):
        """Get collapsed summary display."""
        send_currency = "KAS" if self.swap_direction == "kas2sat" else "BTC"
        receive_currency = "BTC" if self.swap_direction == "kas2sat" else "KAS"
        send = self.send_amount_display if self.send_amount_display else "—"
        receive = self.receive_amount_display if self.receive_amount_display else "—"
        self.summary_display = f"{send} {send_currency} -> {receive} {receive_currency}"
    
    def collapse(self):
        """Collapse to summary view with animation."""
        self.is_editing = False
        self.is_collapsed = True  # Mark as collapsed to prevent maker queries
        # Update all display strings before showing summary
        self.get_send_amount_display()
        self.get_receive_amount_display()
        self.get_summary_display()
        anim = Animation(height=dp(60), duration=0.5)
        anim.start(self)
    
    def expand(self):
        """Expand to editor view with animation."""
        self.is_editing = True
        anim = Animation(height=dp(244), duration=0.5)
        anim.start(self)
    
    def on_kas2sat_rate(self, instance, value):
        """Recalculate when rate changes."""
        self.calculate_amounts()
        self.get_send_amount_display()
        self.get_receive_amount_display()
        self.validate()
        # Clear fetching flag if the rates were updated for kas2sat direction
        if self.swap_direction == "kas2sat":
            self.is_fetching_rate = False  # Rate updated, clear fetching flag
    
    def on_sat2kas_rate(self, instance, value):
        """Recalculate when rate changes."""
        self.calculate_amounts()
        self.get_send_amount_display()
        self.get_receive_amount_display()
        self.validate()
        # Clear fetching flag if the rates were updated for sat2kas direction
        if self.swap_direction == "sat2kas":
            self.is_fetching_rate = False  # Rate updated, clear fetching flag
    
    def reset(self):
        """Reset all properties to default values."""
        # self.swap_direction = "kas2sat"  # no reset on swap direction
        self.send_amount_display = ""
        self.receive_amount_display = ""
        self.kas_amount = 0
        self.btc_amount = 0
        self.anchor_field = "none"
        self.kas2sat_rate = 0
        self.sat2kas_rate = 0
        self.summary_display = ""
        self.is_editing = True
        self.is_valid = False
        self.is_fetching_rate = False
        self.is_collapsed = False
        self.ids.editor_collapsed_icon.icon = "check-circle"
        self.ids.editor_collapsed_icon.text_color = (0, 0.8, 0, 1)

