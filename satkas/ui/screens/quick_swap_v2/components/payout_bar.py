"""Payout address card + edit dialog, set before a swap starts."""

import asyncio

from bitcoinutils.keys import P2wpkhAddress
from kivy.app import App
from kivy.metrics import dp
from kivy.properties import AliasProperty, BooleanProperty, StringProperty
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDButton, MDButtonText
from kivymd.uix.card import MDCard
from kivymd.uix.dialog import (
    MDDialog,
    MDDialogButtonContainer,
    MDDialogContentContainer,
    MDDialogHeadlineText,
)
from kivymd.uix.label import MDLabel
from kivymd.uix.selectioncontrol import MDCheckbox
from kivymd.uix.textfield import MDTextField, MDTextFieldHintText
from kivymd.uix.widget import MDWidget

from satkas.core.db.models import Setting
from satkas.core.klib.kaddress import decode_address
from satkas.ui.media import BTC_ICON, KASPA_ICON

_KAS_CUSTOM = 'app.payout.kas_custom'
_BTC_CUSTOM = 'app.payout.btc_custom'
_KAS_AUTO = 'app.payout.kas_auto_wallet'
_BTC_AUTO = 'app.payout.btc_auto_wallet'

_SOURCE_LABEL = {
    'wallet': 'Wallet',
    'custom': 'Custom',
    'internal': 'Internal',
}

_ROLE_REDEEM = 'Redeem to:'
_ROLE_REFUND = 'Refund to:'


def _valid_kas(address):
    try:
        decode_address(address)
        return True
    except Exception:
        return False


def _valid_btc(address):
    try:
        P2wpkhAddress.from_address(address)
        return True
    except Exception:
        return False


class PayoutAddressDialog(MDDialog):
    """Paste, wallet auto-pick, or SatKas-key fallback for one chain."""

    def __init__(self, chain, role, address, source, auto_wallet, on_confirm, **kwargs):
        self.chain = chain
        self.on_confirm_callback = on_confirm
        self.source = source or 'custom'
        self.app = App.get_running_app()
        self._wallet_addr = None
        self._internal_addr = None

        is_kas = chain == 'kas'
        hint = 'Kaspa address' if is_kas else 'Bitcoin address'
        self._validate = _valid_kas if is_kas else _valid_btc

        self.text_field = MDTextField(
            MDTextFieldHintText(text=hint),
            mode='outlined',
            multiline=False,
            size_hint_y=None,
            height=dp(56),
        )
        self.text_field.bind(text=self._on_text)

        self.validation_label = MDLabel(
            text='',
            size_hint_y=None,
            height=dp(20),
            theme_text_color='Custom',
            text_color=(0.5, 0.5, 0.5, 1),
        )
        self.warning_label = MDLabel(
            text=(
                'Funds sent here are not visible in SatKas. You must handle '
                'the private keys yourself.'
            ),
            size_hint_y=None,
            height=dp(48),
            theme_text_color='Custom',
            text_color=(1, 0.35, 0.3, 1),
        )

        self.wallet_button = MDButton(
            MDButtonText(text='Use wallet address'),
            style='outlined',
            on_release=lambda *_: asyncio.create_task(self._use_wallet()),
        )
        self.internal_button = MDButton(
            MDButtonText(text='Use SatKas keys'),
            style='outlined',
            on_release=lambda *_: self._use_internal(),
        )

        self.auto_checkbox = MDCheckbox(
            active=bool(auto_wallet),
            size_hint=(None, None),
            size=(dp(40), dp(40)),
        )
        self.auto_row = MDBoxLayout(
            self.auto_checkbox,
            MDLabel(text="Don't ask again", adaptive_height=True),
            orientation='horizontal',
            spacing=dp(8),
            size_hint_y=None,
            height=dp(40),
        )
        self.understand_checkbox = MDCheckbox(
            active=False,
            size_hint=(None, None),
            size=(dp(40), dp(40)),
        )
        self.understand_checkbox.bind(active=lambda *_: self._sync_confirm())
        self.understand_row = MDBoxLayout(
            self.understand_checkbox,
            MDLabel(text='I understand', adaptive_height=True),
            orientation='horizontal',
            spacing=dp(8),
            size_hint_y=None,
            height=dp(40),
        )
        self.internal_box = MDBoxLayout(
            self.warning_label,
            self.understand_row,
            orientation='vertical',
            spacing=dp(8),
            size_hint_y=None,
            height=dp(48) + dp(40) + dp(8),
        )

        sm = self.app.service_manager
        wallet_ok = (
            not sm.kas_wallet_is_external() if is_kas
            else not sm.btc_wallet_is_external()
        )
        content_children = [self.text_field, self.wallet_button]
        if wallet_ok:
            content_children.append(self.auto_row)
        else:
            self.wallet_button.disabled = True
        content_children.extend([self.internal_button, self.validation_label])
        self._content = MDDialogContentContainer(
            *content_children,
            orientation='vertical',
            spacing=dp(8),
        )

        self.confirm_button = MDButton(
            MDButtonText(
                text='OK',
                disabled_color=self.app.theme_cls.onSecondaryContainerColor,
            ),
            style='filled',
            disabled=True,
            opacity=0.5,
            md_bg_color_disabled=self.app.theme_cls.secondaryContainerColor,
            on_release=lambda *_: self._confirm(),
        )

        super().__init__(
            MDDialogHeadlineText(text=f'Set {role}', halign='left'),
            self._content,
            MDDialogButtonContainer(
                MDWidget(),
                MDButton(
                    MDButtonText(text='Cancel'),
                    style='text',
                    on_release=lambda *_: self.dismiss(),
                ),
                self.confirm_button,
                spacing=dp(12),
            ),
            auto_dismiss=False,
            **kwargs,
        )
        if source == 'internal':
            self._internal_addr = address
        elif source == 'wallet':
            self._wallet_addr = address
        if address:
            self.text_field.text = address

    async def _use_wallet(self):
        sm = self.app.service_manager
        try:
            if self.chain == 'kas':
                addr = await sm.kaspa_wallet_service.get_new_address()
            else:
                addr = await sm.btc_wallet_service.get_new_address()
        except Exception:
            addr = None
        if not addr:
            self._set_validation('Could not get a wallet address')
            return
        self._wallet_addr = addr
        self.source = 'wallet'
        self.text_field.text = addr

    def _use_internal(self):
        taker = self.app.taker
        if self.chain == 'kas':
            addr = taker.address
        else:
            addr = taker.btc_address.to_string()
        self._internal_addr = addr
        self.source = 'internal'
        self.text_field.text = addr

    def _on_text(self, _instance, value):
        text = (value or '').strip()
        if text == self._internal_addr:
            self.source = 'internal'
        elif text == self._wallet_addr:
            self.source = 'wallet'
        else:
            self.source = 'custom'
        internal = self.source == 'internal'
        self._set_internal_visible(internal)
        if not internal:
            self.understand_checkbox.active = False
        self._sync_confirm()

    def _set_internal_visible(self, visible):
        if visible:
            if self.internal_box.parent is None:
                self._content.remove_widget(self.validation_label)
                self._content.add_widget(self.internal_box)
                self._content.add_widget(self.validation_label)
        elif self.internal_box.parent:
            self._content.remove_widget(self.internal_box)

    def _set_validation(self, message):
        self.validation_label.text = message
        self.validation_label.text_color = (0.8, 0, 0, 1)

    def _sync_confirm(self, *_args):
        text = (self.text_field.text or '').strip()
        valid = bool(text) and self._validate(text)
        if valid or not text:
            self._set_validation('')
        else:
            self._set_validation('Invalid address')
        if self.source == 'internal' and not self.understand_checkbox.active:
            valid = False
        self.confirm_button.disabled = not valid
        self.confirm_button.opacity = 1 if valid else 0.5

    def _confirm(self):
        address = (self.text_field.text or '').strip()
        auto = bool(self.auto_checkbox.active) and self.source == 'wallet'
        self.on_confirm_callback(self.chain, address, self.source, auto)
        self.dismiss()


class PayoutAddressBar(MDCard):
    """Grouped payout card: one Kaspa row, plus BTC when on-chain."""

    kas_address = StringProperty('')
    btc_address = StringProperty('')
    kas_source = StringProperty('')
    btc_source = StringProperty('')
    kas_role = StringProperty(_ROLE_REFUND)
    btc_role = StringProperty(_ROLE_REDEEM)
    kaspa_icon = StringProperty(KASPA_ICON)
    btc_icon = StringProperty(BTC_ICON)
    show_btc = BooleanProperty(False)
    is_locked = BooleanProperty(False)

    def _kas_address_display(self):
        return self.kas_address or 'Set address'

    def _btc_address_display(self):
        return self.btc_address or 'Set address'

    def _kas_source_label(self):
        return _SOURCE_LABEL.get(self.kas_source, '') if self.kas_address else ''

    def _btc_source_label(self):
        return _SOURCE_LABEL.get(self.btc_source, '') if self.btc_address else ''

    def _is_ready(self):
        if not self.kas_address:
            return False
        if self.show_btc and not self.btc_address:
            return False
        return True

    kas_address_display = AliasProperty(
        _kas_address_display, None, bind=['kas_address'],
    )
    btc_address_display = AliasProperty(
        _btc_address_display, None, bind=['btc_address'],
    )
    kas_source_label = AliasProperty(
        _kas_source_label, None, bind=['kas_source', 'kas_address'],
    )
    btc_source_label = AliasProperty(
        _btc_source_label, None, bind=['btc_source', 'btc_address'],
    )
    is_ready = AliasProperty(
        _is_ready, None,
        bind=['kas_address', 'btc_address', 'show_btc'],
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.screen = None
        self._dialog = None

    def sync_from_editor(self, editor):
        onchain = editor.swap_mode == 'onchain'
        self.show_btc = onchain
        sends_kas = editor.swap_direction in ('kas2sat', 'kas2btc')
        self.kas_role = _ROLE_REFUND if sends_kas else _ROLE_REDEEM
        self.btc_role = _ROLE_REDEEM if sends_kas else _ROLE_REFUND

    async def apply_persisted(self):
        sm = App.get_running_app().service_manager
        if not self.kas_address and Setting.get_value(_KAS_AUTO, False):
            if sm.kas_wallet_is_external():
                Setting.set_value(_KAS_AUTO, False, 'bool')
            else:
                try:
                    addr = await sm.kaspa_wallet_service.get_new_address()
                except Exception:
                    addr = None
                if addr and not self.kas_address:
                    self.kas_address = addr
                    self.kas_source = 'wallet'
        if not self.kas_address:
            custom = Setting.get_value(_KAS_CUSTOM, '') or ''
            if custom:
                self.kas_address = custom
                self.kas_source = 'custom'

        if not self.btc_address and Setting.get_value(_BTC_AUTO, False):
            if sm.btc_wallet_is_external():
                Setting.set_value(_BTC_AUTO, False, 'bool')
            else:
                try:
                    addr = await sm.btc_wallet_service.get_new_address()
                except Exception:
                    addr = None
                if addr and not self.btc_address:
                    self.btc_address = addr
                    self.btc_source = 'wallet'
        if not self.btc_address:
            custom = Setting.get_value(_BTC_CUSTOM, '') or ''
            if custom:
                self.btc_address = custom
                self.btc_source = 'custom'

    def open_dialog(self, chain):
        if self.is_locked:
            return
        is_kas = chain == 'kas'
        self._dialog = PayoutAddressDialog(
            chain=chain,
            role=self.kas_role if is_kas else self.btc_role,
            address=self.kas_address if is_kas else self.btc_address,
            source=self.kas_source if is_kas else self.btc_source,
            auto_wallet=Setting.get_value(_KAS_AUTO if is_kas else _BTC_AUTO, False),
            on_confirm=self._on_dialog_confirm,
        )
        self._dialog.open()

    def _on_dialog_confirm(self, chain, address, source, auto_wallet):
        is_kas = chain == 'kas'
        if is_kas:
            self.kas_address = address
            self.kas_source = source
            Setting.set_value(_KAS_AUTO, auto_wallet, 'bool')
            if source == 'custom':
                Setting.set_value(_KAS_CUSTOM, address, 'str')
        else:
            self.btc_address = address
            self.btc_source = source
            Setting.set_value(_BTC_AUTO, auto_wallet, 'bool')
            if source == 'custom':
                Setting.set_value(_BTC_CUSTOM, address, 'str')
