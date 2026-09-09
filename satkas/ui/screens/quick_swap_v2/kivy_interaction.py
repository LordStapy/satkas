"""Kivy adapter for SwapInteraction.

Turns a swap's questions into widget state, and a tap into an answer. Lives
next to the controller so the screen can answer without importing taker.py.
"""

import asyncio
import hashlib
import logging
import time

from satkas.core.swapper.swap_errors import InteractionRequired
from satkas.core.swapper.swap_interaction import SwapInteraction

logger = logging.getLogger('kivy_interaction')


class KivyInteraction(SwapInteraction):
    """Park each question on a future; widget handlers call answer()."""

    def __init__(self, screen):
        self.screen = screen
        self._pending = {}
        self._payment_hash = None

    async def _ask(self, key):
        future = asyncio.get_running_loop().create_future()
        self._pending[key] = future
        try:
            return await future
        finally:
            self._pending.pop(key, None)

    def answer(self, key, value):
        """Called from a widget handler. Late or duplicate taps are ignored."""
        future = self._pending.get(key)
        if future is not None and not future.done():
            future.set_result(value)

    def is_waiting(self, key):
        future = self._pending.get(key)
        return future is not None and not future.done()

    def abandon(self):
        """Cancel every unanswered question, so a cancelled swap does not
        leave a coroutine parked on a future nobody will resolve."""
        for future in list(self._pending.values()):
            if not future.done():
                future.cancel()
        self._pending.clear()

    async def request_ln_invoice(self, sat_amount):
        self.screen.prepare_invoice_request(sat_amount)
        existing = getattr(self.screen.ids.invoice_field, 'invoice', '') or ''
        if existing:
            return existing
        future = asyncio.get_running_loop().create_future()
        self._pending['invoice'] = future
        try:
            while not future.done():
                # Quote expiry while waiting for an invoice: abandon and let
                # the orchestrator surface InteractionRequired / cancel.
                valid_until = getattr(self.screen, '_kas2sat_valid_until', 0)
                if valid_until and valid_until < time.time():
                    self.abandon()
                    raise InteractionRequired('quote expired before an invoice was supplied')
                await asyncio.sleep(0.2)
            return future.result()
        finally:
            self._pending.pop('invoice', None)

    async def request_preimage(self, invoice, payment_hash=None, timeout=None):
        self._payment_hash = payment_hash
        try:
            return await self._ask('preimage')
        finally:
            self._payment_hash = None

    async def request_output_address(self, refund=False, *, is_btc=False):
        """Deprecated fallback.

        Quick Swap requires a payout on PayoutAddressBar before start, and
        copies it onto the taker so resolve_payout_address should already
        have the swap field. This is only reached if that field is missing
        (scripted/resume hole). Still chain-correct: never return Kaspa
        for a BTC ask.
        """
        logger.warning(
            'request_output_address is deprecated; payout should already '
            'be on the swap (is_btc=%s, refund=%s)',
            is_btc, refund,
        )
        return await self.screen.ask_output_address(refund=refund, is_btc=is_btc)

    async def confirm_funding(self, kind, address, amount):
        self.screen.prepare_funding_request(kind, address, amount)
        return await self._ask('funding')

    def preimage_matches(self, preimage_hex):
        """Local keystroke gate using the hash the orchestrator passed in."""
        if not self._payment_hash or len(preimage_hex) != 64:
            return False
        try:
            digest = hashlib.sha256(bytes.fromhex(preimage_hex)).hexdigest()
        except ValueError:
            return False
        return digest == self._payment_hash
