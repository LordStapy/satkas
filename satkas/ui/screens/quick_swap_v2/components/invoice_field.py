"""
Invoice Field Component - Smart invoice display/input
"""
import asyncio

from kivy.animation import Animation
from kivy.properties import StringProperty, BooleanProperty
from kivy.metrics import dp
from kivy.core.clipboard import Clipboard
from kivymd.app import App
from kivymd.uix.card import MDCard
from kivymd.uix.dialog import MDDialog, MDDialogHeadlineText, MDDialogContentContainer, MDDialogButtonContainer
from kivymd.uix.textfield import MDTextField, MDTextFieldHintText
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDButton, MDButtonText, MDButtonIcon, MDIconButton
from kivymd.uix.label import MDLabel
from kivymd.uix.widget import MDWidget


class InvoiceInputDialog(MDDialog):
    """
    Dialog for inputting Lightning invoice with validation.
    
    Features:
    - Auto-fill from clipboard
    - Paste button
    - Real-time validation (green check / red error)
    - Dismiss / Confirm buttons
    """
    
    def __init__(self, on_confirm_callback, **kwargs):
        self.on_confirm_callback = on_confirm_callback
        self.invoice_text = ""
        self.is_valid = False
        self.parsed_data = {}
        self.validate_task = None
        self.app = App.get_running_app()
        
        # Create dialog content
        self.text_field = MDTextField(
            mode="outlined",
            multiline=True,
            max_height=dp(120),
        )
        self.text_field.add_widget(MDTextFieldHintText(text="Paste Lightning Invoice"))
        self.text_field.bind(text=self.on_text_changed)
        
        self.validation_label = MDLabel(
            text="",
            size_hint_y=None,
            height=dp(20),
            theme_text_color="Custom",
            #md_bg_color=self.app.theme_cls.surfaceContainerHighColor
        )
        
        paste_button = MDButton(
            MDButtonIcon(icon="content-paste"),
            MDButtonText(text="Paste"),
            style="outlined",
            on_release=lambda x: self._paste_from_clipboard(),
        )
        
        dismiss_button = MDButton(
            MDButtonText(text="Dismiss"),
            style="text",
            on_release=lambda x: self.dismiss(),
        )
        
        self.confirm_button = MDButton(
            MDButtonText(text="Confirm"),
            style="outlined",
            on_release=lambda x: self._confirm(),
            disabled=True,
        )
        
        content = MDDialogContentContainer(
            self.text_field,
            paste_button,
            self.validation_label,
            orientation="vertical",
            spacing=dp(8),
        )

        buttons = MDDialogButtonContainer(
            MDWidget(),
            dismiss_button,
            self.confirm_button,
            spacing=dp(12),
        )
        
        super().__init__(
            MDDialogHeadlineText(text="Read Lightning Invoice", halign="left"),
            content,
            buttons,
            auto_dismiss=False,
            **kwargs
        )
        
        # Try to auto-fill from clipboard
        self._auto_fill_clipboard()
    
    def _auto_fill_clipboard(self):
        """Auto-fill from clipboard if contains invoice."""
        clipboard_text = Clipboard.paste()
        if clipboard_text and (clipboard_text.startswith('lnbc') or clipboard_text.startswith('lntb')):
            self.text_field.text = clipboard_text
    
    def _paste_from_clipboard(self):
        """Paste button handler."""
        self.text_field.text = Clipboard.paste()

    def on_text_changed(self, instance, value):
        print(f"[invoice_field] on_text_changed: {value}")
        if self.validate_task:
            self.validate_task.cancel()
        self.validate_task = asyncio.create_task(self._on_text_changed(instance, value))
    
    async def _on_text_changed(self, instance, value):
        """Validate as user types."""
        print(f"[invoice_field] (async) _on_text_changed: {value}")
        self.invoice_text = value.strip()
        
        if not self.invoice_text:
            self.validation_label.text = ""
            self.validation_label.text_color = (0.5, 0.5, 0.5, 1)
            self.confirm_button.style = "outlined"
            self.confirm_button.disabled = True
            self.is_valid = False
            return

        # strip the "lightning:" prefix if it exists
        if self.invoice_text.startswith('lightning:'):
            self.invoice_text = self.invoice_text[10:]
        
        if not (self.invoice_text.startswith('lnbc') or self.invoice_text.startswith('lntb')):
            self.validation_label.text = "Invalid invoice format"
            self.validation_label.text_color = (0.8, 0, 0, 1)
            self.is_valid = False
            self.confirm_button.style = "outlined"
            self.confirm_button.disabled = True
            return

        decoded = await self.app.service_manager.ln_wallet_service.decode_invoice(self.invoice_text)
        print(f"[invoice_field] (async) decoded: {decoded}")
        valid = await self.app.service_manager.ln_wallet_service.validate_invoice(self.invoice_text, parsed_data=decoded)
        print(f"[invoice_field] (async) valid: {valid}")
        if valid:
            sats = decoded.get('amount_msat', 0) // 1000
            self.validation_label.text = f"Valid invoice: {sats} sats"
            self.validation_label.text_color = (0, 0.8, 0, 1)
            self.is_valid = True
            self.confirm_button.style = "filled"
            self.confirm_button.disabled = False
            self.confirm_button.children[0].text_color = self.app.theme_cls.onPrimaryColor
            self.parsed_data = decoded
            self.parsed_data['invoice_str'] = self.invoice_text
            self.parsed_data['amount_sat'] = sats
        else:
            error = decoded.get('error', 'Invalid invoice')
            self.validation_label.text = f"{error}"
            self.validation_label.text_color = (0.8, 0, 0, 1)
            self.is_valid = False
            self.confirm_button.style = "outlined"
            self.confirm_button.disabled = True
    
    def _confirm(self):
        """Confirm button - call callback and close."""
        if self.is_valid and self.parsed_data:
            self.on_confirm_callback(self.parsed_data)
            self.dismiss()


class InvoiceField(MDCard):
    """
    Smart invoice field with multiple input modes:
    - Manual input (always available)
    - Generated (kas2sat with internal wallet)
    - From maker (sat2kas)
    - Pre-input during preview (kas2sat - reads invoice before swap)
    """
    
    invoice = StringProperty("")
    title_label_text = StringProperty("Lightning Invoice")
    info_label_text = StringProperty("")
    is_visible = BooleanProperty(False)
    
    # Swap context
    swap_direction = StringProperty("kas2sat")  # Set by parent screen
    is_ln_wallet_external = BooleanProperty(False)  # Set by parent screen
    
    # Invoice validation state
    is_invoice_validated = BooleanProperty(False)
    buttons_row_visible = BooleanProperty(True)
    is_generating = BooleanProperty(False)
    
    # Reference to parent screen
    screen = None
    
    def show(self):
        """Show invoice field with animation."""
        self.is_visible = True
        # Set buttons visibility based on swap direction
        if self.swap_direction == "sat2kas":
            self.buttons_row_visible = False  # Always hidden for sat2kas
        elif self.swap_direction == "kas2sat":
            # Show buttons if invoice not validated yet
            self.buttons_row_visible = not self.is_invoice_validated
        # Height is now responsive, calculated in .kv based on content
        # Animation will use the calculated height automatically
        anim = Animation(opacity=1, duration=0.5)
        anim.start(self)
    
    def hide(self):
        """Hide invoice field with animation."""
        self.is_visible = False
        self.invoice = ""
        self.info_label_text = ""
        self.is_invoice_validated = False
        self.buttons_row_visible = True  # Reset to default
        anim = Animation(height=0, opacity=0, duration=0.5)
        anim.start(self)
    
    def show_qr_popup(self):
        """Show invoice QR code."""
        if self.screen:
            self.screen.show_invoice_qr_popup(self.invoice)
    
    def copy_invoice(self):
        """Copy invoice to clipboard."""
        if self.screen:
            self.screen.copy_to_clipboard(self.invoice)
    
    def open_invoice_input_dialog(self, on_parsed_callback=None):
        """
        Open invoice input dialog with optional callback.
        
        Args:
            on_parsed_callback: If provided, called with parsed data (preview mode).
                               If None, just stores invoice internally (post-swap mode).
        
        Usage:
            # Preview button (root screen):
            invoice_field.open_invoice_input_dialog(screen.apply_invoice_to_preview)
            
            # Post-swap button (invoice field):
            invoice_field.open_invoice_input_dialog()  # Uses internal handler
        """
        if on_parsed_callback:
            # External caller (preview button) - use their callback
            callback = on_parsed_callback
        else:
            # Internal caller (post-swap button) - use internal method
            callback = self._apply_invoice_internal
        
        dialog = InvoiceInputDialog(on_confirm_callback=callback)
        dialog.open()
    
    def _apply_invoice_internal(self, invoice_data):
        """
        Internal handler for post-swap invoice input.
        Validates amount matches editor, then stores invoice.
        """
        # Validate amount matches what was in editor
        if not self._validate_invoice_amount(invoice_data):
            return  # Validation failed, error already shown
        
        self.invoice = invoice_data['invoice_str']
        self.info_label_text = f"Invoice set: {invoice_data['amount_sat']} sats"
        self.is_invoice_validated = True
        
        # Hide buttons row for kas2sat after invoice is validated
        if self.swap_direction == "kas2sat":
            self.buttons_row_visible = False
    
    def _validate_invoice_amount(self, invoice_data):
        """
        Validate that invoice amount matches editor BTC amount.
        
        Returns:
            bool: True if valid, False if mismatch
        """
        if not self.screen:
            return True  # Can't validate without screen reference
        
        editor = self.screen.ids.swap_editor
        
        print(f"[invoice_field] _validate_invoice_amount: editor.btc_amount: {editor.btc_amount}")
        expected_sats = round(editor.btc_amount * 1e8)
        print(f"[invoice_field] _validate_invoice_amount: expected_sats: {expected_sats}")
        invoice_sats = invoice_data.get('amount_sat', 0)
        
        if expected_sats != invoice_sats:
            # Amount mismatch - show error
            error_msg = (
                f"Invoice amount mismatch!\n"
                f"Expected: {expected_sats} sats\n"
                f"Invoice: {invoice_sats} sats\n\n"
                f"Please provide an invoice matching the preview amount."
            )
            self._show_amount_error(error_msg)
            return False
        
        return True
    
    def _show_amount_error(self, message):
        """Show error dialog for amount mismatch."""
        
        dialog = MDDialog(
            MDDialogHeadlineText(text="Amount Mismatch", halign="left"),
            MDDialogContentContainer(
                MDLabel(
                    text=message,
                    theme_text_color="Secondary",
                    adaptive_height=True,
                )
            ),
            MDDialogButtonContainer(
                MDButton(
                    MDButtonText(text="OK"),
                    style="text",
                    pos_hint={"right": 1},
                    on_release=lambda x: dialog.dismiss(),
                )
            ),
            auto_dismiss=True,
        )
        dialog.open()
    
    def reset(self):
        """Reset all properties to default values."""
        self.invoice = ""
        self.title_label_text = "Lightning Invoice"
        self.info_label_text = ""
        self.is_visible = False
        self.swap_direction = "kas2sat"
        self.is_ln_wallet_external = False
        self.is_invoice_validated = False
        self.buttons_row_visible = True
        self.is_generating = False
        self.ids.invoice_info_label.text_color = self.screen.app.theme_cls.primaryColor

