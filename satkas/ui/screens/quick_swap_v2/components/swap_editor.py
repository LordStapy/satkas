"""
Swap Editor Component - Dual-card editable amount selector
"""

import math
import time
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
    
    # Swap mode and direction
    swap_mode = StringProperty("offchain")  # "offchain" | "onchain"
    # Direction: kas2sat/sat2kas (offchain) or kas2btc/btc2kas (onchain)
    swap_direction = StringProperty("kas2sat")
    # True when user sends KAS (kas2sat / kas2btc)
    sends_kas = BooleanProperty(True)

    # Swap amounts (formatted)
    send_amount_display = StringProperty("")
    receive_amount_display = StringProperty("")
    
    # Amounts (always stored in their respective currencies)
    kas_amount = NumericProperty(0)
    btc_amount = NumericProperty(0)  # In BTC (not sats)
    
    # Anchor field - which amount should remain fixed during rate updates
    anchor_field = StringProperty("none")  # "kas", "btc", or "none"
    
    # Rates (sats per KAS) — separate for off-chain and on-chain offers
    kas2sat_rate = NumericProperty(0)
    sat2kas_rate = NumericProperty(0)
    kas2btc_rate = NumericProperty(0)
    btc2kas_rate = NumericProperty(0)
    
    # Summary display
    summary_display = StringProperty("")
    
    # Display state
    is_editing = BooleanProperty(True)  # True = editor mode, False = collapsed summary
    is_valid = BooleanProperty(False)  # Can start swap?
    is_fetching_rate = BooleanProperty(False)  # True when waiting for quote
    is_collapsed = BooleanProperty(False)  # True when swap is ongoing (prevents maker queries)
    send_text_faded = BooleanProperty(False)
    receive_text_faded = BooleanProperty(False)
    quote_failed = BooleanProperty(False)
    
    # Reference to parent screen
    screen = ObjectProperty(None)
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.last_quote = None
        self.bind(swap_direction=self._sync_sends_kas)
        self.bind(
            is_fetching_rate=self._sync_quote_fade,
            anchor_field=self._sync_quote_fade,
            sends_kas=self._sync_quote_fade,
        )
        self._sync_sends_kas(self, self.swap_direction)

    def _sync_sends_kas(self, instance, direction):
        self.sends_kas = direction in ("kas2sat", "kas2btc")

    def _sync_quote_fade(self, *args):
        """Fade the derived amount while a maker quote is in flight."""
        if not self.is_fetching_rate:
            self.send_text_faded = False
            self.receive_text_faded = False
            return
        if self.anchor_field == "kas":
            self.send_text_faded = not self.sends_kas
            self.receive_text_faded = self.sends_kas
        elif self.anchor_field == "btc":
            self.send_text_faded = self.sends_kas
            self.receive_text_faded = not self.sends_kas
        else:
            self.send_text_faded = False
            self.receive_text_faded = True

    def get_current_rate(self):
        """Get rate for current direction."""
        rates = {
            "kas2sat": self.kas2sat_rate,
            "sat2kas": self.sat2kas_rate,
            "kas2btc": self.kas2btc_rate,
            "btc2kas": self.btc2kas_rate,
        }
        return rates.get(self.swap_direction, 0)

    def set_swap_mode(self, mode: str):
        """Switch between off-chain and on-chain; remap direction pair."""
        if mode == self.swap_mode:
            return
        self.swap_mode = mode
        if mode == "onchain":
            self.swap_direction = "kas2btc" if self.sends_kas else "btc2kas"
        else:
            self.swap_direction = "kas2sat" if self.sends_kas else "sat2kas"

        # On-chain has no LN invoice
        if self.screen and mode == "onchain":
            invoice_field = self.screen.ids.invoice_field
            if invoice_field.invoice:
                invoice_field.invoice = ""

        self.last_quote = None
        self.quote_failed = False
        self.calculate_amounts()
        self.get_send_amount_display()
        self.get_receive_amount_display()
        self.validate()
        if self.screen:
            peer_selector = self.screen.ids.peer_selector
            # Show cached rate for the new mode immediately (LN vs on-chain pair)
            peer_selector.update_status(peer_selector.peer_status, editor=self)
            self.screen.restart_rate_refresh()
    
    def switch_direction(self):
        """Toggle swap direction and swap the fields."""
        if self.swap_mode == "onchain":
            self.swap_direction = "btc2kas" if self.swap_direction == "kas2btc" else "kas2btc"
        else:
            self.swap_direction = "sat2kas" if self.swap_direction == "kas2sat" else "kas2sat"
        
        # Preserve anchor_field (Option B - anchor stays fixed across direction switch)
        # This allows amounts (including those from invoices) to remain anchored
        
        # Clear invoice if switching away from kas2sat (invoice was for receiving BTC)
        if self.screen and self.swap_direction != "kas2sat":
            invoice_field = self.screen.ids.invoice_field
            if invoice_field.invoice:
                invoice_field.invoice = ""  # Clear stored invoice
        
        self.last_quote = None
        self.quote_failed = False
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
            self.quote_failed = False
            if self.sends_kas:
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
            self.quote_failed = False
            if self.sends_kas:
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
        else:
            # Programmatic text update (quote / rate), not a keystroke.
            return

        self.last_quote = None
        self.validate()
        
        # Trigger debounced rate refresh for volume-based pricing (only when user is typing)
        # Skip if swap is ongoing (collapsed)
        if self.screen and (self.kas_amount > 0 or self.btc_amount > 0) and field.focus and not self.is_collapsed:
            self.is_fetching_rate = True  # Immediately mark as fetching
            self.screen.debounced_rate_refresh()

    def on_field_focus(self, field, focus):
        if not focus or not self.quote_failed:
            return
        self.quote_failed = False
        self.get_send_amount_display()
        self.get_receive_amount_display()
    
    def calculate_btc_from_kas(self):
        """Calculate BTC amount from KAS amount."""
        rate = self.get_current_rate()
        if rate > 0 and self.kas_amount > 0:
            if self.sends_kas:
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
            if self.sends_kas:
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
    
    def apply_quote(self, quote):
        """Fill the derived field from a maker quote; keep the named/anchor amount."""
        self.last_quote = dict(quote)
        self.last_quote['swap_type'] = self.swap_direction
        price = quote.get('price') or 0
        if self.swap_direction == "kas2sat":
            self.kas2sat_rate = price
        elif self.swap_direction == "sat2kas":
            self.sat2kas_rate = price
        elif self.swap_direction == "kas2btc":
            self.kas2btc_rate = price
        elif self.swap_direction == "btc2kas":
            self.btc2kas_rate = price

        if self.anchor_field == "kas":
            self.btc_amount = quote['sat_amount'] / 1e8
        elif self.anchor_field == "btc":
            self.kas_amount = quote['kas_amount']
        else:
            self.kas_amount = quote['kas_amount']
            self.btc_amount = quote['sat_amount'] / 1e8

        self.quote_failed = False
        self.get_send_amount_display()
        self.get_receive_amount_display()
        self.is_fetching_rate = False
        self.validate()

    def clear_quote(self):
        """Drop the locked quote and the derived amount; keep the named field."""
        self.last_quote = None
        self.is_fetching_rate = False
        if self.anchor_field == "kas":
            self.btc_amount = 0
        elif self.anchor_field == "btc":
            self.kas_amount = 0
        elif self.sends_kas:
            self.btc_amount = 0
        else:
            self.kas_amount = 0
        self.quote_failed = True
        self.get_send_amount_display()
        self.get_receive_amount_display()
        self.validate()

    def validate(self):
        """Check if amounts are valid for starting swap."""
        has_amounts = self.kas_amount > 0 and self.btc_amount > 0
        q = self.last_quote
        quote_ok = (
            q
            and q.get('swap_type') == self.swap_direction
            and (q.get('valid_until') or 0) > time.time()
            and q.get('kas_amount', 0) > 0
            and q.get('sat_amount', 0) > 0
            and not self.is_fetching_rate
        )
        self.is_valid = bool(has_amounts and quote_ok)
    
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
        failed_on_send = self.quote_failed and (
            (self.anchor_field == "kas" and not self.sends_kas)
            or (self.anchor_field == "btc" and self.sends_kas)
        )
        if failed_on_send:
            self.send_amount_display = "Quote failed"
            return
        if self.sends_kas:
            self.send_amount_display = self.format_kas(self.kas_amount)
        else:
            self.send_amount_display = self.format_btc(self.btc_amount)
    
    def get_receive_amount_display(self):
        """Get formatted receive amount."""
        failed_on_receive = self.quote_failed and not (
            (self.anchor_field == "kas" and not self.sends_kas)
            or (self.anchor_field == "btc" and self.sends_kas)
        )
        if failed_on_receive:
            self.receive_amount_display = "Quote failed"
            return
        # Don't show receive amount if rate is invalid (e.g., amount exceeds maker's offer)
        if self.get_current_rate() <= 0:
            self.receive_amount_display = ""
            return
        
        if self.sends_kas:
            self.receive_amount_display = self.format_btc(self.btc_amount)
        else:
            self.receive_amount_display = self.format_kas(self.kas_amount)
    
    def get_summary_display(self):
        """Get collapsed summary display."""
        send_currency = "KAS" if self.sends_kas else "BTC"
        receive_currency = "BTC" if self.sends_kas else "KAS"
        send = self.send_amount_display if self.send_amount_display else "—"
        receive = self.receive_amount_display if self.receive_amount_display else "—"
        mode_tag = "on-chain" if self.swap_mode == "onchain" else "LN"
        if send_currency == "BTC":
            self.summary_display = f"{send} BTC ({mode_tag}) -> {receive} {receive_currency}"
        else:
            self.summary_display = f"{send} KAS -> {receive} BTC ({mode_tag})"
    
    def collapse(self):
        """Collapse to summary view with animation."""
        self.is_editing = False
        self.is_collapsed = True  # Mark as collapsed to prevent maker queries
        # Update all display strings before showing summary
        self.get_send_amount_display()
        self.get_receive_amount_display()
        self.get_summary_display()
        anim = Animation(height=dp(48), duration=0.5)
        anim.start(self)
    
    def expand(self):
        """Expand to editor view with animation."""
        self.is_editing = True
        # Mode selector row adds height when editing
        anim = Animation(height=dp(264), duration=0.5)
        anim.start(self)
    
    def on_kas2sat_rate(self, instance, value):
        """Recalculate when off-chain send-KAS rate changes."""
        if self.swap_direction == "kas2sat":
            self._on_active_rate_updated()

    def on_sat2kas_rate(self, instance, value):
        """Recalculate when off-chain send-BTC rate changes."""
        if self.swap_direction == "sat2kas":
            self._on_active_rate_updated()

    def on_kas2btc_rate(self, instance, value):
        """Recalculate when on-chain send-KAS rate changes."""
        if self.swap_direction == "kas2btc":
            self._on_active_rate_updated()

    def on_btc2kas_rate(self, instance, value):
        """Recalculate when on-chain send-BTC rate changes."""
        if self.swap_direction == "btc2kas":
            self._on_active_rate_updated()

    def _on_active_rate_updated(self):
        q = self.last_quote
        quote_live = (
            q
            and q.get('swap_type') == self.swap_direction
            and (q.get('valid_until') or 0) > time.time()
        )
        if not quote_live:
            self.calculate_amounts()
            self.get_send_amount_display()
            self.get_receive_amount_display()
        self.validate()
        if self.screen:
            peer_selector = self.screen.ids.peer_selector
            peer_selector.update_status(peer_selector.peer_status, editor=self)
    
    def reset(self):
        """Reset all properties to default values."""
        # self.swap_direction / swap_mode not reset — keep user preference
        self.send_amount_display = ""
        self.receive_amount_display = ""
        self.kas_amount = 0
        self.btc_amount = 0
        self.anchor_field = "none"
        self.last_quote = None
        self.quote_failed = False
        self.kas2sat_rate = 0
        self.sat2kas_rate = 0
        self.kas2btc_rate = 0
        self.btc2kas_rate = 0
        self.summary_display = ""
        self.is_editing = True
        self.is_valid = False
        self.is_fetching_rate = False
        self.is_collapsed = False
        self.ids.editor_collapsed_icon.icon = "check-circle"
        self.ids.editor_collapsed_icon.text_color = (0, 0.8, 0, 1)
        self.height = dp(264)


