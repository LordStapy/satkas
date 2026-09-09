"""How a swap asks its frontend for something only a human can supply.

Kept out of taker.py for the reason swap_errors.py gives in its docstring: a
frontend implements this contract without importing the swapper classes, and
importing taker.py to get a base class would pull aiohttp, inputimeout and
aiohttp_socks into a Kivy screen.

The base class defaults are the non-interactive behaviour, so it doubles as
its own null object: a swap run with internal wallets never prompts and never
reaches a method that raises.

Every argument is a primitive - an int, a bolt11 string, an address, a number
of seconds. An interaction never receives the swap, so an adapter cannot reach
into core state.
"""

import logging

from satkas.core.swapper.swap_errors import InteractionRequired

logger = logging.getLogger('swap_interaction')


class SwapInteraction:
    """The questions a swap can ask, and the answers a headless run gives."""

    async def request_ln_invoice(self, sat_amount):
        """A bolt11 invoice for sat_amount, which we will ask the maker to pay."""
        raise InteractionRequired(
            f"an LN invoice for {sat_amount} sats is needed and no frontend can supply one"
        )

    async def request_preimage(self, invoice, payment_hash=None, timeout=None):
        """The preimage of a paid invoice, as a hex string.

        payment_hash is optional so an adapter can gate keystrokes without
        touching the swap object. The core still validates what comes back.
        """
        raise InteractionRequired(
            'a payment preimage is needed and no frontend can supply one'
        )

    async def request_output_address(self, refund=False, *, is_btc=False):
        """Where the proceeds should land.

        Called only when the swap has no payout address yet for this chain.
        `is_btc` selects Bitcoin vs Kaspa; never return the other chain.

        UI adapters: payout is set before start; this is a last-resort
        fallback. CLI/scripted adapters must return a concrete address.
        The base raises so a headless run without one cannot silently continue.
        """
        kind = 'refund' if refund else 'output'
        chain = 'BTC' if is_btc else 'Kaspa'
        raise InteractionRequired(
            f"a {chain} {kind} address is needed and no frontend can supply one"
        )

    async def confirm_funding(self, kind, address, amount):
        """Whether to send `amount` to `address`. `kind` is 'kas' or 'btc'.

        True by default, matching today's CLI, which funds the moment it can.
        """
        return True


class CliInteraction(SwapInteraction):
    """Prompts on stdin, reproducing what the legacy taker methods print.

    input() and inputimeout() both block the event loop, exactly as the legacy
    methods do. That is acceptable for a CLI run with nothing else scheduled;
    asyncio.to_thread is the fix if that stops being true.
    """

    async def request_ln_invoice(self, sat_amount):
        return input(f"Generate a LN invoice for {sat_amount} sats and paste it here: ").strip()

    async def request_preimage(self, invoice, payment_hash=None, timeout=None):
        from inputimeout import inputimeout, TimeoutOccurred

        logger.info(f"Pay the invoice then paste the preimage of the payment\n\n{invoice}\n")
        if timeout is None:
            return input('Insert the preimage: ').strip()
        try:
            return inputimeout('Insert the preimage: ', timeout=timeout).strip()
        except TimeoutOccurred:
            raise InteractionRequired('Timeout: the invoice is expired, aborting swap')

    async def request_output_address(self, refund=False, *, is_btc=False):
        kind = 'refund' if refund else 'output'
        chain = 'BTC' if is_btc else 'Kaspa'
        return input(f"Enter {chain} {kind} address: ").strip()


class ScriptedInteraction(SwapInteraction):
    """Canned answers, so an example can run a full swap unattended."""

    def __init__(self, ln_invoice=None, preimage=None, output_address=None,
                 btc_output_address=None, fund=True):
        self.ln_invoice = ln_invoice
        self.preimage = preimage
        self.output_address = output_address
        self.btc_output_address = btc_output_address
        self.fund = fund

    async def request_ln_invoice(self, sat_amount):
        if self.ln_invoice is None:
            return await super().request_ln_invoice(sat_amount)
        return self.ln_invoice

    async def request_preimage(self, invoice, payment_hash=None, timeout=None):
        if self.preimage is None:
            return await super().request_preimage(invoice, payment_hash, timeout)
        return self.preimage

    async def request_output_address(self, refund=False, *, is_btc=False):
        # Same-chain only: a Kaspa address must not satisfy a BTC ask.
        return self.btc_output_address if is_btc else self.output_address

    async def confirm_funding(self, kind, address, amount):
        return self.fund
