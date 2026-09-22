"""
Invoice Field Component - Smart invoice display/input
"""
import asyncio

from kivy.animation import Animation
from kivy.properties import StringProperty, BooleanProperty, NumericProperty
from kivy.metrics import dp
from kivy.core.clipboard import Clipboard
from kivymd.app import App
from kivymd.uix.card import MDCard
from kivymd.uix.dialog import MDDialog, MDDialogHeadlineText, MDDialogContentContainer, MDDialogButtonContainer
from kivymd.uix.textfield import MDTextField, MDTextFieldHintText
from kivymd.uix.button import MDButton, MDButtonText, MDButtonIcon, MDIconButton
from kivymd.uix.label import MDLabel
from kivymd.uix.widget import MDWidget

from satkas.ui.screens.quick_swap_v2.components.attention_pulse import AttentionPulseMixin


class InvoiceReadButton(AttentionPulseMixin, MDButton):
    """Outlined editor-row button; pulse its line_color when an invoice is needed."""


class InvoiceInputDialog(MDDialog):
    """Paste / generate a bolt11. Kas-anchor mismatch blocks Confirm."""

    def __init__(
        self,
        on_confirm_callback,
        expected_sats=None,
        wallet_flavor=None,
        on_generate=None,
        **kwargs,
    ):
        self.on_confirm_callback = on_confirm_callback
        self.on_generate = on_generate
        self.wallet_flavor = wallet_flavor
        self.expected_sats = int(expected_sats) if expected_sats else None
        self.invoice_text = ""
        self.is_valid = False
        self.parsed_data = {}
        self.validate_task = None
        self.app = App.get_running_app()

        self.hint_label = MDLabel(
            text=self._hint_text(),
            size_hint_y=None,
            height=dp(20) if self.expected_sats else 0,
            opacity=1 if self.expected_sats else 0,
            theme_text_color="Secondary",
        )
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
        )

        content_widgets = [
            self.hint_label,
            self.text_field,
            MDButton(
                MDButtonIcon(icon="content-paste"),
                MDButtonText(text="Paste"),
                style="outlined",
                on_release=lambda *_: self._paste_from_clipboard(),
            ),
        ]
        self.generate_button = None
        if self.expected_sats:
            self._make_generate_button()
            if self.generate_button:
                content_widgets.append(self.generate_button)
        content_widgets.append(self.validation_label)

        self.use_invoice_label = MDButtonText(text="Use invoice amount")
        self.use_invoice_button = MDButton(
            self.use_invoice_label,
            style="text",
            on_release=lambda *_: self._confirm(honor_invoice=True),
        )
        self.confirm_label = MDButtonText(text="Confirm")
        self.confirm_button = MDButton(
            self.confirm_label,
            style="outlined",
            on_release=lambda *_: self._confirm(),
            disabled=True,
        )
        self._content = MDDialogContentContainer(
            *content_widgets,
            orientation="vertical",
            spacing=dp(8),
        )
        self._buttons = MDDialogButtonContainer(
            MDWidget(),
            MDButton(
                MDButtonText(text="Dismiss"),
                style="text",
                on_release=lambda *_: self.dismiss(),
            ),
            self.confirm_button,
            spacing=dp(12),
        )

        super().__init__(
            MDDialogHeadlineText(text="Read Lightning Invoice", halign="left"),
            self._content,
            self._buttons,
            auto_dismiss=False,
            **kwargs,
        )
        self._auto_fill_clipboard()

    def _hint_text(self):
        if not self.expected_sats:
            return ""
        return f"Create an invoice for {self.expected_sats} sats"

    def _make_generate_button(self):
        if self.generate_button or self.wallet_flavor not in ("lnbits", "lncli"):
            return
        label = "Use LNbits" if self.wallet_flavor == "lnbits" else "Use LND"
        self.generate_button = MDButton(
            MDButtonIcon(icon="lightning-bolt"),
            MDButtonText(text=label),
            style="filled",
            on_release=lambda *_: self._generate(),
        )

    def update_expected_sats(self, expected_sats):
        self.expected_sats = int(expected_sats) if expected_sats else None
        self.hint_label.text = self._hint_text()
        self.hint_label.height = dp(20) if self.expected_sats else 0
        self.hint_label.opacity = 1 if self.expected_sats else 0
        if self.expected_sats:
            self._make_generate_button()
            if self.generate_button and self.generate_button.parent is None:
                self._content.remove_widget(self.validation_label)
                self._content.add_widget(self.generate_button)
                self._content.add_widget(self.validation_label)
        elif self.generate_button and self.generate_button.parent:
            self.generate_button.parent.remove_widget(self.generate_button)
        if self.invoice_text:
            self.on_text_changed(self.text_field, self.text_field.text)

    def _auto_fill_clipboard(self):
        clipboard_text = Clipboard.paste()
        if clipboard_text and (
            clipboard_text.startswith("lnbc") or clipboard_text.startswith("lntb")
        ):
            self.text_field.text = clipboard_text

    def _paste_from_clipboard(self):
        self.text_field.text = Clipboard.paste()

    def _generate(self):
        if not self.on_generate or not self.generate_button:
            return
        self.generate_button.disabled = True
        self.on_generate(self)

    def on_text_changed(self, instance, value):
        if self.validate_task:
            self.validate_task.cancel()
        self.validate_task = asyncio.create_task(self._on_text_changed(instance, value))

    def _set_confirm(self, enabled, parsed=None):
        self.is_valid = enabled
        self.confirm_button.disabled = not enabled
        self.confirm_button.style = "filled" if enabled else "outlined"
        if enabled:
            self.confirm_label.text_color = self.app.theme_cls.onPrimaryColor
            self.parsed_data = parsed or {}
        else:
            self.parsed_data = parsed or self.parsed_data

    def _set_use_invoice(self, sats=None):
        if sats is None:
            if self.use_invoice_button.parent:
                self.use_invoice_button.parent.remove_widget(self.use_invoice_button)
            return
        self.use_invoice_label.text = f"Use {sats} sats instead"
        if self.use_invoice_button.parent is None:
            self._buttons.remove_widget(self.confirm_button)
            self._buttons.add_widget(self.use_invoice_button)
            self._buttons.add_widget(self.confirm_button)

    async def _on_text_changed(self, instance, value):
        self.invoice_text = value.strip()
        self._set_use_invoice(None)
        if not self.invoice_text:
            self.validation_label.text = ""
            self.validation_label.text_color = (0.5, 0.5, 0.5, 1)
            self._set_confirm(False)
            return

        if self.invoice_text.startswith("lightning:"):
            self.invoice_text = self.invoice_text[10:]

        if not (
            self.invoice_text.startswith("lnbc") or self.invoice_text.startswith("lntb")
        ):
            self.validation_label.text = "Invalid invoice format"
            self.validation_label.text_color = (0.8, 0, 0, 1)
            self._set_confirm(False)
            return

        ln = self.app.service_manager.ln_wallet_service
        try:
            decoded = await ln.decode_invoice(self.invoice_text)
            valid = await ln.validate_invoice(self.invoice_text, parsed_data=decoded)
        except asyncio.CancelledError:
            return
        decoded = decoded or {}
        if not valid:
            self.validation_label.text = decoded.get("error", "Invalid invoice")
            self.validation_label.text_color = (0.8, 0, 0, 1)
            self._set_confirm(False)
            return

        sats = int(decoded.get("amount_msat") or 0) // 1000
        decoded["invoice_str"] = self.invoice_text
        decoded["amount_sat"] = sats
        if self.expected_sats and abs(sats - self.expected_sats) > 1:
            self.validation_label.text = (
                f"Invoice is {sats} sats, expected {self.expected_sats}"
            )
            self.validation_label.text_color = (0.8, 0, 0, 1)
            self._set_confirm(False, parsed=decoded)
            self._set_use_invoice(sats)
            return

        self.validation_label.text = f"Valid invoice: {sats} sats"
        self.validation_label.text_color = (0, 0.8, 0, 1)
        self._set_confirm(True, parsed=decoded)

    def _confirm(self, honor_invoice=False):
        if honor_invoice:
            if not self.parsed_data:
                return
            if self.on_confirm_callback(self.parsed_data, honor_invoice=True) is False:
                return
            self.dismiss()
            return
        if self.is_valid and self.parsed_data:
            if self.on_confirm_callback(self.parsed_data) is False:
                return
            self.dismiss()


class InvoiceField(AttentionPulseMixin, MDCard):
    """Invoice display after start; generate/paste happen in the editor dialog."""

    invoice = StringProperty("")
    invoice_sats = NumericProperty(0)
    title_label_text = StringProperty("Lightning Invoice")
    info_label_text = StringProperty("")
    is_visible = BooleanProperty(False)
    swap_direction = StringProperty("kas2sat")
    is_ln_wallet_external = BooleanProperty(False)
    is_invoice_validated = BooleanProperty(False)
    buttons_row_visible = BooleanProperty(False)
    is_generating = BooleanProperty(False)
    screen = None

    def show(self):
        self.is_visible = True
        # kas2sat invoice is collected before Start; field is display-only.
        self.buttons_row_visible = False
        # Commented: post-start invoice wait. kas2sat invoice is collected before Start.
        # Keep Read only if the fallback wait still has no invoice.
        # self.buttons_row_visible = (
        #     self.swap_direction == "kas2sat" and not self.invoice
        # )
        anim = Animation(opacity=1, duration=0.5)
        anim.start(self)

    def hide(self):
        self.stop_attention_pulse()
        self.clear_stored()
        self.is_visible = False
        self.buttons_row_visible = False
        anim = Animation(height=0, opacity=0, duration=0.5)
        anim.start(self)

    def clear_stored(self):
        self.invoice = ""
        self.invoice_sats = 0
        self.is_invoice_validated = False
        self.info_label_text = ""
        self.is_generating = False

    def store(self, invoice_str, sats, info=None):
        self.invoice = invoice_str
        self.invoice_sats = int(sats or 0)
        self.is_invoice_validated = True
        self.is_generating = False
        self.info_label_text = info or f"Invoice set: {self.invoice_sats} sats"
        self.buttons_row_visible = False

    def show_qr_popup(self):
        if self.screen:
            self.screen.show_invoice_qr_popup(self.invoice)

    def copy_invoice(self):
        if self.screen:
            self.screen.copy_to_clipboard(self.invoice)

    # Commented: post-start invoice wait. kas2sat invoice is collected before Start.
    # def open_invoice_input_dialog(self, on_parsed_callback=None):
    #     if self.screen:
    #         self.screen.open_invoice_dialog()

    def reset(self):
        self.stop_attention_pulse()
        self.clear_stored()
        self.title_label_text = "Lightning Invoice"
        self.is_visible = False
        self.swap_direction = "kas2sat"
        self.is_ln_wallet_external = False
        self.buttons_row_visible = False
        if self.screen:
            self.ids.invoice_info_label.text_color = self.screen.app.theme_cls.primaryColor
