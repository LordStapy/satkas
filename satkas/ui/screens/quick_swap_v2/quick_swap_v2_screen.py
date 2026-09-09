"""
Quick Swap V2 Screen - Clean, single-column implementation
"""

import asyncio
import logging
import os
import time
from kivy.app import App
from kivy.clock import Clock
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
from kivymd.uix.textfield import MDTextField, MDTextFieldHintText
from kivymd.uix.widget import MDWidget

from satkas.ui.utils import make_qr
# from satkas.core.klib.kaddress import decode_address  # used by retired ask_output_address
from .swap_controller import SwapController
from .components import (
    PeerSelectorCard,
    SwapEditor,
    PayoutAddressBar,
    InvoiceField,
    ContractAddressField,
    PaymentField,
    StatusWidget
)

logger = logging.getLogger('quick_swap')

# Load KV file
components_dir = os.path.join(os.path.dirname(__file__), 'components')
Builder.load_file(os.path.join(os.path.dirname(__file__), 'quick_swap_v2_screen.kv'))
Builder.load_file(os.path.join(components_dir, 'peer_selector.kv'))
Builder.load_file(os.path.join(components_dir, 'swap_editor.kv'))
Builder.load_file(os.path.join(components_dir, 'payout_bar.kv'))
Builder.load_file(os.path.join(components_dir, 'invoice_field.kv'))
Builder.load_file(os.path.join(components_dir, 'contract_field.kv'))
Builder.load_file(os.path.join(components_dir, 'payment_field.kv'))
Builder.load_file(os.path.join(components_dir, 'status_widget.kv'))


class QuickSwapV2Screen(MDScreen):
    """Single-column swap screen"""
    
    # Cancel/Back button state
    payment_started = BooleanProperty(False)
    # "", "settling", "completed", "refunded", "expired", "failed"
    swap_final_state = StringProperty("")
    show_cancel_button = BooleanProperty(False)
    cancel_button_text = StringProperty("Cancel Swap")

    output_address = StringProperty("")
    # True from Start tap until abort (no quote) or _reset_swap_state.
    # KV disables Start from this; do not set button.disabled from Python.
    start_in_flight = BooleanProperty(False)
    resume_active = BooleanProperty(False)
    resume_finished = BooleanProperty(False)
    resume_headline = StringProperty("Interrupted swap")
    resume_detail = StringProperty("")
    resume_status_text = StringProperty("Checking…")
    
    def __init__(self, **kwargs):
        self.app = App.get_running_app()
        self.refresh_rate_task = None
        self.amount_debounce_task = None
        self.dialog = None
        # True while the optional contract-funding QR dialog is the active dialog.
        self._is_funding_qr_dialog = False
        
        # Track valid_until per direction
        self._kas2sat_valid_until = 0
        self._sat2kas_valid_until = 0
        self._kas2btc_valid_until = 0
        self._btc2kas_valid_until = 0
        self._resume_saw_swap = False

        # Initialize controller; interaction is bound once KV has wired ids.
        self.controller = SwapController(
            self.app.taker,
            self.app.service_manager,
        )
        super().__init__(**kwargs)
        self.name = "quick_swap_v2_screen"
        
    
    def on_kv_post(self, base_widget):
        """Called after KV is loaded."""
        if not getattr(self, 'app', None):
            self.app = App.get_running_app()
        
        # Setup component references
        self.ids.peer_selector.screen = self
        self.ids.swap_editor.screen = self
        self.ids.payout_bar.screen = self
        self.ids.invoice_field.screen = self
        self.ids.contract_field.screen = self
        self.ids.btc_contract_field.screen = self
        self.ids.payment_field.screen = self
        self.ids.status_widget.screen = self
        self.controller.bind_screen(self)
        editor = self.ids.swap_editor
        editor.bind(
            swap_mode=lambda *_: self.ids.payout_bar.sync_from_editor(editor),
            swap_direction=lambda *_: self.ids.payout_bar.sync_from_editor(editor),
        )
        self.ids.payout_bar.sync_from_editor(editor)
        self._attach_resume_ui()
    
    _RESUME_STATUS = {
        'swap_initialized': 'Restored',
        'monitoring': 'Watching contract',
        'funded': 'Funded — waiting for redeem',
        'waiting_confirmations': 'Waiting for confirmations',
        'waiting_counterparty': 'Waiting for counterparty',
        'ready_to_redeem': 'Redeeming',
        'btc_funded': 'BTC funded',
        'expired': 'Expired — refunding',
        'completing': 'Redeem broadcast',
        'completed': 'Completed',
        'refunding': 'Refund broadcast',
        'refunded': 'Refunded',
        'failed': 'Failed',
    }
    _RESUME_FUNDED_EVENTS = {
        'funded', 'waiting_confirmations', 'waiting_counterparty',
        'ready_to_redeem', 'btc_funded', 'completing', 'completed',
        'refunding', 'refunded', 'failed',
    }

    def _attach_resume_ui(self):
        taker = self.app.taker
        task = getattr(taker, '_resume_task', None)
        if task is None or task.done():
            return
        self._resume_saw_swap = False
        taker.on_event = self._on_resume_event
        task.add_done_callback(
            lambda t: Clock.schedule_once(lambda dt: self._on_resume_done(t), 0)
        )

    def _on_resume_event(self, event):
        status = getattr(event, 'status', '') or ''
        row = getattr(self.app.taker, 'db_swap', None)
        row_status = getattr(row, 'status', '') if row else ''
        swap_type = getattr(row, 'swap_type', '') if row else ''
        kas = getattr(event, 'kas_amount', None)
        sat = getattr(event, 'sat_amount', None)
        if not self.resume_active:
            funded = row_status == 'FUNDED' or status in self._RESUME_FUNDED_EVENTS
            if not funded:
                return
            self.resume_active = True
            if self.refresh_rate_task and not self.refresh_rate_task.done():
                self.refresh_rate_task.cancel()
        self._resume_saw_swap = True
        if swap_type:
            self.resume_headline = f"Completing {swap_type}"
        if kas or sat:
            self.resume_detail = f"{kas or '—'} KAS  ·  {sat or '—'} sats"
        payload = event.as_dict() if hasattr(event, 'as_dict') else {}
        countdown = self._countdown_text(payload)
        label = self._RESUME_STATUS.get(status, status.replace('_', ' ') or 'Working')
        self.resume_status_text = f"{label}" + (f"  ·  {countdown}" if countdown else "")

    def _on_resume_done(self, task):
        err = None if task.cancelled() else task.exception()
        if not getattr(self, '_resume_saw_swap', False):
            return
        self.resume_finished = True
        if err:
            self.resume_status_text = f"Stopped: {err}"
        elif task.cancelled():
            self.resume_status_text = "Stopped"

    def dismiss_resume_panel(self):
        self.resume_active = False
        self.resume_finished = False
        self.restart_rate_refresh()
        if not self.ids.payout_bar.is_locked:
            asyncio.create_task(self.ids.payout_bar.apply_persisted())

    def on_enter(self):
        """Called when entering the screen."""
        if self.app and hasattr(self.app, 'root'):
            main_screen = self.app.root.get_screen('main_screen')
            if hasattr(main_screen, 'children') and main_screen.children:
                main_screen.children[0].ids.top_bar_title.text = "Quick Swap V2"
        
        if self.resume_active:
            return
        # Start rate refresh
        self.refresh_rate_task = asyncio.create_task(self.refresh_rates())

        if not self.ids.payout_bar.is_locked:
            asyncio.create_task(self.ids.payout_bar.apply_persisted())

        # Replay the last event so a leave mid-swap rebuilds UI state.
        if (
            self.controller.is_active
            and not self.resume_active
            and self.controller.last_event is not None
        ):
            asyncio.create_task(
                self.on_swap_update(self.controller.last_event.as_dict())
            )
    
    def on_leave(self):
        """Called when leaving the screen.

        Only the rate ticker is cancelled. The swap task lives on the
        controller and keeps running — an on-chain swap cannot be abandoned
        because the user pressed back.
        """
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
    
    def _rate_swap_types(self):
        """Return (send_kas_type, send_btc_type) for the current editor mode."""
        editor = self.ids.swap_editor
        if editor.swap_mode == "onchain":
            return "kas2btc", "btc2kas"
        return "kas2sat", "sat2kas"

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
                send_kas_type, send_btc_type = self._rate_swap_types()
                
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
                        editor.last_quote = None
                        editor.is_fetching_rate = False
                        editor.validate()
                        peer_selector.update_status("offline", "No peers available")
                        await asyncio.sleep(30)
                        continue
                else:
                    # Manual peer selection
                    peer_selector.update_status("checking", f"Fetching rates {amount_info}...".strip())
                    peer = peer_selector.selected_peer
                    peer_selector.actual_peer = peer
                    self.app.taker.maker_endpoint = peer
                    
                    logger.debug(f"{time.ctime()} - Querying {peer} for {kas_amount:.0f} KAS ({send_kas_type}/{send_btc_type})")
                    
                    # Query both directions concurrently from selected peer
                    results = await asyncio.gather(
                        self.app.taker.query_price(send_kas_type, kas_amount),
                        self.app.taker.query_price(send_btc_type, kas_amount),
                        return_exceptions=True
                    )
                    
                    kas2sat_res, sat2kas_res = results
                    new_kas2sat_rate = 0
                    new_sat2kas_rate = 0
                    
                    if kas2sat_res and not isinstance(kas2sat_res, Exception):
                        new_kas2sat_rate = list(kas2sat_res[0].values())[0][0] if kas2sat_res[0] else 0
                        send_kas_until = kas2sat_res[1]
                    else:
                        send_kas_until = time.time() + 30
                    
                    if sat2kas_res and not isinstance(sat2kas_res, Exception):
                        new_sat2kas_rate = list(sat2kas_res[0].values())[0][0] if sat2kas_res[0] else 0
                        send_btc_until = sat2kas_res[1]
                    else:
                        send_btc_until = time.time() + 30

                    if editor.swap_mode == "onchain":
                        self._kas2btc_valid_until = send_kas_until
                        self._btc2kas_valid_until = send_btc_until
                    else:
                        self._kas2sat_valid_until = send_kas_until
                        self._sat2kas_valid_until = send_btc_until
                
                if (
                    editor.last_quote
                    and editor.last_quote.get('peer')
                    and editor.last_quote['peer'] != peer_selector.actual_peer
                ):
                    editor.last_quote = None

                # Update rates for the active mode (off-chain and on-chain offers are independent)
                if editor.swap_mode == "onchain":
                    editor.kas2btc_rate = new_kas2sat_rate
                    editor.btc2kas_rate = new_sat2kas_rate
                else:
                    editor.kas2sat_rate = new_kas2sat_rate
                    editor.sat2kas_rate = new_sat2kas_rate

                peer = peer_selector.actual_peer
                if editor.anchor_field == "btc" and editor.btc_amount > 0:
                    q_kas, q_sat = None, round(editor.btc_amount * 1e8)
                elif editor.kas_amount > 0:
                    q_kas, q_sat = editor.kas_amount, None
                elif editor.btc_amount > 0:
                    q_kas, q_sat = None, round(editor.btc_amount * 1e8)
                else:
                    q_kas, q_sat = None, None

                if peer and (q_kas or q_sat):
                    editor.is_fetching_rate = True
                    editor.validate()
                    quoted = await self.app.taker.query_quote(
                        editor.swap_direction,
                        kas_amount=q_kas,
                        sat_amount=q_sat,
                        endpoint=peer,
                    )
                    if quoted:
                        quoted['peer'] = peer
                        editor.apply_quote(quoted)
                    else:
                        editor.clear_quote()
                else:
                    editor.is_fetching_rate = False
                    editor.validate()
                
                # Update peer status based on results (ONCE at end)
                if new_kas2sat_rate > 0 or new_sat2kas_rate > 0:
                    peer_selector.update_status("online", editor=editor)
                else:
                    peer_selector.update_status("offline", "No rates available")
                
                # Sleep until earliest expiration
                if editor.swap_mode == "onchain":
                    next_refresh = min(self._kas2btc_valid_until, self._btc2kas_valid_until)
                else:
                    next_refresh = min(self._kas2sat_valid_until, self._sat2kas_valid_until)
                if editor.last_quote and editor.last_quote.get('valid_until'):
                    next_refresh = min(next_refresh, editor.last_quote['valid_until'])
                sleep_time = max(next_refresh - time.time(), 0.5)
                
                await asyncio.sleep(sleep_time)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error refreshing rates: {e}")
                self.ids.swap_editor.clear_quote()
                self.ids.peer_selector.update_status("offline", "Error fetching rates from peer")
                await asyncio.sleep(30)
    
    async def _auto_select_best_peer(self, kas_amount):
        """Query all peers concurrently and select best rate.
        
        Returns:
            tuple: (best_peer, send_kas_rate, send_btc_rate)
        """
        available_peers = self.ids.peer_selector.available_peers[1:]  # Skip "auto"
        send_kas_type, send_btc_type = self._rate_swap_types()
        
        if not available_peers:
            return None, 0, 0
        
        logger.debug(f"{time.ctime()} - Auto-selecting: querying {len(available_peers)} peers for {kas_amount:.0f} KAS ({send_kas_type}/{send_btc_type})")
        
        # Query all peers concurrently using endpoint parameter
        tasks = []
        for peer in available_peers:
            # Query both directions from this peer
            task_kas2sat = self.app.taker.query_price(send_kas_type, kas_amount, endpoint=peer)
            task_sat2kas = self.app.taker.query_price(send_btc_type, kas_amount, endpoint=peer)
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
        sends_kas = editor.sends_kas
        
        best_peer = None
        best_rate = 0 if sends_kas else float('inf')  # Max when selling KAS, min when buying
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
            current_rate = kas2sat_rate if sends_kas else sat2kas_rate
            
            # send KAS: maximize rate (sell KAS for more BTC)
            # send BTC: minimize rate (buy KAS cheaper, fewer sats per KAS)
            is_better = False
            if sends_kas:
                is_better = current_rate > best_rate and current_rate > 0
            else:
                is_better = 0 < current_rate < best_rate
            
            if is_better:
                best_rate = current_rate
                best_peer = peer
                best_kas2sat_rate = kas2sat_rate
                best_sat2kas_rate = sat2kas_rate
                best_valid_until = valid_until
            
            # print(f"  {peer[:20]}...: send_kas={kas2sat_rate:.2f}, send_btc={sat2kas_rate:.2f}")
        
        if best_peer:
            logger.debug(f"  → Selected: {best_peer[:20]}... (best {editor.swap_direction} rate: {best_rate:.2f})")
            if editor.swap_mode == "onchain":
                self._kas2btc_valid_until = best_valid_until
                self._btc2kas_valid_until = best_valid_until
            else:
                self._kas2sat_valid_until = best_valid_until
                self._sat2kas_valid_until = best_valid_until
            # Set the best peer as the maker endpoint
            self.app.taker.maker_endpoint = best_peer
        else:
            logger.debug(f"  → No peers available")
        
        return best_peer, best_kas2sat_rate, best_sat2kas_rate

    def apply_invoice_to_preview(self, invoice_data):
        """
        Apply parsed invoice during preview phase.
        Decode is local; quote uses sat_amount only (bolt11 waits until init).
        """
        editor = self.ids.swap_editor
        invoice_field = self.ids.invoice_field
        sats = invoice_data.get('amount_sat', 0)

        editor.btc_amount = sats / 1e8
        editor.anchor_field = "btc"
        editor.last_quote = None
        editor.calculate_kas_from_btc()
        editor.get_send_amount_display()
        editor.get_receive_amount_display()
        editor.is_fetching_rate = True
        editor.validate()

        invoice_field.invoice = invoice_data['invoice_str']
        invoice_field.is_invoice_validated = True
        invoice_field.info_label_text = f"Invoice set: {sats} sats"
        invoice_field.swap_direction = "kas2sat"
        invoice_field.buttons_row_visible = False

        logger.debug(f"Invoice applied to preview: {sats} sats")
        self.restart_rate_refresh()
    
    def start_swap(self):
        if self.resume_active or self.start_in_flight:
            return
        self.start_in_flight = True
        asyncio.create_task(self._start_swap())
    
    async def _start_swap(self):
        """Collapse the editor and hand the flow to Taker.run_*."""
        editor = self.ids.swap_editor
        invoice_field = self.ids.invoice_field
        committed = False
        try:
            if not editor.is_valid:
                return
            if not self.ids.payout_bar.is_ready:
                return

            quote = editor.last_quote
            if (
                not quote
                or quote.get('swap_type') != editor.swap_direction
                or (quote.get('valid_until') or 0) < time.time()
            ):
                if editor.anchor_field == "btc" and editor.btc_amount > 0:
                    q_kas, q_sat = None, round(editor.btc_amount * 1e8)
                elif editor.kas_amount > 0:
                    q_kas, q_sat = editor.kas_amount, None
                elif editor.btc_amount > 0:
                    q_kas, q_sat = None, round(editor.btc_amount * 1e8)
                else:
                    q_kas, q_sat = None, None
                peer = self.ids.peer_selector.actual_peer or self.app.taker.maker_endpoint
                quote = None
                if peer and (q_kas or q_sat):
                    quote = await self.app.taker.query_quote(
                        editor.swap_direction,
                        kas_amount=q_kas,
                        sat_amount=q_sat,
                        endpoint=peer,
                    )
                    if quote:
                        quote['peer'] = peer
                        editor.apply_quote(quote)
            if not quote:
                editor.clear_quote()
                return

            logger.info(f"Starting swap: {editor.swap_direction}, KAS: {quote['kas_amount']}, sats: {quote['sat_amount']}, rate: {quote['price']}")

            payout = self.ids.payout_bar
            self.app.taker.output_address = payout.kas_address
            if editor.swap_mode == "onchain":
                self.app.taker.btc_output_address = payout.btc_address
            payout.is_locked = True

            editor.collapse()
            committed = True
            editor.get_summary_display()
            self.ids.peer_selector.collapse()

            self.payment_started = False
            self.swap_final_state = ""
            self.show_cancel_button = True
            self.cancel_button_text = "Cancel Swap"
            self.output_address = ""

            invoice_field.swap_direction = editor.swap_direction
            invoice_field.is_ln_wallet_external = self.app.service_manager.ln_wallet_is_external()

            status_widget = self.ids.status_widget
            status_widget.swap_direction = editor.swap_direction
            status_widget.status_text = "Starting swap..."
            status_widget.show()

            try:
                self.controller.start_swap(
                    editor.swap_direction,
                    quote['kas_amount'],
                    quote['sat_amount'],
                    quote['price'],
                    self.on_swap_update,
                )
            except Exception as e:
                logger.error(f"Error starting swap: {e}")
                import traceback
                traceback.print_exc()
                editor_collapsed_icon = self.ids.swap_editor.ids.editor_collapsed_icon
                editor_collapsed_icon.icon = "close-circle"
                editor_collapsed_icon.text_color = (1, 0.3, 0.3, 1)
                status_widget.status_text = f"Swap failed: {e}"
                status_widget.countdown_text = ""
                self.ids.payout_bar.is_locked = False
                self.swap_final_state = "failed"
                self.show_cancel_button = True
                self.cancel_button_text = "Back to Main Screen"
        finally:
            if not committed:
                self.start_in_flight = False
    
    @staticmethod
    def _countdown_text(update: dict, *, refund: bool = False) -> str:
        """Format locktime remaining from an event payload.

        Preference matches the chain unit the core emits:
        BTC blocks → KAS DAA → LN wall-clock seconds.
        """
        blocks = update.get('blocks_remaining')
        if blocks is not None:
            label = "Blocks until refund" if refund else "Blocks remaining"
            return f"{label}: {max(int(blocks), 0)}"
        daa = update.get('daa_remaining')
        if daa is not None:
            label = "DAA until refund" if refund else "DAA remaining"
            return f"{label}: {max(int(daa), 0)}"
        time_remaining = update.get('time_remaining')
        if time_remaining is not None:
            minutes = max(int(time_remaining), 0) // 60
            seconds = max(int(time_remaining), 0) % 60
            if refund:
                return f"Refund available in: {minutes:02d}:{seconds:02d}"
            return f"Time remaining: {minutes:02d}:{seconds:02d}"
        return ""

    async def on_swap_update(self, update: dict):
        """Handle swap status updates from controller.

        Renders only. Redeem and refund are owned by the orchestrator; spawning
        them from here was commented during phase 5 of the taker/controller
        integration.
        """
        # Soft leave (Back during completing/refunding) detaches the UI; late
        # promote events must not resurrect widgets on a reset screen.
        if not self.controller.is_active:
            return

        status = update.get('status')
        status_widget = self.ids.status_widget

        if status == 'swap_initialized':
            await self._apply_swap_initialized(update)

        elif status == 'awaiting_manual_funding':
            address = update.get('contract_address') or update.get('btc_contract_address', '')
            amount = update.get('kas_amount')
            sat_amount = update.get('sat_amount')
            if sat_amount is not None:
                status_widget.status_text = (
                    f"Send {sat_amount} sats to\n{address}"
                )
            elif amount is not None:
                status_widget.status_text = (
                    f"Send {amount} KAS to\n{address}"
                )
            else:
                status_widget.status_text = f"Fund manually:\n{address}"

        elif status == 'monitoring':
            funded = update.get('funded', 0) or 0
            if self.swap_final_state == 'expired':
                # Grace / locktime wait after expiry: keep the countdown alive.
                status_widget.countdown_text = self._countdown_text(update, refund=True)
                status_widget.status_text = "Swap expired, waiting for refund..."
                self.show_cancel_button = False
            else:
                status_widget.countdown_text = self._countdown_text(update)
                if funded > 0:
                    status_widget.status_text = f"Funded: {funded:.4f} KAS"
                    self._dismiss_funding_dialog()
                    self.ids.contract_field.ids.contract_status_label.text_color = (0.8, 0.8, 0, 1)
                    funded_display = self.ids.swap_editor.format_kas(funded)
                    kas_amount_display = self.ids.swap_editor.format_kas(self.controller.kas_amount)
                    self.ids.contract_field.status_text = (
                        f"Partially funded: {funded_display} / {kas_amount_display} KAS"
                    )
                    status_widget.is_funded = True
                    self.show_cancel_button = False
                else:
                    status_widget.status_text = "Waiting for funding..."

        elif status == 'funded':
            self._dismiss_funding_dialog()
            self.ids.payment_field.stop_attention_pulse()
            self.ids.payment_field.style = "filled"
            self.ids.payment_field.theme_line_color = "Primary"
            direction = self.controller.swap_direction
            if direction == 'kas2btc':
                status_widget.status_text = "KAS funded — waiting for confirmations..."
                self.ids.contract_field.ids.contract_status_label.text_color = (0, 0.8, 0, 1)
                self.ids.contract_field.status_text = "Funded!"
                self.ids.payment_field.ids.payment_info_label.text_color = (0, 0.8, 0, 1)
                self.ids.payment_field.info_text = "Done!"
                self.ids.payment_field.buttons_row_visible = False
                status_widget.is_focused = True
            elif direction == 'btc2kas':
                # Maker's KAS just arrived (our BTC already done).
                status_widget.status_text = "KAS funded — waiting for confirmations..."
                self.ids.contract_field.ids.contract_status_label.text_color = (0.8, 0.8, 0, 1)
                self.ids.contract_field.status_text = "Funded"
            else:
                status_widget.status_text = "Waiting for redeem by maker..."
                self.ids.contract_field.ids.contract_status_label.text_color = (0, 0.8, 0, 1)
                self.ids.contract_field.status_text = "Funded!"
                self.ids.payment_field.ids.payment_info_label.text_color = (0, 0.8, 0, 1)
                self.ids.payment_field.info_text = "Done!"
                self.ids.payment_field.buttons_row_visible = False
                status_widget.is_focused = True
            status_widget.is_funded = True
            status_widget.countdown_text = self._countdown_text(update)
            self.show_cancel_button = False

        elif status == 'ready_to_pay':
            status_widget.countdown_text = self._countdown_text(update)
            if not self.payment_started:
                self.ids.contract_field.is_confirmed = True
                self.ids.contract_field.status_text = "Confirmed!"
                status_widget.status_text = "Contract confirmed - Pay invoice now"
                payment_field = self.ids.payment_field
                payment_field.info_text = "Ready to pay"
                payment_field.payment_enabled = True
                payment_field.show()
                payment_field.start_attention_pulse()

        elif status == 'waiting_user_payment':
            if not self.swap_final_state:
                status_widget.countdown_text = self._countdown_text(update)
            else:
                status_widget.countdown_text = ""

        elif status == 'completing':
            txid = update.get('txid')
            status_widget.ids.status_info_label.text_color = (0.8, 0.8, 0, 1)
            if txid:
                status_widget.status_text = (
                    f"Redeem broadcast — waiting for confirmations...\nTXID: {txid}"
                )
                status_widget.txid = str(txid)
            else:
                status_widget.status_text = "Redeem broadcast — waiting for confirmations..."
            status_widget.countdown_text = ""
            # Broadcast done; promote continues in background. Treat as leave-safe.
            self.swap_final_state = "settling"
            self.show_cancel_button = True
            self.cancel_button_text = "Back to Main Screen"

        elif status == 'completed':
            status_widget.ids.status_info_label.text_color = (0, 0.8, 0, 1)
            txid = update.get('txid')
            if txid:
                status_widget.status_text = f"Swap completed!\nTXID: {txid}"
                status_widget.txid = str(txid)
            else:
                status_widget.status_text = "Swap completed!"
            status_widget.countdown_text = ""
            self.swap_final_state = "completed"
            status_widget.is_focused = False
            self.show_cancel_button = True
            self.cancel_button_text = "Back to Main Screen"

        elif status == 'refunding':
            txid = update.get('txid')
            status_widget.ids.status_info_label.text_color = (0.8, 0.8, 0, 1)
            if txid:
                status_widget.status_text = (
                    f"Refund broadcast — waiting for confirmations...\nTXID: {txid}"
                )
                status_widget.txid = str(txid)
            else:
                status_widget.status_text = "Refund broadcast — waiting for confirmations..."
            status_widget.countdown_text = ""
            status_widget.show_refund = False
            # Broadcast done; promote continues in background. Treat as leave-safe.
            self.swap_final_state = "settling"
            self.show_cancel_button = True
            self.cancel_button_text = "Back to Main Screen"

        elif status == 'refunded':
            status_widget.ids.status_info_label.text_color = (0.8, 0.8, 0, 1)
            txid = update.get('txid')
            if txid:
                status_widget.status_text = f"Refunded!\nTXID: {txid}"
                status_widget.txid = str(txid)
            else:
                status_widget.status_text = "Refunded!"
            status_widget.countdown_text = ""
            status_widget.show_refund = False
            self.swap_final_state = "refunded"
            status_widget.is_focused = False
            self.show_cancel_button = True
            self.cancel_button_text = "Back to Main Screen"

        elif status == 'failed':
            editor_collapsed_icon = self.ids.swap_editor.ids.editor_collapsed_icon
            editor_collapsed_icon.icon = "close-circle"
            editor_collapsed_icon.text_color = (1, 0.3, 0.3, 1)
            status_widget.ids.status_info_label.text_color = (0.8, 0, 0, 1)
            status_widget.status_text = f"Swap failed: {update.get('error', 'unknown error')}"
            status_widget.countdown_text = ""
            self.swap_final_state = "failed"
            status_widget.is_focused = False
            self.show_cancel_button = True
            self.cancel_button_text = "Back to Main Screen"

        elif status == 'expired':
            self.ids.payment_field.stop_attention_pulse()
            self.ids.payment_field.payment_enabled = False
            self.ids.payment_field.style = "filled"
            self.ids.payment_field.theme_line_color = "Primary"
            self.swap_final_state = "expired"
            status_widget.is_expired = True
            status_widget.show_refund = False
            status_widget.countdown_text = self._countdown_text(update, refund=True)
            if self.controller.contract_funded or self.controller.btc_contract_funded or status_widget.is_funded:
                status_widget.ids.status_info_label.text_color = (0.8, 0.8, 0, 1)
                status_widget.status_text = "Swap expired, waiting for refund..."
                self.show_cancel_button = False
            else:
                status_widget.ids.status_info_label.text_color = (0.8, 0, 0, 1)
                status_widget.status_text = "Swap expired"
                status_widget.is_focused = False
                self.show_cancel_button = True
                self.cancel_button_text = "Back to Main Screen"
            # Commented during phase 5 of the taker/controller integration:
            # the orchestrator refunds on its own.
            # if self.controller.swap_direction == "kas2sat" and status_widget.is_funded:
            #     asyncio.create_task(self._handle_refund_countdown())
            # elif self.controller.is_onchain and (...):
            #     asyncio.create_task(self._refund_onchain_async())

        elif status == 'waiting_confirmations':
            self._dismiss_funding_dialog()
            status_widget.countdown_text = self._countdown_text(update)
            # Detail lives on the status widget; contract field stays "Funded"
            # until confirmations clear (then waiting_counterparty → Confirmed!).
            if update.get('btc_funded'):
                self.ids.btc_contract_field.ids.contract_status_label.text_color = (0.8, 0.8, 0, 1)
                self.ids.btc_contract_field.status_text = "Funded"
                status_widget.status_text = "BTC funded — waiting for confirmations..."
            else:
                self.ids.contract_field.ids.contract_status_label.text_color = (0.8, 0.8, 0, 1)
                self.ids.contract_field.status_text = "Funded"
                status_widget.status_text = "KAS funded — waiting for confirmations..."

        elif status == 'btc_funded':
            self._dismiss_funding_dialog()
            status_widget.countdown_text = self._countdown_text(update)
            direction = self.controller.swap_direction
            if direction in ('sat2kas', 'btc2kas'):
                self.ids.payment_field.stop_attention_pulse()
                self.ids.payment_field.style = "filled"
                self.ids.payment_field.theme_line_color = "Primary"
                self.ids.payment_field.ids.payment_info_label.text_color = (0, 0.8, 0, 1)
                self.ids.payment_field.info_text = "Done!"
                self.ids.payment_field.buttons_row_visible = False
                status_widget.is_focused = True
            if direction == 'sat2kas':
                status_widget.status_text = "Invoice paid — redeeming..."
            else:
                self.ids.btc_contract_field.ids.contract_status_label.text_color = (0.8, 0.8, 0, 1)
                self.ids.btc_contract_field.status_text = "Funded"
                status_widget.status_text = "BTC funded — waiting for confirmations..."
            self.show_cancel_button = False

        elif status == 'waiting_counterparty':
            status_widget.countdown_text = self._countdown_text(update)
            direction = self.controller.swap_direction
            if direction == 'kas2btc':
                # Our KAS confirmations cleared; waiting on maker BTC.
                self.ids.contract_field.is_confirmed = True
                self.ids.contract_field.ids.contract_status_label.text_color = (0, 0.8, 0, 1)
                self.ids.contract_field.status_text = "Confirmed!"
                status_widget.status_text = "Waiting for maker to fund BTC..."
            elif direction == 'btc2kas':
                # Our BTC confirmations cleared; waiting on maker KAS.
                self.ids.btc_contract_field.is_confirmed = True
                self.ids.btc_contract_field.ids.contract_status_label.text_color = (0, 0.8, 0, 1)
                self.ids.btc_contract_field.status_text = "Confirmed!"
                status_widget.status_text = "Waiting for maker to fund KAS..."
            else:
                status_widget.status_text = "Waiting for counterparty funding..."

        elif status == 'ready_to_redeem':
            direction = self.controller.swap_direction
            if direction == 'kas2btc' or update.get('btc_funded'):
                self.ids.btc_contract_field.is_confirmed = True
                self.ids.btc_contract_field.ids.contract_status_label.text_color = (0, 0.8, 0, 1)
                self.ids.btc_contract_field.status_text = "Confirmed!"
                self.ids.contract_field.is_confirmed = True
                self.ids.contract_field.ids.contract_status_label.text_color = (0, 0.8, 0, 1)
                if self.ids.contract_field.status_text != "Confirmed!":
                    self.ids.contract_field.status_text = "Confirmed!"
            else:
                # btc2kas (and any kas-redeem path): KAS is the redeem target.
                self.ids.contract_field.is_confirmed = True
                self.ids.contract_field.ids.contract_status_label.text_color = (0, 0.8, 0, 1)
                self.ids.contract_field.status_text = "Confirmed!"
                if self.controller.is_onchain:
                    self.ids.btc_contract_field.is_confirmed = True
                    self.ids.btc_contract_field.ids.contract_status_label.text_color = (0, 0.8, 0, 1)
                    self.ids.btc_contract_field.status_text = "Confirmed!"
            status_widget.status_text = "Ready to redeem — claiming funds..."
            status_widget.countdown_text = self._countdown_text(update)
            self.payment_started = True
            # Commented during phase 5 of the taker/controller integration:
            # asyncio.create_task(self._redeem_onchain_async())

        elif status == 'waiting_funding':
            status_widget.countdown_text = self._countdown_text(update)
            status_widget.status_text = "Waiting for contract funding..."
            direction = self.controller.swap_direction
            if direction in ('kas2sat', 'kas2btc'):
                self.ids.contract_field.status_text = "Waiting for your funding..."
            elif direction == 'btc2kas':
                self.ids.btc_contract_field.status_text = "Waiting for your funding..."

        elif status == 'waiting_btc_funding':
            status_widget.status_text = "Waiting for your BTC funding..."
            status_widget.countdown_text = self._countdown_text(update)
            self.ids.btc_contract_field.status_text = "Waiting for your funding..."

    async def _apply_swap_initialized(self, update: dict):
        """Populate contract / invoice widgets from the swap_initialized event."""
        editor = self.ids.swap_editor
        invoice_field = self.ids.invoice_field
        is_onchain = editor.swap_mode == "onchain"
        direction = self.controller.swap_direction

        invoice = update.get('invoice')
        if invoice and direction in ("sat2kas", "kas2sat"):
            invoice_field.invoice = invoice
            try:
                decoded = await self.app.service_manager.ln_wallet_service.decode_invoice(invoice)
                amount_sats = decoded.get('amount_msat', 0) // 1000
            except Exception:
                amount_sats = update.get('sat_amount') or self.controller.sat_amount
            if direction == "sat2kas":
                invoice_field.info_label_text = f"Invoice received from maker: {amount_sats} sats"
            else:
                invoice_field.info_label_text = f"Invoice set: {amount_sats} sats"
            invoice_field.is_invoice_validated = True
            invoice_field.buttons_row_visible = False
            invoice_field.stop_attention_pulse()
            invoice_field.style = "filled"
            invoice_field.theme_line_color = "Primary"
            invoice_field.show()

        contract_field = self.ids.contract_field
        contract_address = update.get('contract_address', '')
        if contract_address:
            contract_field.contract_address = contract_address
            contract_field.ids.contract_address_label.texture_update()
            contract_field.title_text = "Kaspa Contract"
            if direction in ("kas2sat", "kas2btc"):
                contract_field.description_text = "You fund this contract"
                contract_field.status_text = "Ready to fund"
            elif direction == "btc2kas":
                contract_field.description_text = "Maker funds this contract"
                contract_field.status_text = "Waiting for maker..."
            else:
                # sat2kas
                contract_field.description_text = "Maker funds this contract"
                contract_field.status_text = "Waiting for maker..."
            contract_field.show()

        btc_contract_field = self.ids.btc_contract_field
        if is_onchain:
            btc_addr = update.get('btc_contract_address', '')
            btc_contract_field.contract_address = btc_addr
            btc_contract_field.title_text = "BTC Contract"
            if direction == "btc2kas":
                btc_contract_field.description_text = "You fund this contract"
                btc_contract_field.status_text = "Ready to fund"
            else:
                # kas2btc
                btc_contract_field.description_text = "Maker funds this contract"
                btc_contract_field.status_text = "Waiting for maker..."
            btc_contract_field.show()
            # TESTING: do not delete this block in later commits. Reorder was
            # commented so Kaspa stays first / BTC second for all swaps; pulse
            # already marks which card needs action. Re-enable if we want
            # taker's contract on top again (btc2kas → BTC above Kaspa).
            # if direction == "btc2kas":
            #     parent = contract_field.parent
            #     parent.remove_widget(btc_contract_field)
            #     parent.add_widget(btc_contract_field, index=parent.children.index(contract_field) + 1)
        else:
            btc_contract_field.hide()

        payment_field = self.ids.payment_field
        payment_field.style = "outlined"
        payment_field.theme_line_color = "Custom"
        payment_field.line_color = self.app.theme_cls.primaryColor

        if direction in ("kas2sat", "kas2btc"):
            payment_field.configure_for_direction(
                direction,
                self.app.service_manager.kas_wallet_is_external(),
            )
            payment_field.title_text = "Fund Kaspa Contract"
            payment_field.info_text = "Pay KAS to start swap"
            payment_field.payment_enabled = True
            payment_field.show()
        elif direction == "btc2kas":
            payment_field.configure_for_direction(
                direction,
                self.app.service_manager.btc_wallet_is_external(),
            )
            payment_field.title_text = "Fund BTC Contract"
            sat_amount = update.get('sat_amount') or self.controller.sat_amount
            payment_field.info_text = f"Send {sat_amount} sats"
            payment_field.payment_enabled = True
            payment_field.show()
        else:
            payment_field.configure_for_direction(
                direction,
                self.app.service_manager.ln_wallet_is_external(),
            )
            payment_field.title_text = "Pay Invoice"
            payment_field.info_text = "Wait for confirmation..."
            payment_field.payment_enabled = False

        status_widget = self.ids.status_widget
        status_widget.status_text = "Swap initialized"
        status_widget.show()

    def prepare_invoice_request(self, sat_amount):
        """Show the invoice field; generate/paste resolve the interaction future."""
        invoice_field = self.ids.invoice_field
        invoice_field.show()
        if invoice_field.invoice:
            invoice_field.stop_attention_pulse()
            invoice_field.style = "filled"
            invoice_field.theme_line_color = "Primary"
        else:
            invoice_field.start_attention_pulse()
            if invoice_field.info_label_text != "Generating...":
                invoice_field.info_label_text = f"Waiting for invoice ({sat_amount} sats)..."

    def prepare_funding_request(self, kind, address, amount):
        """Enable the pay button; a tap answers confirm_funding."""
        payment_field = self.ids.payment_field
        payment_field.payment_enabled = True
        if kind == 'btc':
            payment_field.info_text = f"Send {amount} sats"
        else:
            payment_field.info_text = f"Pay {amount} KAS to start swap"
        payment_field.show()
        payment_field.start_attention_pulse()

    def prepare_preimage_request(self, invoice, timeout=None):
        """Show the external-pay dialog that collects a preimage."""
        self.ids.status_widget.status_text = "Waiting for external BTC payment..."
        self.ids.payment_field.payment_enabled = True
        self.ids.payment_field.show()
        self.show_invoice_qr_popup(invoice, input_preimage=True)

    def generate_invoice(self, amount=None):
        """Generate invoice with internal LN wallet."""
        invoice_field = self.ids.invoice_field
        invoice_field.is_generating = True
        invoice_field.info_label_text = "Generating..."
        asyncio.create_task(self._generate_invoice(amount))

    async def _generate_invoice(self, amount=None):
        """Generate invoice with error handling."""
        invoice_field = self.ids.invoice_field
        try:
            if amount is None:
                quote = self.ids.swap_editor.last_quote
                if quote and quote.get('sat_amount'):
                    amount = int(quote['sat_amount'])
                elif self.controller.sat_amount:
                    amount = int(self.controller.sat_amount)
                else:
                    amount = round(self.ids.swap_editor.btc_amount * 1e8)
                logger.debug(f"generating invoice for {amount} sats")
            invoice = await self.app.taker.create_ln_invoice(amount)
            invoice_field.invoice = invoice
            invoice_field.is_invoice_validated = True
            invoice_field.info_label_text = f"Invoice generated: {amount} sats"
            invoice_field.is_generating = False
            if invoice_field.swap_direction == "kas2sat":
                invoice_field.buttons_row_visible = False
            if self.controller.interaction and self.controller.interaction.is_waiting('invoice'):
                self.controller.interaction.answer('invoice', invoice)
                invoice_field.stop_attention_pulse()
                invoice_field.style = "filled"
                invoice_field.theme_line_color = "Primary"
        except Exception as e:
            logger.error(f"Failed to generate invoice: {e}")
            invoice_field.info_label_text = f"Error generating invoice: {str(e)}"
            invoice_field.is_generating = False
            import traceback
            traceback.print_exc()

    def pay_with_kaspa_wallet(self):
        """Confirm funding; the orchestrator sends the KAS."""
        self.ids.payment_field.payment_enabled = False
        self.ids.status_widget.status_text = "KAS payment sending..."
        self.payment_started = True
        self.show_cancel_button = False
        if self.controller.interaction:
            self.controller.interaction.answer('funding', True)

    def pay_with_external_kaspa_wallet(self):
        """Show the contract QR. Close does not commit the swap."""
        self.show_contract_qr_popup(
            self.ids.contract_field.contract_address, pay_info=True, chain='kas',
        )

    def pay_with_btc_wallet(self):
        """Confirm BTC funding; the orchestrator sends the sats."""
        self.ids.payment_field.payment_enabled = False
        self.ids.payment_field.info_text = "Sending BTC..."
        self.payment_started = True
        self.show_cancel_button = False
        if self.controller.interaction:
            self.controller.interaction.answer('funding', True)

    def pay_with_external_btc_wallet(self):
        """Show the BTC contract QR. Close does not commit the swap."""
        self.show_contract_qr_popup(
            self.ids.btc_contract_field.contract_address, pay_info=True, chain='btc',
        )

    # Commented during phase 5 of the taker/controller integration:
    # redeem/refund and LN pay+redeem are owned by Taker.run_*.
    # async def _redeem_onchain_async(self): ...
    # async def _refund_onchain_async(self): ...
    # async def _pay_with_ln_wallet_async(self): ...
    # async def _on_preimage_validated(self, preimage): ...

    def pay_with_ln_wallet(self):
        """Pay the invoice; a secret answers the taker's preimage wait."""
        self.ids.payment_field.payment_enabled = False
        self.ids.payment_field.info_text = "Paying invoice..."
        self.payment_started = True
        self.show_cancel_button = False
        asyncio.create_task(self._pay_with_ln_wallet())

    async def _pay_with_ln_wallet(self):
        try:
            secret = await self.app.taker._pay_invoice_resolved()
        except Exception as e:
            logger.error(f"LN pay failed: {e}")
            secret = None
        if secret and self.controller.interaction:
            self.controller.interaction.answer('preimage', secret)
            return
        self.ids.payment_field.payment_enabled = True
        self.ids.payment_field.info_text = "Payment failed — pay external or retry"
        self.payment_started = False
        self.show_cancel_button = True

    def pay_with_external_ln_wallet(self):
        """Open the invoice dialog. Close does not commit the swap."""
        invoice = self.ids.invoice_field.invoice or self.controller.lightning_invoice
        self.prepare_preimage_request(invoice)

    def on_preimage_input(self, instance, value):
        """Resolve the preimage future once the hash matches."""
        logger.debug(f"preimage input value: {value}")
        interaction = self.controller.interaction
        if interaction is None or not interaction.preimage_matches(value):
            return
        self.payment_started = True
        self.ids.payment_field.stop_attention_pulse()
        self.ids.payment_field.ids.payment_info_label.text_color = (0, 0.8, 0, 1)
        self.ids.payment_field.info_text = "Invoice paid!"
        self.ids.payment_field.style = "filled"
        self.ids.payment_field.theme_line_color = "Primary"
        self.ids.payment_field.buttons_row_visible = False
        if self.dialog:
            self.dialog.dismiss()
            self.dialog = None
        interaction.answer('preimage', value.strip())

    def cancel_swap(self):
        """Cancel the swap and reset UI."""
        # Terminal or leave-safe mid-settlement: reset without killing promote.
        if self.swap_final_state:
            self._reset_swap_state()
            return

        if not self.controller.can_cancel():
            self._show_uncancellable_dialog()
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
        dialog.dismiss()
        if not self.controller.cancel_swap():
            # Funded while the dialog was open.
            self._show_uncancellable_dialog()
            return
        self._reset_swap_state()

    def _show_uncancellable_dialog(self):
        """Explain why a funded swap keeps running instead of cancelling.

        The contract holds the money until it is redeemed or, once the
        locktime passes, refunded, and the swap that is running is what does
        either. Stopping it would leave the funds behind.
        """
        dialog = MDDialog(
            MDDialogHeadlineText(text="Swap already funded"),
            MDDialogContentContainer(
                MDLabel(
                    text=(
                        "This swap cannot be cancelled: the funds are in the "
                        "contract. It will complete, or refund automatically "
                        "once the locktime expires."
                    ),
                    theme_text_color="Secondary",
                ),
                MDWidget(size_hint_y=None, height=dp(20)),
                MDBoxLayout(
                    MDButton(
                        MDButtonText(text="OK"),
                        style="filled",
                        on_release=lambda x: dialog.dismiss(),
                    ),
                    adaptive_height=True,
                ),
                orientation="vertical",
                spacing=dp(12),
                padding=dp(16),
            ),
            auto_dismiss=True,
        )
        dialog.open()
    
    def _reset_swap_state(self):
        """Reset all swap state and UI.

        During settling the orchestrator has already returned; promote lives on
        the taker. Detach the UI without cancelling an in-flight run when the
        task is already done.
        """
        if self.controller:
            if self.swap_final_state == "settling":
                # Orchestrator finished at broadcast; do not CancelledError it.
                self.controller.is_active = False
                self.controller.task = None
                self.controller.monitor_task = None
            else:
                self.controller.reset()
        
        # Reset all component properties to defaults
        self.ids.swap_editor.reset()
        self.ids.invoice_field.reset()
        self.ids.contract_field.reset()
        self.ids.btc_contract_field.reset()
        self.ids.payment_field.reset()
        self.ids.status_widget.reset()
        
        # Reset UI component visibility/state
        self.ids.swap_editor.expand()
        self.ids.peer_selector.expand()
        self.ids.payout_bar.is_locked = False
        self.ids.invoice_field.hide()
        self.ids.contract_field.hide()
        self.ids.btc_contract_field.hide()
        # TESTING: do not delete this block in later commits. Pair of the
        # btc2kas add/remove above; unused while cards stay in KV order.
        # parent = self.ids.contract_field.parent
        # parent.remove_widget(self.ids.contract_field)
        # parent.add_widget(
        #     self.ids.contract_field,
        #     index=parent.children.index(self.ids.btc_contract_field) + 1,
        # )
        self.ids.payment_field.hide()
        self.ids.status_widget.hide()
        
        # Reset state properties
        self.payment_started = False
        self.start_in_flight = False
        self.swap_final_state = ""
        self.show_cancel_button = False
        self.cancel_button_text = "Cancel Swap"
        self.contract_address_qr_image = None
        self.contract_address_qr_texture = None
        self.contract_address_bip21_qr_texture = None
        self.output_address = ""
    
    # Commented during phase 5 of the taker/controller integration:
    # refund countdown and spend lived here; Taker._kas_refundable /
    # _refund_kas own them now and emit events the screen renders.
    # async def _handle_refund_countdown(self): ...
    # def refund_contract(self, refund_address): ...
    # async def _refund_contract_async(self, refund_address): ...

    async def ask_output_address(self, refund=False, *, is_btc=False):
        """Deprecated. Payout is set on PayoutAddressBar before start.

        Mid-swap dialogs were retired. Start is disabled until the bar is
        ready, then kas/btc addresses are written onto the taker. This
        method remains so resolve_payout_address can still read the bar
        if a swap object has no payout field. Same-chain only.
        """
        bar = self.ids.payout_bar
        if is_btc:
            return bar.btc_address or ''
        return bar.kas_address or ''

    # Deprecated mid-swap output-address dialog. PayoutAddressBar owns this now.
    # async def ask_output_address(self, refund=False):
    #     """Dialog for an output/refund address; returns it (does not write to taker)."""
    #     # step 1: if internal go wallet -> we generate a new address
    #     # step 2: we open a dialog, with text field, auto-fill if address was generated in step 1
    #     # Why not fall back to satkas keys? Getting funds out of them is hard,
    #     # and the db may be wiped during updates.
    #
    #     self.output_address = ""
    #     if self.app.service_manager._preferred_wallet == 'go':
    #         address = await self.app.service_manager.kaspa_wallet_service.get_new_address()
    #         print(f"New address: {address}")
    #     else:
    #         address = ""
    #
    #     addr_type = "refund" if refund else "output"
    #
    #     self.output_address_button = MDButton(
    #         MDButtonText(text="OK"),
    #         style="text",
    #         disabled=True,
    #         on_release=self.confirm_output_address
    #     )
    #
    #     # definition -> binding -> text assignment
    #     # IN THIS ORDER! This way the callback is called even for auto-filled addresses.
    #     self.output_address_text_field = MDTextField(
    #         MDTextFieldHintText(text=f"Enter {addr_type} address"),
    #         mode="outlined",
    #         multiline=False,
    #         max_height=dp(48)
    #     )
    #     self.output_address_text_field.bind(text=self.on_output_address_input)
    #     self.output_address_text_field.text = address
    #
    #     self.dialog = MDDialog(
    #         MDDialogHeadlineText(text=f"Set {addr_type} address"),
    #         MDDialogContentContainer(
    #             self.output_address_text_field
    #         ),
    #         MDDialogButtonContainer(
    #             MDWidget(),
    #             self.output_address_button
    #         ),
    #         auto_dismiss=False,
    #     )
    #     self.dialog.open()
    #
    #     while not self.output_address:
    #         await asyncio.sleep(0.5)
    #     return self.output_address
    #
    # # Kept for StatusWidget / any leftover call sites; forwards to ask_output_address.
    # async def handle_output_address(self, refund=False):
    #     return await self.ask_output_address(refund=refund)
    #
    # def on_output_address_input(self, instance, value):
    #     """Handle refund address input."""
    #     print(f"Refund address input: {value}")
    #     try:
    #         decode_address(value)
    #         self.output_address_button.disabled = False
    #     except Exception as e:
    #         print(f"Invalid address: {e}")
    #         self.output_address_button.disabled = True
    #         return
    #     self.output_address_button.disabled = False
    #
    # def confirm_output_address(self, *args):
    #     """Confirm output address."""
    #     self.output_address = self.output_address_text_field.text
    #     # Commented during phase 5 of the taker/controller integration:
    #     # the address travels back as the return value of ask_output_address.
    #     # self.app.taker.output_address = self.output_address
    #     self.dialog.dismiss()
    #     self.dialog = None

    def show_invoice_qr_popup(self, invoice, input_preimage=False):
        """Show invoice QR code dialog."""
        self._is_funding_qr_dialog = False
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
    
    def show_contract_qr_popup(self, address, pay_info=False, chain='kas'):
        """Show contract address QR. pay_info adds amount + a BIP-21 checkbox.

        Kaspa addresses already carry the network prefix (`kaspa:` /
        `kaspatest:` / …) from encode_address — not a URI scheme, so no extra
        wrap. Amount is `address?amount=<kas>`.

        BTC uses a BIP-21 URI (`bitcoin:<addr>?amount=<btc>`). The scheme is
        required for wallets to pick up the amount. Unchecked QR is
        `bitcoin:<addr>` without amount; if a wallet later fails that form,
        fall back to the bare address then.
        """
        if chain == 'btc':
            address = f"bitcoin:{address}"
            sat_amount = int(
                self.controller.sat_amount
                or round((self.ids.swap_editor.btc_amount or 0) * 1e8)
                or 0
            )
            bip21_amount = f"{sat_amount / 1e8:.8f}"
            amount_label = f"Send {bip21_amount} BTC to this address"
        else:
            kas_amount = self.controller.kas_amount or self.ids.swap_editor.kas_amount
            kas_display = self.ids.swap_editor.format_kas(kas_amount) if kas_amount else str(kas_amount or 0)
            amount_label = f"Send {kas_display} KAS to this address"
            bip21_amount = kas_amount

        self.contract_address_qr_texture = make_qr(address)
        self.contract_address_bip21_qr_texture = make_qr(f"{address}?amount={bip21_amount}")

        self.contract_address_qr_image = Image(
            texture=self.contract_address_bip21_qr_texture if pay_info else self.contract_address_qr_texture,
            size_hint=(None, None),
            size=(dp(250), dp(250)),
            pos_hint={"center_x": 0.5},
        )

        if pay_info:
            self.bip_21_box = MDBoxLayout(
                    MDLabel(
                        text=amount_label,
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
        self._is_funding_qr_dialog = True
        self.dialog.bind(on_dismiss=self._on_funding_dialog_dismissed)
        self.dialog.open()

    def update_bip21_qr(self, instance, value):
        logger.debug(f"Updating BIP-21 QR ({instance}): {value}")
        self.contract_address_qr_image.texture = self.contract_address_bip21_qr_texture if value else self.contract_address_qr_texture

    def copy_to_clipboard(self, text):
        """Copy text to clipboard."""
        Clipboard.copy(text.strip())
        logger.debug(f"Copied: {text}")

    def _dismiss_funding_dialog(self):
        """Close the optional funding QR if it is still open.

        Monitoring does not depend on the dialog; this only clears it when
        on-chain funding (or partial funding) is detected.
        """
        if not self._is_funding_qr_dialog or self.dialog is None:
            return
        try:
            self.dialog.dismiss()
        except Exception:
            self._is_funding_qr_dialog = False
            self.dialog = None

    def _on_funding_dialog_dismissed(self, *args):
        """Clear funding-QR bookkeeping whether closed by user or by funding."""
        self._is_funding_qr_dialog = False
        self.dialog = None

    def dismiss_dialog(self, *args):
        """Dismiss dialog."""
        if self.dialog:
            self.dialog.dismiss()
            self.dialog = None
        self._is_funding_qr_dialog = False
