"""
Quick Swap V2 Screen - Clean, single-column implementation
"""

import asyncio
import os
import time
import math
from kivy.app import App
from kivy.lang import Builder
from kivy.metrics import dp
from kivy.core.clipboard import Clipboard
from kivy.properties import BooleanProperty, StringProperty
from kivy.uix.image import Image
from kivymd.uix.screen import MDScreen
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.dialog import MDDialog, MDDialogHeadlineText, MDDialogContentContainer, MDDialogButtonContainer
from kivymd.uix.label import MDLabel
from kivymd.uix.button import MDButton, MDButtonText, MDButtonIcon
from kivymd.uix.selectioncontrol import MDCheckbox
from kivymd.uix.behaviors.toggle_behavior import MDToggleButton
from kivymd.uix.textfield import MDTextField, MDTextFieldHintText
from kivymd.uix.widget import MDWidget
from kivymd.uix.card import MDCard

from satkas.ui.utils import make_qr
from satkas.core.klib.kaddress import decode_address
from .swap_controller import SwapController
from .components import (
    PeerSelectorCard,
    SwapEditor,
    InvoiceField,
    ContractAddressField,
    PaymentField,
    StatusWidget
)

# Load KV file
components_dir = os.path.join(os.path.dirname(__file__), 'components')
Builder.load_file(os.path.join(os.path.dirname(__file__), 'quick_swap_v2_screen.kv'))
Builder.load_file(os.path.join(components_dir, 'peer_selector.kv'))
Builder.load_file(os.path.join(components_dir, 'swap_editor.kv'))
Builder.load_file(os.path.join(components_dir, 'invoice_field.kv'))
Builder.load_file(os.path.join(components_dir, 'contract_field.kv'))
Builder.load_file(os.path.join(components_dir, 'payment_field.kv'))
Builder.load_file(os.path.join(components_dir, 'status_widget.kv'))


class QuickSwapV2Screen(MDScreen):
    """Single-column swap screen"""
    
    # Cancel/Back button state
    payment_started = BooleanProperty(False)
    swap_final_state = StringProperty("")  # "completed", "expired", "failed", or ""
    show_cancel_button = BooleanProperty(False)
    cancel_button_text = StringProperty("Cancel Swap")

    output_address = StringProperty("")
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.name = "quick_swap_v2_screen"
        self.app = App.get_running_app()
        self.refresh_rate_task = None
        self.amount_debounce_task = None
        self.dialog = None
        
        # Track valid_until per direction
        self._kas2sat_valid_until = 0
        self._sat2kas_valid_until = 0

        # Initialize controller
        self.controller = SwapController(
            self.app.taker,
            self.app.service_manager
        )
    
    def on_kv_post(self, base_widget):
        """Called after KV is loaded."""
        if not getattr(self, 'app', None):
            self.app = App.get_running_app()
        
        # Setup component references
        self.ids.peer_selector.screen = self
        self.ids.swap_editor.screen = self
        self.ids.invoice_field.screen = self
        self.ids.contract_field.screen = self
        self.ids.payment_field.screen = self
        self.ids.status_widget.screen = self
    
    def on_enter(self):
        """Called when entering the screen."""
        if self.app and hasattr(self.app, 'root'):
            main_screen = self.app.root.get_screen('main_screen')
            if hasattr(main_screen, 'children') and main_screen.children:
                main_screen.children[0].ids.top_bar_title.text = "Quick Swap V2"
        
        # Start rate refresh
        self.refresh_rate_task = asyncio.create_task(self.refresh_rates())
    
    def on_leave(self):
        """Called when leaving the screen."""
        if self.refresh_rate_task and not self.refresh_rate_task.done():
            self.refresh_rate_task.cancel()
    
    def restart_rate_refresh(self):
        """Kill ongoing refresh task and start a new one immediately."""
        # Cancel existing task
        if self.refresh_rate_task and not self.refresh_rate_task.done():
            self.refresh_rate_task.cancel()
        
        # Start new task immediately
        self.refresh_rate_task = asyncio.create_task(self.refresh_rates())
    
    def debounced_rate_refresh(self):
        """Trigger rate refresh after debounce delay (for amount changes)."""
        # Cancel existing debounce task
        if self.amount_debounce_task and not self.amount_debounce_task.done():
            self.amount_debounce_task.cancel()
        
        # Start new debounce task
        self.amount_debounce_task = asyncio.create_task(self._debounced_refresh())
    
    async def _debounced_refresh(self):
        """Wait for debounce delay then restart refresh."""
        try:
            await asyncio.sleep(1)  # 1 second debounce
            self.restart_rate_refresh()
        except asyncio.CancelledError:
            pass  # Task was cancelled, user is still typing
    
    async def refresh_rates(self):
        """Continuously refresh peer rates with concurrent queries."""
        while True:
            try:
                editor = self.ids.swap_editor
                peer_selector = self.ids.peer_selector
                
                # Skip queries if swap is ongoing
                if editor.is_collapsed:
                    await asyncio.sleep(1)
                    continue
                
                # Query rates for both directions
                kas_amount = editor.kas_amount if editor.kas_amount > 0 else 0
                
                # Set status to checking (ONCE at start)
                amount_info = f"for {kas_amount:.0f} KAS" if kas_amount != 1 else ""
                
                # Check if auto-select is enabled
                if peer_selector.selected_peer == "auto":
                    peer_selector.update_status("checking", f"Finding best rate {amount_info}...".strip())
                    
                    # Query all peers and select best
                    best_peer, new_kas2sat_rate, new_sat2kas_rate = await self._auto_select_best_peer(kas_amount)
                    
                    if best_peer:
                        peer_selector.actual_peer = best_peer
                        self.app.taker.maker_endpoint = best_peer
                    else:
                        # No peers responded, stay offline
                        peer_selector.actual_peer = ""
                        peer_selector.update_status("offline", "No peers available")
                        await asyncio.sleep(30)
                        continue
                else:
                    # Manual peer selection
                    peer_selector.update_status("checking", f"Fetching rates {amount_info}...".strip())
                    peer = peer_selector.selected_peer
                    peer_selector.actual_peer = peer
                    self.app.taker.maker_endpoint = peer
                    
                    print(f"{time.ctime()} - Querying {peer} for {kas_amount:.0f} KAS")
                    
                    # Query both directions concurrently from selected peer
                    results = await asyncio.gather(
                        self.app.taker.query_price("kas2sat", kas_amount),
                        self.app.taker.query_price("sat2kas", kas_amount),
                        return_exceptions=True
                    )
                    
                    kas2sat_res, sat2kas_res = results
                    new_kas2sat_rate = 0
                    new_sat2kas_rate = 0
                    
                    if kas2sat_res and not isinstance(kas2sat_res, Exception):
                        new_kas2sat_rate = list(kas2sat_res[0].values())[0][0] if kas2sat_res[0] else 0
                        self._kas2sat_valid_until = kas2sat_res[1]
                    else:
                        self._kas2sat_valid_until = time.time() + 30
                    
                    if sat2kas_res and not isinstance(sat2kas_res, Exception):
                        new_sat2kas_rate = list(sat2kas_res[0].values())[0][0] if sat2kas_res[0] else 0
                        self._sat2kas_valid_until = sat2kas_res[1]
                    else:
                        self._sat2kas_valid_until = time.time() + 30
                
                # Update both rates at once (reduces callback triggers)
                editor.kas2sat_rate = new_kas2sat_rate
                editor.sat2kas_rate = new_sat2kas_rate
                
                # Clear fetching flag after rates are set (even if values didn't change)
                # This handles the case where Kivy property callbacks don't fire
                # because the rate value is unchanged
                editor.is_fetching_rate = False
                
                # Update peer status based on results (ONCE at end)
                if new_kas2sat_rate > 0 or new_sat2kas_rate > 0:
                    peer_selector.update_status("online", editor=editor)
                else:
                    peer_selector.update_status("offline", "No rates available")
                
                # Sleep until earliest expiration
                next_refresh = min(self._kas2sat_valid_until, self._sat2kas_valid_until)
                sleep_time = max(next_refresh - time.time(), 0.5)
                
                await asyncio.sleep(sleep_time)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Error refreshing rates: {e}")
                self.ids.peer_selector.update_status("offline", "Error fetching rates from peer")
                await asyncio.sleep(30)
    
    async def _auto_select_best_peer(self, kas_amount):
        """Query all peers concurrently and select best rate.
        
        Returns:
            tuple: (best_peer, kas2sat_rate, sat2kas_rate)
        """
        available_peers = self.ids.peer_selector.available_peers[1:]  # Skip "auto"
        
        if not available_peers:
            return None, 0, 0
        
        print(f"{time.ctime()} - Auto-selecting: querying {len(available_peers)} peers for {kas_amount:.0f} KAS")
        
        # Query all peers concurrently using endpoint parameter
        tasks = []
        for peer in available_peers:
            # Query both directions from this peer
            task_kas2sat = self.app.taker.query_price("kas2sat", kas_amount, endpoint=peer)
            task_sat2kas = self.app.taker.query_price("sat2kas", kas_amount, endpoint=peer)
            tasks.append((peer, task_kas2sat, task_sat2kas))
        
        # Gather all results (flatten into single list for gather)
        all_tasks = []
        for peer, t1, t2 in tasks:
            all_tasks.extend([t1, t2])
        
        all_results_flat = await asyncio.gather(*all_tasks, return_exceptions=True)
        
        # Reconstruct results per peer
        all_results = []
        for i, (peer, _, _) in enumerate(tasks):
            kas2sat_res = all_results_flat[i * 2]
            sat2kas_res = all_results_flat[i * 2 + 1]
            all_results.append((peer, kas2sat_res, sat2kas_res))
        
        # Find best peer based on current direction
        editor = self.ids.swap_editor
        current_direction = editor.swap_direction
        
        best_peer = None
        best_rate = 0 if current_direction == "kas2sat" else float('inf')  # Max for kas2sat, min for sat2kas
        best_kas2sat_rate = 0
        best_sat2kas_rate = 0
        best_valid_until = time.time() + 30
        
        for peer, kas2sat_res, sat2kas_res in all_results:
            # Extract rates
            kas2sat_rate = 0
            sat2kas_rate = 0
            valid_until = time.time() + 30
            
            if kas2sat_res and not isinstance(kas2sat_res, Exception):
                kas2sat_rate = list(kas2sat_res[0].values())[0][0] if kas2sat_res[0] else 0
                valid_until = kas2sat_res[1]
            
            if sat2kas_res and not isinstance(sat2kas_res, Exception):
                sat2kas_rate = list(sat2kas_res[0].values())[0][0] if sat2kas_res[0] else 0
                valid_until = min(valid_until, sat2kas_res[1])
            
            # Select best based on current direction
            current_rate = kas2sat_rate if current_direction == "kas2sat" else sat2kas_rate
            
            # kas2sat: maximize rate (sell KAS for more BTC)
            # sat2kas: minimize rate (buy KAS cheaper, fewer sats per KAS)
            is_better = False
            if current_direction == "kas2sat":
                is_better = current_rate > best_rate and current_rate > 0
            else:  # sat2kas
                is_better = 0 < current_rate < best_rate
            
            if is_better:
                best_rate = current_rate
                best_peer = peer
                best_kas2sat_rate = kas2sat_rate
                best_sat2kas_rate = sat2kas_rate
                best_valid_until = valid_until
            
            print(f"  {peer[:20]}...: kas2sat={kas2sat_rate:.2f}, sat2kas={sat2kas_rate:.2f}")
        
        if best_peer:
            print(f"  → Selected: {best_peer[:20]}... (best {current_direction} rate: {best_rate:.2f})")
            self._kas2sat_valid_until = best_valid_until
            self._sat2kas_valid_until = best_valid_until
            # Set the best peer as the maker endpoint
            self.app.taker.maker_endpoint = best_peer
        else:
            print(f"  → No peers available")
        
        return best_peer, best_kas2sat_rate, best_sat2kas_rate

    def apply_invoice_to_preview(self, invoice_data):
        """
        Apply parsed invoice during preview phase.
        Called by preview "Read LN Invoice" button.
        
        Updates:
        - Editor BTC amount (from invoice)
        - Recalculates KAS amount
        - Stores invoice in invoice_field for later use
        
        Args:
            invoice_data: dict with 'invoice_str', 'amount_sats', etc.
        """
        editor = self.ids.swap_editor
        invoice_field = self.ids.invoice_field
        
        # Update editor amounts
        editor.btc_amount = invoice_data.get('amount_sat', 0) / 1e8
        editor.calculate_kas_from_btc()  # Recalculate KAS from BTC
        editor.get_send_amount_display()
        editor.get_receive_amount_display()
        editor.validate()

        # set the anchor field to btc
        editor.anchor_field = "btc"
        
        # Store invoice for later use in _start_swap
        invoice_field.invoice = invoice_data['invoice_str']
        invoice_field.is_invoice_validated = True
        invoice_field.info_label_text = f"Invoice set: {invoice_data['amount_sat']} sats"
        invoice_field.swap_direction = "kas2sat"
        invoice_field.buttons_row_visible = False
        
        print(f"Invoice applied to preview: {invoice_data['amount_sat']} sats -> {editor.btc_amount:.8f} BTC -> {editor.kas_amount:.2f} KAS")
    
    def start_swap(self):
        asyncio.create_task(self._start_swap())
    
    async def _start_swap(self):
        """Start the swap - collapse editor and initialize."""
        editor = self.ids.swap_editor
        invoice_field = self.ids.invoice_field
        
        if not editor.is_valid:
            return
        
        print(f"Starting swap: {editor.swap_direction}, KAS: {editor.kas_amount}, Rate: {editor.get_current_rate()}")
        
        # Collapse UI
        editor.collapse()
        editor.get_summary_display()
        self.ids.peer_selector.collapse()

        # Initialize swap via controller
        try:

            # Show cancel button (before any payment)
            self.payment_started = False
            self.swap_final_state = ""
            self.show_cancel_button = True
            self.cancel_button_text = "Cancel Swap"

            invoice_field.swap_direction = editor.swap_direction
            invoice_field.is_ln_wallet_external = (self.app.service_manager._preferred_ln_wallet == "external")

            init_swap_kwargs = {}
            if editor.swap_direction == "kas2sat":
                # immediately show the invoice field for kas2sat
                invoice_field.style = "outlined"
                invoice_field.theme_line_color = "Custom"
                invoice_field.line_color = self.app.theme_cls.primaryColor
                invoice_field.show()
                while not invoice_field.invoice:
                    # Don't overwrite "Generating..." message if invoice is being generated
                    if invoice_field.info_label_text != "Generating...":
                        invoice_field.info_label_text = "Waiting for invoice..."
                    await asyncio.sleep(0.2)
                    if self._kas2sat_valid_until < time.time():
                        return # TODO: we probably need to refresh the swap and retry
                invoice_field.style = "filled" # reset the style to default
                invoice_field.theme_line_color = "Primary"

                init_swap_kwargs['invoice'] = invoice_field.invoice
            
            result = await self.controller.init_swap(
                direction=editor.swap_direction,
                kas_amount=editor.kas_amount,
                rate=editor.get_current_rate(),
                **init_swap_kwargs
            )
            
            print(f"Swap initialized: {result}")
            if not result:
                # we show the status widget with the error message
                editor_collapsed_icon = self.ids.swap_editor.ids.editor_collapsed_icon
                editor_collapsed_icon.icon = "close-circle"
                editor_collapsed_icon.text_color = (1, 0.3, 0.3, 1)
                status_widget = self.ids.status_widget
                status_widget.status_text = "Error initializing swap, please go back and try again"
                status_widget.countdown_text = ""
                self.swap_final_state = "failed"
                self.show_cancel_button = True
                self.cancel_button_text = "Back to Main Screen"
                status_widget.show()
                return

            # Start monitoring
            await self.controller.monitor_swap(self.on_swap_update)
            
            # Setup invoice field for sat2kas
            if editor.swap_direction == "sat2kas":
                invoice_field.invoice = result['invoice']
                decoded_invoice = await self.app.service_manager.ln_wallet_service.decode_invoice(invoice_field.invoice)
                invoice_field.info_label_text = f"Invoice received from maker: {decoded_invoice.get('amount_msat', 0) // 1000} sats"
                invoice_field.is_invoice_validated = True
                invoice_field.buttons_row_visible = False  # Always hidden for sat2kas
                invoice_field.show()
            
            # Setup contract field
            contract_field = self.ids.contract_field
            contract_field.contract_address = result['contract_address']
            contract_field.ids.contract_address_label.texture_update()
            
            if editor.swap_direction == "kas2sat":
                contract_field.title_text = "Contract Address"
                contract_field.status_text = "Ready to fund"
            else:
                contract_field.title_text = "Contract Address"
                contract_field.status_text = "Waiting for funding..."
            
            contract_field.show()
            
            # Setup payment field
            payment_field = self.ids.payment_field
            payment_field.swap_direction = editor.swap_direction
            payment_field.style = "outlined"
            payment_field.theme_line_color = "Custom"
            payment_field.line_color = self.app.theme_cls.primaryColor

            if editor.swap_direction == "kas2sat":
                # Kas2sat: User pays KAS to contract
                payment_field.is_wallet_external = (self.app.service_manager._preferred_wallet == "external")
                payment_field.title_text = "Fund Contract"
                payment_field.info_text = "Pay KAS to start swap"
                payment_field.payment_enabled = True
                payment_field.show()
            else:
                # Sat2kas: User pays BTC invoice (after confirmation)
                payment_field.is_wallet_external = (self.app.service_manager._preferred_ln_wallet == "external")
                payment_field.title_text = "Pay Invoice"
                payment_field.info_text = "Wait for confirmation..."
                payment_field.payment_enabled = False
                # Don't show yet, wait for contract confirmation
                while not payment_field.payment_enabled:
                    await asyncio.sleep(0.5)
                    if self._sat2kas_valid_until < time.time():
                        return # TODO: we probably need to refresh the swap and retry
                payment_field.show()
            
            # Show status widget
            status_widget = self.ids.status_widget
            status_widget.swap_direction = editor.swap_direction
            status_widget.status_text = "Swap initialized"
            status_widget.show()
            
        except Exception as e:
            print(f"Error starting swap: {e}")
            # we show the status widget with the error message
            editor_collapsed_icon = self.ids.swap_editor.ids.editor_collapsed_icon
            editor_collapsed_icon.icon = "close-circle"
            editor_collapsed_icon.text_color = (1, 0.3, 0.3, 1)
            status_widget = self.ids.status_widget
            status_widget.status_text = "Swap failed, please try again"
            status_widget.countdown_text = ""
            self.swap_final_state = "failed"
            self.show_cancel_button = True
            self.cancel_button_text = "Back to Main Screen"
            status_widget.show()
            return
    
    async def on_swap_update(self, update: dict):
        """Handle swap status updates from controller."""
        status = update.get('status')
        status_widget = self.ids.status_widget
        
        if status == 'monitoring':
            funded = update.get('funded', 0)
            time_remaining = update.get('time_remaining', 0)
            minutes = time_remaining // 60
            seconds = time_remaining % 60
            status_widget.status_text = f"Funded: {funded:.4f} KAS"
            status_widget.countdown_text = f"Time remaining: {minutes:02d}:{seconds:02d}"
            if funded:
                # display a yellow color for the contract status label
                self.ids.contract_field.ids.contract_status_label.text_color = (0.8, 0.8, 0, 1)
                # format both the funded and the kas amount to the right number of decimals using format_kas method from swap_editor
                funded_display = self.ids.swap_editor.format_kas(funded)
                kas_amount_display = self.ids.swap_editor.format_kas(self.controller.kas_amount)
                self.ids.contract_field.status_text = f"Partially funded: {funded_display} / {kas_amount_display} KAS"
                status_widget.is_funded = True
                self.show_cancel_button = False
        
        elif status == 'funded':
            if self.dialog:
                try:
                    self.dialog.dismiss()
                except Exception as e:
                    print(f"Error dismissing dialog: {e}")
                self.dialog = None
            self.ids.payment_field.style = "filled"
            self.ids.payment_field.theme_line_color = "Primary"
            status_widget.status_text = "Waiting for redeem by maker..."
            status_widget.is_funded = True
            time_remaining = update.get('time_remaining', 0)
            minutes = time_remaining // 60
            seconds = time_remaining % 60
            status_widget.countdown_text = f"Time remaining: {minutes:02d}:{seconds:02d}"
            self.ids.contract_field.ids.contract_status_label.text_color = (0, 0.8, 0, 1)
            self.ids.contract_field.status_text = "Funded!"
            self.ids.payment_field.ids.payment_info_label.text_color = (0, 0.8, 0, 1)
            self.ids.payment_field.info_text = "Done!"
            self.ids.payment_field.buttons_row_visible = False
            self.show_cancel_button = False
        
        elif status == 'ready_to_pay':
            time_remaining = update.get('time_remaining', 0)
            minutes = time_remaining // 60
            seconds = time_remaining % 60
            status_widget.countdown_text = f"Time remaining: {minutes:02d}:{seconds:02d}"
            if not self.payment_started:
                self.ids.contract_field.is_confirmed = True
                self.ids.contract_field.status_text = "Confirmed!"
                status_widget.status_text = "Contract confirmed - Pay invoice now"
            
            # Show and enable payment field for sat2kas
            payment_field = self.ids.payment_field
            payment_field.info_text = "Ready to pay"
            payment_field.payment_enabled = True
            #payment_field.show()

        elif status == 'waiting_user_payment':
            time_remaining = update.get('time_remaining', 0)
            minutes = time_remaining // 60
            seconds = time_remaining % 60
            # conditional update of the countdown text
            if not self.swap_final_state:
                status_widget.countdown_text = f"Time remaining: {minutes:02d}:{seconds:02d}"
            else:
                status_widget.countdown_text = ""

        
        elif status == 'completed':
            status_widget.ids.status_info_label.text_color = (0, 0.8, 0, 1)
            status_widget.status_text = "Swap completed!"
            status_widget.countdown_text = ""
            self.swap_final_state = "completed"
            self.show_cancel_button = True
            self.cancel_button_text = "Back to Main Screen"
        
        elif status == 'expired':
            
            status_widget.countdown_text = ""
            # disable payment buttons, but leave them visible
            self.ids.payment_field.payment_enabled = False
            self.ids.payment_field.style = "filled"
            self.ids.payment_field.theme_line_color = "Primary"
            self.swap_final_state = "expired"
            status_widget.is_expired = True
            self.cancel_button_text = "Back to Main Screen"
            status_widget.update_refund_visibility()
            
            # If kas2sat and funded, start refund countdown
            if self.controller.swap_direction == "kas2sat" and status_widget.is_funded:
                asyncio.create_task(self._handle_refund_countdown())
                status_widget.ids.status_info_label.text_color = (0.8, 0.8, 0, 1)
                status_widget.status_text = "Swap expired, waiting for refund..."
                self.show_cancel_button = False
            else:
                status_widget.ids.status_info_label.text_color = (0.8, 0, 0, 1)
                status_widget.status_text = "Swap expired"
                self.show_cancel_button = True


        elif status == 'waiting_confirmations':
            time_remaining = update.get('time_remaining', 0)
            minutes = time_remaining // 60
            seconds = time_remaining % 60
            status_widget.countdown_text = f"Time remaining: {minutes:02d}:{seconds:02d}"
            self.ids.contract_field.status_text = "Funded, waiting for confirmations..."

    def generate_invoice(self, amount=None):
        """Generate invoice with internal LN wallet."""
        # Update status label to "Generating..." and disable button
        invoice_field = self.ids.invoice_field
        invoice_field.is_generating = True
        invoice_field.info_label_text = "Generating..."
        asyncio.create_task(self._generate_invoice(amount))

    async def _generate_invoice(self, amount=None):
        """Generate invoice with error handling."""
        invoice_field = self.ids.invoice_field
        try:
            if amount is None:
                amount = round(self.ids.swap_editor.btc_amount * 1e8)
                print(f"generating invoice for {amount} sats ({self.ids.swap_editor.btc_amount * 1e8} BTC)")
            invoice = await self.controller.create_invoice(amount)
            invoice_field.invoice = invoice
            invoice_field.is_invoice_validated = True
            invoice_field.info_label_text = f"Invoice generated: {amount} sats"
            invoice_field.is_generating = False
            # Hide buttons row for kas2sat after invoice is generated
            if invoice_field.swap_direction == "kas2sat":
                invoice_field.buttons_row_visible = False
        except Exception as e:
            print(f"Failed to generate invoice: {e}")
            invoice_field.info_label_text = f"Error generating invoice: {str(e)}"
            invoice_field.is_generating = False
            import traceback
            traceback.print_exc()
    
    def pay_with_kaspa_wallet(self):
        """Pay contract with internal Kaspa wallet."""
        asyncio.create_task(self.controller.pay_with_kaspa_wallet())
        self.ids.payment_field.payment_enabled = False
        self.ids.status_widget.status_text = "KAS payment sent..."
        # Hide cancel button when payment starts
        self.payment_started = True
        self.show_cancel_button = False
    
    def pay_with_external_kaspa_wallet(self):
        """Open external Kaspa wallet to pay contract."""
        self.ids.status_widget.status_text = "Waiting for external KAS payment..."
        self.show_contract_qr_popup(self.ids.contract_field.contract_address, pay_info=True)
        # Hide cancel button when payment starts
        self.payment_started = True
        #self.show_cancel_button = False
    
    def pay_with_ln_wallet(self):
        """Pay invoice with internal LN wallet."""
        asyncio.create_task(self._pay_with_ln_wallet_async())
    
    async def _pay_with_ln_wallet_async(self):
        """Async payment with internal LN wallet."""
        try:
            self.ids.payment_field.payment_enabled = False
            #self.ids.status_widget.status_text = "Paying invoice..."
            self.ids.payment_field.info_text = "Paying invoice..."
            # Hide cancel button when payment starts
            self.payment_started = True
            self.show_cancel_button = False
            
            # Pay invoice and get preimage
            preimage = await self.controller.pay_with_internal_ln_wallet()

            if not preimage:
                # ToDo: maybe show error somewhere, and revert the status
                self.ids.payment_field.payment_enabled = True
                self.ids.status_widget.status_text = "Payment failed"
                self.ids.payment_field.info_text = "Payment failed"
                # Hide cancel button when payment starts
                self.payment_started = False
                self.show_cancel_button = True
                return

            self.ids.payment_field.ids.payment_info_label.text_color = (0, 0.8, 0, 1)
            self.ids.payment_field.info_text = "Invoice paid!"
            self.ids.payment_field.buttons_row_visible = False
            self.ids.payment_field.style = "filled"
            self.ids.payment_field.theme_line_color = "Primary"

            if not self.output_address:
                # ask for output address
                await self.handle_output_address()
            
            # Redeem contract with preimage
            txid = await self.controller.redeem_with_preimage(preimage)
            
            self.ids.status_widget.ids.status_info_label.text_color = (0, 0.8, 0, 1)
            self.ids.status_widget.status_text = f"Swap complete!\nTXID: {txid}"
            self.ids.status_widget.txid = txid
            self.swap_final_state = "completed"
            self.ids.status_widget.countdown_text = ""
            self.show_cancel_button = True
            self.cancel_button_text = "Back to Main Screen"

        except Exception as e:
            print(f"Error paying with LN wallet: {e}")
            self.ids.status_widget.status_text = f"Payment failed: {str(e)}"
            self.ids.payment_field.payment_enabled = True
            self.ids.payment_field.buttons_row_visible = True
    
    def pay_with_external_ln_wallet(self):
        """Open external LN wallet to pay invoice."""
        invoice = self.ids.invoice_field.invoice
        self.ids.status_widget.status_text = "Waiting for external BTC payment..."
        self.show_invoice_qr_popup(invoice, input_preimage=True)
        # Hide cancel button when payment starts
        self.payment_started = True
        #self.show_cancel_button = False

    def on_preimage_input(self, instance, value):
        """Handle preimage input."""
        print(f"preimage input value: {value}")
        # validate the preimage
        if not self.controller._validate_preimage(value):
            return
        # redeem the contract with the preimage
        asyncio.create_task(self._on_preimage_validated(value))

    async def _on_preimage_validated(self, preimage):
        """Handle preimage validation."""
        # redeem the contract with the preimage
        self.ids.payment_field.ids.payment_info_label.text_color = (0, 0.8, 0, 1)
        self.ids.payment_field.info_text = "Invoice paid!"
        self.ids.payment_field.style = "filled"
        self.ids.payment_field.theme_line_color = "Primary"
        self.ids.payment_field.buttons_row_visible = False

        if self.dialog:
                self.dialog.dismiss()
                self.dialog = None

        if not self.output_address:
            # ask for output address
            await self.handle_output_address()

        txid = await self.controller.redeem_with_preimage(preimage)
        if txid:
            
            self.ids.status_widget.ids.status_info_label.text_color = (0, 0.8, 0, 1)
            self.ids.status_widget.status_text = f"Swap complete!\nTXID: {txid}"
            self.ids.status_widget.txid = txid
            self.swap_final_state = "completed"
            self.ids.status_widget.countdown_text = ""
            self.show_cancel_button = True
            self.cancel_button_text = "Back to Main Screen"
    
    def cancel_swap(self):
        """Cancel the swap and reset UI."""
        # If swap is in final state, just reset without confirmation
        if self.swap_final_state:
            self._reset_swap_state()
            return
        
        # Show confirmation dialog only if swap is active
        dialog = MDDialog(
            MDDialogHeadlineText(text="Cancel Swap?"),
            MDDialogContentContainer(
                MDLabel(
                    text="Are you sure you want to cancel?",
                    theme_text_color="Secondary",
                ),
                MDWidget(size_hint_y=None, height=dp(20)),
                MDBoxLayout(
                    MDButton(
                        MDButtonText(text="No"),
                        style="text",
                        on_release=lambda x: dialog.dismiss(),
                    ),
                    MDButton(
                        MDButtonText(text="Yes, Cancel"),
                        style="filled",
                        on_release=lambda x: self._confirm_cancel(dialog),
                    ),
                    spacing=dp(12),
                    adaptive_height=True,
                ),
                orientation="vertical",
                spacing=dp(12),
                padding=dp(16),
            ),
            auto_dismiss=True,
        )
        dialog.open()
    
    def _confirm_cancel(self, dialog):
        """Confirm cancellation."""
        self.controller.cancel_swap()
        self._reset_swap_state()
        dialog.dismiss()
    
    def _reset_swap_state(self):
        """Reset all swap state and UI."""
        # Reset controller
        if self.controller:
            self.controller.reset()
        
        # Reset all component properties to defaults
        self.ids.swap_editor.reset()
        self.ids.invoice_field.reset()
        self.ids.contract_field.reset()
        self.ids.payment_field.reset()
        self.ids.status_widget.reset()
        
        # Reset UI component visibility/state
        self.ids.swap_editor.expand()
        self.ids.peer_selector.expand()
        self.ids.invoice_field.hide()
        self.ids.contract_field.hide()
        self.ids.payment_field.hide()
        self.ids.status_widget.hide()
        
        # Reset state properties
        self.payment_started = False
        self.swap_final_state = ""
        self.show_cancel_button = False
        self.cancel_button_text = "Cancel Swap"
        self.contract_address_qr_image = None
        self.contract_address_qr_texture = None
        self.contract_address_bip21_qr_texture = None
        self.output_address = ""
    
    async def _handle_refund_countdown(self):
        """
        Handle refund safety timeout countdown (180 seconds after swap expiry).
        Shows countdown, then enables refund button.
        """
        status_widget = self.ids.status_widget
        
        # 180 second safety timeout after swap expiry
        REFUND_SAFETY_TIMEOUT = 180
        time_remaining = REFUND_SAFETY_TIMEOUT + int(self.app.taker.swap.timelock / 1e3 - time.time())
        
        while time_remaining > 0:
            minutes = time_remaining // 60
            seconds = time_remaining % 60
            status_widget.refund_countdown_text = f"Refund available in: {minutes:02d}:{seconds:02d}"
            
            # Check if funds are still there
            funded = await self.app.taker.swap.async_check_utxo(timeout=False)
            if funded < self.controller.kas_amount * 0.1:
                # Funds already spent (maker redeemed?)
                status_widget.status_text = "Funds already spent"
                status_widget.show_refund = False
                return
            
            await asyncio.sleep(1)
            time_remaining = REFUND_SAFETY_TIMEOUT + int(self.app.taker.swap.timelock / 1e3 - time.time())
        
        # Timeout expired, enable refund
        status_widget.refund_countdown_text = ""
        status_widget.refund_enabled = True
        status_widget.status_text = "Refund available!"

    async def handle_output_address(self, refund=False):
        """Handle refund address."""
        # step 1: if internal go wallet -> we generate a new address
        # step 2: we open a dialog, with text field, auto-fill if address was generated in step 1
        # step 3: fallback using internal satkas keys -> not yet, we expect the user to set an address somehow
        # Why not step 3? Because getting funds out of satkas internal keys is hard right now.
        # Also, db may be wiped during updates, and we don't want the user to lose funds.
        
        if self.app.service_manager._preferred_wallet == 'go':
            # get a new address
            address = await self.app.service_manager.kaspa_wallet_service.get_new_address()
            print(f"New address: {address}")
        else:
            address = ""

        addr_type = "refund" if refund else "output"

        self.output_address_button = MDButton(
            MDButtonText(text="OK"),
            style="text",
            disabled=True,
            on_release=self.confirm_output_address
        )

        # definition -> binding -> text assignment
        # IN THIS ORDER! This way the callback is called even for auto-filled addresses.
        # This may give issues when compiling though, we may need a better solution later on.
        self.output_address_text_field = MDTextField(
            MDTextFieldHintText(text=f"Enter {addr_type} address"),
            mode="outlined",
            multiline=False,
            max_height=dp(48)
        )
        self.output_address_text_field.bind(text=self.on_output_address_input)
        self.output_address_text_field.text = address

        self.dialog = MDDialog(
            MDDialogHeadlineText(text=f"Set {addr_type} address"),
            MDDialogContentContainer(
                self.output_address_text_field
            ),
            MDDialogButtonContainer(
                MDWidget(),
                self.output_address_button
            ),
            auto_dismiss=False,
        )
        self.dialog.open()
    
        while not self.output_address:
            await asyncio.sleep(0.5)
    
    def on_output_address_input(self, instance, value):
        """Handle refund address input."""
        print(f"Refund address input: {value}")
        # validate address first
        try:
            decode_address(value)
            self.output_address_button.disabled = False
        except Exception as e:
            print(f"Invalid address: {e}")
            self.output_address_button.disabled = True
            return
        
        self.output_address_button.disabled = False

    def confirm_output_address(self, *args):
        """Confirm output address."""
        self.output_address = self.output_address_text_field.text
        self.app.taker.output_address = self.output_address
        self.dialog.dismiss()
        self.dialog = None
    
    def refund_contract(self, refund_address):
        """Trigger refund transaction."""
        asyncio.create_task(self._refund_contract_async(refund_address))
    
    async def _refund_contract_async(self, refund_address):
        """Async refund transaction."""
        status_widget = self.ids.status_widget
        
        try:
            print(f"Refunding to: {refund_address}")
            
            # Set up taker for refund
            self.app.taker.output_address = refund_address
            self.app.taker.swap.sender_private_key = self.app.taker.get_secret_key()
            self.app.taker.swap.output_address = refund_address
            
            # Execute refund
            txid = self.app.taker.swap.spend_contract()
            
            print(f"Refund txid: {txid}")
            status_widget.ids.status_info_label.text_color = (0.8, 0.8, 0, 1)
            status_widget.status_text = f"Refunded!\nTXID: {txid}"
            status_widget.txid = txid
            status_widget.show_refund = False
            self.cancel_button_text = "Back to Main Screen"
            self.show_cancel_button = True
            
            # Update database
            self.app.taker.db_set_swap_txid(txid)
            self.app.taker.db_set_swap_status('REFUNDED')
            
        except Exception as e:
            print(f"Refund failed: {e}")
            status_widget.status_text = f"Refund failed: {str(e)}"
            status_widget.refund_triggered = False
            status_widget.refund_enabled = True
            import traceback
            traceback.print_exc()
    
    def show_invoice_qr_popup(self, invoice, input_preimage=False):
        """Show invoice QR code dialog."""
        qr_texture = make_qr(invoice)
        
        # ToDo: display a informative message for the user and a text input field for the preimage
        if input_preimage:

            preimage_text_field = MDTextField(
                MDTextFieldHintText(text="Insert payment preimage"),
                mode="outlined",
                multiline=False,
                max_height=dp(48)
            )
            preimage_text_field.bind(text=self.on_preimage_input)

            preimage_box = MDBoxLayout(
                MDLabel(
                    text="Pay the invoice and enter the preimage below",
                    halign="center",
                    font_style="Title",
                    role="medium",
                    size_hint_y=None,
                    height=dp(32)
                ),
                MDLabel(
                    text="WARNING: use a LN wallet that shows the payment preimage!",
                    halign="center",
                    font_style="Body",
                    role="large",
                    bold=True,
                    size_hint_y=None,
                    height=dp(32)
                ),
                preimage_text_field,
                orientation="vertical",
                spacing=dp(12),
                size_hint_y=None,
                height=dp(128),
            )
        else:
            preimage_box = MDWidget(size_hint_y=None, height=0)

        self.dialog = MDDialog(
            MDDialogHeadlineText(text="Lightning Invoice", halign="center"),
            MDDialogContentContainer(
                Image(
                    texture=qr_texture,
                    size_hint=(None, None),
                    size=(dp(250), dp(250)),
                    pos_hint={"center_x": 0.5},
                ),
                preimage_box,
                MDButton(
                    MDButtonIcon(icon="content-copy"),
                    MDButtonText(text="Copy"),
                    style="filled",
                    pos_hint={"center_x": 0.5},
                    on_release=lambda x: self.copy_to_clipboard(invoice),
                ),
                orientation="vertical",
                spacing=dp(8),
                #padding=dp(16),
            ),
            MDDialogButtonContainer(
                MDWidget(),
                MDButton(
                    MDButtonText(text="Close"),
                    style="text",
                    pos_hint={"right": 1},
                    on_release=lambda x: self.dialog.dismiss(),
                )
            ),
            auto_dismiss=True,
        )
        self.dialog.open()
    
    def show_contract_qr_popup(self, address, pay_info=False):
        """Show contract address QR code dialog."""
        
        self.contract_address_qr_texture = make_qr(address)
        self.contract_address_bip21_qr_texture = make_qr(f"{address}?amount={self.ids.swap_editor.kas_amount}")

        self.contract_address_qr_image = Image(
            texture=self.contract_address_qr_texture if not pay_info else self.contract_address_bip21_qr_texture,
            size_hint=(None, None),
            size=(dp(250), dp(250)),
            pos_hint={"center_x": 0.5},
        )

        if pay_info:
            self.bip_21_box = MDBoxLayout(
                    MDLabel(
                        text=f"Send {self.ids.swap_editor.kas_amount} KAS to this address",
                        halign="center",
                        font_style="Title",
                        role="medium",
                        size_hint_y=None,
                        height=dp(32)
                    ),
                    MDBoxLayout(
                        # MDWidget(),
                        MDLabel(
                            text="Include amount in QR code (BIP-21)",
                            font_style="Body",
                            role="small",
                            size_hint_y=None,
                            height=dp(32)
                        ),
                        MDCheckbox(
                            active=True,
                            on_active=self.update_bip21_qr,
                        ),
                        # MDWidget(),
                        orientation="horizontal",
                        pos_hint={"center_x": 0.5},
                        spacing=dp(12),
                        #adaptive_width=True,
                        size_hint=(None, None),
                        height=dp(32),
                        width=dp(250),
                    ),
                orientation="vertical",
                spacing=dp(8),
                size_hint_y=None,
                height=dp(72),
            )
        else:
            self.bip_21_box = MDBoxLayout(size_hint_y=None, height=0)

        self.dialog = MDDialog(
            MDDialogHeadlineText(text="Contract Address", halign="center"),
            MDDialogContentContainer(
                self.contract_address_qr_image,
                self.bip_21_box,
                MDButton(
                    MDButtonIcon(icon="content-copy"),
                    MDButtonText(text="Copy"),
                    style="filled",
                    pos_hint={"center_x": 0.5},
                    on_release=lambda x: self.copy_to_clipboard(address),
                ),
                orientation="vertical",
                spacing=dp(8),
                # adaptive_height=True,
            ),
            MDDialogButtonContainer(
                MDWidget(),
                MDButton(
                    MDButtonText(text="Close"),
                    style="text",
                    pos_hint={"right": 1},
                    on_release=lambda x: self.dialog.dismiss(),
                )
            ),
            auto_dismiss=True,
        )
        self.dialog.open()

    def update_bip21_qr(self, instance, value):
        print(f"Updating BIP-21 QR ({instance}): {value}")
        self.contract_address_qr_image.texture = self.contract_address_bip21_qr_texture if value else self.contract_address_qr_texture
    
    def copy_to_clipboard(self, text):
        """Copy text to clipboard."""
        Clipboard.copy(text.strip())
        print(f"Copied: {text}")

    def dismiss_dialog(self, *args):
        """Dismiss dialog."""
        if self.dialog:
            self.dialog.dismiss()
            self.dialog = None