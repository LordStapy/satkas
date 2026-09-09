"""
Swap Controller - UI adapter over Taker.run_*.

Commented-out methods below were the Phase 1–4 parallel implementation; they
were commented during phase 5 of the taker/controller integration.
"""

import asyncio
import logging
from typing import Optional

from satkas.ui.screens.quick_swap_v2.kivy_interaction import KivyInteraction

logger = logging.getLogger('swap_controller')


class SwapController:
    """Starts a swap orchestrator and forwards its events to the screen."""

    OFFCHAIN_DIRECTIONS = ('kas2sat', 'sat2kas')
    ONCHAIN_DIRECTIONS = ('kas2btc', 'btc2kas')

    def __init__(self, taker, service_manager, screen=None):
        self.taker = taker
        self.service_manager = service_manager
        self.screen = screen
        self.interaction = KivyInteraction(screen) if screen is not None else None
        self.task: Optional[asyncio.Task] = None
        # Alias kept so older call sites and mental model still find it.
        self.monitor_task = None

        # Display state the screen still reads between events.
        self.swap_mode = 'offchain'  # 'offchain' | 'onchain'
        self.swap_direction = 'kas2sat'
        self.kas_amount = 0
        self.sat_amount = 0
        self.rate = 0
        self.contract_address = ''
        self.btc_contract_address = ''
        self.contract_funded = False
        self.btc_contract_funded = False
        self.lightning_invoice = ''
        self.is_active = False
        self.last_event = None

    def bind_screen(self, screen):
        """Attach the screen after construction, once KV has wired ids."""
        self.screen = screen
        self.interaction = KivyInteraction(screen)

    @property
    def is_onchain(self) -> bool:
        return self.swap_mode == 'onchain' or self.swap_direction in self.ONCHAIN_DIRECTIONS

    @staticmethod
    def user_sends_kas(direction: str) -> bool:
        return direction in ('kas2sat', 'kas2btc')

    @staticmethod
    def user_sends_btc(direction: str) -> bool:
        return direction in ('sat2kas', 'btc2kas')

    def start_swap(self, direction, kas_amount, sat_amount, rate, on_update):
        """Kick off Taker.run_* and stream its events to on_update."""
        if self.interaction is None:
            raise RuntimeError('SwapController has no screen-bound interaction')
        self.swap_direction = direction
        self.swap_mode = 'onchain' if direction in self.ONCHAIN_DIRECTIONS else 'offchain'
        self.kas_amount = kas_amount
        self.sat_amount = sat_amount
        self.rate = rate
        self.contract_funded = False
        self.btc_contract_funded = False
        self.contract_address = ''
        self.btc_contract_address = ''
        self.lightning_invoice = ''
        self.last_event = None
        self.is_active = True

        run = getattr(self.taker, f"run_{direction}")
        self.task = asyncio.create_task(
            run(
                kas_amount=kas_amount,
                sat_amount=sat_amount,
                base_rate=rate,
                on_event=self._forward(on_update),
                interaction=self.interaction,
            )
        )
        self.monitor_task = self.task
        self.task.add_done_callback(self._monitor_done)

    def resume_swap(self, on_update):
        """Re-enter the latest live taker swap after a restart."""
        if self.interaction is None:
            raise RuntimeError('SwapController has no screen-bound interaction')
        self.is_active = True
        self.task = asyncio.create_task(
            self.taker.run_resume(
                on_event=self._forward(on_update),
                interaction=self.interaction,
            )
        )
        self.monitor_task = self.task
        self.task.add_done_callback(self._monitor_done)

    def can_cancel(self):
        """Whether cancelling is still an option, for a screen about to offer it."""
        return self.taker is None or self.taker.can_cancel()

    def cancel(self):
        """Stop the orchestrator, unless the swap is holding money.

        A funded swap has no cancellation left in it: the contract is either
        redeemed or refunded after its locktime, and the running flow is what
        does that. Killing it there would strand the funds, so the taker gets
        asked first and a refusal is reported back rather than acted on.

        Returns whether the swap was stopped. EXPIRED and swap cleanup belong
        to Taker._guarded.
        """
        if not self.can_cancel():
            logger.warning('Refusing to cancel a funded swap; it keeps running')
            return False
        if self.interaction is not None:
            self.interaction.abandon()
        if self.task and not self.task.done():
            self.task.cancel()
        self.task = None
        self.monitor_task = None
        self.is_active = False
        return True

    # Name kept for the screen's cancel path.
    cancel_swap = cancel

    @staticmethod
    def _monitor_done(task):
        """Log an orchestrator that died on its own.

        Nothing awaits the task, so without this an exception is stored on
        the task and never seen: the screen simply stops receiving events.
        """
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(f"Swap orchestrator stopped: {error}", exc_info=error)

    def _forward(self, callback):
        """Adapt swap events to the dict payload the screen consumes."""
        def forward(event):
            self.last_event = event
            if event.contract_address:
                self.contract_address = event.contract_address
            if event.btc_contract_address:
                self.btc_contract_address = event.btc_contract_address
            if event.invoice:
                self.lightning_invoice = event.invoice
            if event.kas_amount is not None:
                self.kas_amount = event.kas_amount
            if event.sat_amount is not None:
                self.sat_amount = event.sat_amount
            if self.kas_amount and event.funded is not None and event.funded >= self.kas_amount:
                self.contract_funded = True
            if event.btc_funded:
                self.btc_contract_funded = True
            return callback(event.as_dict())
        return forward

    def reset(self):
        """Reset controller state."""
        self.cancel()
        self.kas_amount = 0
        self.sat_amount = 0
        self.rate = 0
        self.contract_address = ''
        self.btc_contract_address = ''
        self.lightning_invoice = ''
        self.swap_direction = 'kas2sat'
        self.swap_mode = 'offchain'
        self.contract_funded = False
        self.btc_contract_funded = False
        self.last_event = None
        self.is_active = False


# ---------------------------------------------------------------------------
# Commented during phase 5 of the taker/controller integration.
# Full pre-Phase-5 methods follow so behaviour can be compared. The
# orchestrators on Taker now own init, monitor, pay, redeem and refund.
# ---------------------------------------------------------------------------
#
#     async def init_swap(self, direction: str, kas_amount: float, rate: float, **kwargs):
#         self.swap_direction = direction
#         self.swap_mode = 'onchain' if direction in self.ONCHAIN_DIRECTIONS else 'offchain'
#         self.kas_amount = kas_amount
#         self.rate = rate
#         self.sat_amount = int(kas_amount * rate)
#         self._redeemed = False
#         self.contract_funded = False
#         self.btc_contract_funded = False
#         self.secret = None
#         self.btc_contract_address = ''
#         if direction == 'kas2sat':
#             return await self._init_kas2sat(**kwargs)
#         if direction == 'sat2kas':
#             return await self._init_sat2kas()
#         if direction == 'btc2kas':
#             return await self._init_btc2kas()
#         if direction == 'kas2btc':
#             return await self._init_kas2btc()
#         raise ValueError(f'Unknown swap direction: {direction}')
#
#     async def _init_kas2sat(self, invoice=None):
#         sat_amount = self.sat_amount
#         if invoice is None:
#             invoice = await self.create_invoice(sat_amount)
#         response = await self.taker.init_swap(
#             sender_address=self.taker.address,
#             ln_invoice=invoice,
#             price=self.rate,
#             kas_amount=self.kas_amount,
#         )
#         self.lightning_invoice = invoice
#         self.contract_address = response['p2sh_address']
#         self.is_active = True
#         return {
#             'invoice': invoice,
#             'contract_address': response['p2sh_address'],
#             'kas_amount': response.get('kas_amount', self.kas_amount),
#         }
#
#     async def _init_sat2kas(self):
#         response = await self.taker.init_swap(
#             receiver_address=self.taker.address,
#             price=self.rate,
#             kas_amount=self.kas_amount,
#         )
#         self.lightning_invoice = response['ln_invoice']
#         self.contract_address = response['p2sh_address']
#         self.is_active = True
#         return {
#             'invoice': response['ln_invoice'],
#             'contract_address': response['p2sh_address'],
#             'kas_amount': response.get('kas_amount', self.kas_amount),
#         }
#
#     async def _init_btc2kas(self):
#         self.secret = os.urandom(32)
#         secret_hash = hashlib.sha256(self.secret).hexdigest()
#         btc_height = await self.service_manager.bitcoin_service.get_block_height(force_refresh=True)
#         if not btc_height:
#             raise RuntimeError('Could not get Bitcoin block height')
#         btc_locktime = btc_height + 36
#         btc_sender = self.taker.btc_address.to_string()
#         response = await self.taker.init_swap(
#             swap_type='btc2kas',
#             receiver_address=self.taker.address,
#             btc_sender_address=btc_sender,
#             btc_locktime=btc_locktime,
#             kas_amount=self.kas_amount,
#             price=self.rate,
#             secret_hash=secret_hash,
#         )
#         if not response:
#             raise RuntimeError('Maker rejected btc2kas init')
#         self.sat_amount = int(response.get('sat_amount', self.sat_amount))
#         self.contract_address = response['p2sh_address']
#         self.btc_contract_address = response['btc_p2sh_address']
#         self.is_active = True
#         return {
#             'contract_address': self.contract_address,
#             'btc_contract_address': self.btc_contract_address,
#             'kas_amount': response.get('kas_amount', self.kas_amount),
#             'sat_amount': self.sat_amount,
#             'btc_locktime': btc_locktime,
#             'kas_locktime': response.get('kas_locktime'),
#         }
#
#     async def _init_kas2btc(self):
#         self.secret = os.urandom(32)
#         secret_hash = hashlib.sha256(self.secret).hexdigest()
#         daa_score = await self.service_manager.kaspad_service.get_daa_score(force_refresh=True)
#         if daa_score <= 0:
#             raise RuntimeError('Could not get Kaspa DAA score')
#         kas_locktime = daa_score + (10 * 60 * 60 * 6)
#         btc_receiver = self.taker.btc_address.to_string()
#         response = await self.taker.init_swap(
#             swap_type='kas2btc',
#             sender_address=self.taker.address,
#             btc_receiver_address=btc_receiver,
#             kas_locktime=kas_locktime,
#             kas_amount=self.kas_amount,
#             price=self.rate,
#             secret_hash=secret_hash,
#         )
#         if not response:
#             raise RuntimeError('Maker rejected kas2btc init')
#         self.sat_amount = int(response.get('sat_amount', self.sat_amount))
#         self.kas_amount = float(response.get('kas_amount', self.kas_amount))
#         self.contract_address = response['p2sh_address']
#         self.btc_contract_address = response['btc_p2sh_address']
#         self.is_active = True
#         return {
#             'contract_address': self.contract_address,
#             'btc_contract_address': self.btc_contract_address,
#             'kas_amount': self.kas_amount,
#             'sat_amount': self.sat_amount,
#             'kas_locktime': kas_locktime,
#             'btc_locktime': response.get('btc_locktime'),
#         }
#
#     async def monitor_swap(self, on_update_callback):
#         monitors = {
#             'kas2sat': self._monitor_kas2sat,
#             'sat2kas': self._monitor_sat2kas,
#             'btc2kas': self._monitor_btc2kas,
#             'kas2btc': self._monitor_kas2btc,
#         }
#         monitor_fn = monitors.get(self.swap_direction)
#         if not monitor_fn:
#             raise ValueError(f'No monitor for direction {self.swap_direction}')
#         self.monitor_task = asyncio.create_task(monitor_fn(on_update_callback))
#         self.monitor_task.add_done_callback(self._monitor_done)
#
#     async def _monitor_kas2sat(self, callback):
#         await self.taker.watch_kas2sat(
#             kas_amount=self.kas_amount,
#             on_event=self._forward(callback),
#         )
#
#     async def _monitor_sat2kas(self, callback):
#         await self.taker.watch_sat2kas(
#             kas_amount=self.kas_amount,
#             on_event=self._forward(callback),
#         )
#
#     async def _monitor_btc2kas(self, callback):
#         await self.taker.watch_btc2kas(
#             kas_amount=self.kas_amount,
#             sat_amount=self.sat_amount,
#             on_event=self._forward(callback),
#             is_settled=lambda: self._redeemed,
#         )
#
#     async def _monitor_kas2btc(self, callback):
#         await self.taker.watch_kas2btc(
#             kas_amount=self.kas_amount,
#             sat_amount=self.sat_amount,
#             on_event=self._forward(callback),
#             is_settled=lambda: self._redeemed,
#         )
#
#     async def create_invoice(self, amount: int) -> str:
#         return await self.taker.create_ln_invoice(amount)
#
#     async def pay_with_internal_ln_wallet(self):
#         return await self.taker.pay_ln_invoice(self.lightning_invoice)
#
#     async def redeem_with_preimage(self, preimage_hex: str) -> str:
#         preimage_bytes = bytes.fromhex(preimage_hex)
#         if not self._validate_preimage(preimage_hex):
#             raise ValueError('Invalid preimage')
#         self.taker.swap.receiver_private_key = self.taker.get_secret_key()
#         if not self.taker.swap.output_address:
#             self.taker.swap.output_address = self.taker.output_address
#         return self.taker.swap.spend_contract(secret=preimage_bytes)
#
#     def _validate_preimage(self, preimage_hex: str) -> bool:
#         return self.taker.swap.validate_preimage(preimage_hex)
#
#     async def pay_with_kaspa_wallet(self):
#         await self.taker.fund_kas_contract(self.taker.swap, self.kas_amount)
#
#     async def pay_with_btc_wallet(self) -> str:
#         return await self.taker.fund_btc_contract(self.taker.swap, self.sat_amount)
#
#     async def redeem_onchain(self) -> str:
#         if not self.secret:
#             raise RuntimeError('No secret available for redeem')
#         swap = self.taker.swap
#         if self.swap_direction == 'btc2kas':
#             swap.receiver_private_key = self.taker.get_secret_key()
#             if not swap.output_address:
#                 swap.output_address = self.taker.output_address
#             txid = swap.spend_contract(secret=self.secret)
#         elif self.swap_direction == 'kas2btc':
#             swap.btc_receiver_private_key = self.taker.get_secret_key(is_btc=True)
#             if not swap.btc_output_address:
#                 swap.btc_output_address = self.taker.btc_address.to_string()
#             txid = await swap.spend_btc_contract(secret=self.secret)
#         else:
#             raise ValueError(f'redeem_onchain not valid for {self.swap_direction}')
#         self._redeemed = True
#         self.taker.db_set_swap_status('COMPLETED')
#         return txid
#
#     async def refund_onchain(self) -> str:
#         swap = self.taker.swap
#         if self.swap_direction == 'btc2kas':
#             swap.btc_sender_private_key = self.taker.get_secret_key(is_btc=True)
#             if not swap.btc_output_address:
#                 swap.btc_output_address = self.taker.btc_address.to_string()
#             txid = await swap.spend_btc_contract()
#         elif self.swap_direction == 'kas2btc':
#             swap.sender_private_key = self.taker.get_secret_key()
#             if not swap.output_address:
#                 swap.output_address = self.taker.output_address
#             txid = swap.spend_contract()
#         else:
#             raise ValueError(f'refund_onchain not valid for {self.swap_direction}')
#         self.taker.db_set_swap_status('REFUNDED')
#         return txid
#
#     def cancel_swap_legacy(self):
#         if self.monitor_task and not self.monitor_task.done():
#             self.monitor_task.cancel()
#             self.monitor_task = None
#         self.is_active = False
#         if self.taker.swap:
#             self.taker.db_set_swap_status('EXPIRED')
#         self.taker.swap = None
