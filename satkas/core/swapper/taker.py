

import asyncio
import hashlib
import os
import ssl
import sys
import time
import json
import aiohttp
import logging
import certifi

from math import ceil, floor

from aiohttp_socks import ProxyConnector

from satkas.core.db.models import TakerWallet, Swap
from satkas.core.blib.btx_size import BTC_FUNDING_VSIZE, BTC_SPEND_VSIZE
from satkas.core.klib.ktx_mass import (
    estimate_funding_mass, estimate_spend_mass,
    P2SH_REDEEM_SIGSCRIPT_LN, P2SH_REDEEM_SIGSCRIPT_DAA,
)
from satkas.core.services.base_service import ExternalWalletRequired, PaymentStatus
from satkas.core.swapper.counterparty import Counterparty
from satkas.core.swapper.atomic_swap import AtomicSwap
from satkas.core.swapper.swap_errors import (
    ContractMismatch,
    InvoiceInvalid,
    PreimageInvalid,
    SwapError,
    SwapRejected,
)
from satkas.core.swapper.swap_events import SwapStatus
from satkas.core.swapper.swap_interaction import CliInteraction, SwapInteraction


logger = logging.getLogger('taker')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)-8s - %(name)-16s - %(message)s'
)


class Taker(Counterparty):
    SIDE = 'taker'

    # How often a watch loop comes round. Kept short so a countdown stays
    # live; how often each condition is actually probed is paced separately
    # by Counterparty.POLL_INTERVALS.
    WATCH_TICK_S = 1

    # How long _abandon may spend asking the chains whether our money is out
    # there. Short: it runs while a swap is already unwinding, and a timeout
    # is read as "committed", which is the answer that keeps the row live.
    ABANDON_PROBE_S = 20

    def __init__(self, output_address=None, btc_output_address=None, wallet_index=1,
                 wallet_passwd=None, service_manager=None, interaction=None,
                 plugins=None):
        super().__init__(
            wallet_db_table=TakerWallet,
            keep_unlocked=True,
            wallet_index=wallet_index,
            wallet_passwd=wallet_passwd,
            service_manager=service_manager,
            plugins=plugins,
        )
        # The base class defaults never prompt, so a run with internal wallets
        # behaves the same whether or not a frontend supplied one. Each run_*
        # also takes a per-call override, for a Kivy app that keeps one taker
        # and builds an adapter per screen.
        self.interaction = interaction if interaction is not None else SwapInteraction()
        self.start_time = 0
        self.maker_endpoint = ''
        # Last verified node pubkey per HTTP peer. Lives only in this process;
        # a later phonebook can seed it. Keyed by endpoint so auto-select can
        # poll several makers without one pin clobbering another.
        self._maker_pubkeys = {}
        self.output_address = output_address if output_address is not None else self.address
        self.btc_output_address = (
            btc_output_address if btc_output_address is not None
            else self.btc_address.to_string()
        )
        self.swap = None
        self.db_swap = None
        self._settlements_resumed = False
        self.on_event = None  # UI may set this before the queued resume task runs
        # Drain live rows as soon as the taker exists, but only when someone
        # is already running a loop: a sync caller (test, example, CLI import)
        # has nowhere to run this and resumes by awaiting run_resume itself.
        self._resume_task = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            self._resume_task = loop.create_task(self.run_resume())

    async def init_swap(self, **kwargs):
        """Open a swap, reporting failure as a falsy return.

        The legacy four and the controller both test this for falsiness, so
        open_swap's typed errors stop here and the reason reaches the log,
        exactly as it did before. Orchestrators call open_swap directly.
        """
        try:
            return await self.open_swap(**kwargs)
        except SwapError as e:
            logger.error(e)
            return False

    async def open_swap(self, secret=None, **kwargs):
        """Negotiate a swap with the maker and build our side of the contract.

        Raises SwapRejected, InvoiceInvalid or ContractMismatch instead of
        returning falsy, so a caller can tell a maker refusing a quote from a
        contract that does not match ours.

        `secret` is a named argument rather than part of kwargs because kwargs
        is the payload sent to the maker: only its hash may leave this machine.
        """
        if self.swap is not None:
            raise SwapRejected('A swap is already running')
        self.swap = True
        swap_type = kwargs.get('swap_type', None)
        # if swap_type is None:
        #     if kwargs.get('receiver_address'):
        #         swap_type = 'sat2kas'
        #     else:
        #         swap_type = 'kas2sat'
        kwargs['swap_type'] = swap_type
        # The row goes in before we talk to the maker, so an attempt that fails
        # still records that this key index was handed out. It stays sparse:
        # ln_invoice is unique, and a failed row holding one would make the
        # obvious retry with the same invoice raise IntegrityError.
        self.db_swap = Swap.create(swap_type=swap_type, side='taker', status='INIT')
        try:
            return await self._open_swap(swap_type, secret, kwargs)
        except SwapError:
            self.set_swap_status(self.db_swap, 'FAILED')
            self.db_swap = None
            self.swap = None
            raise

    async def _open_swap(self, swap_type, secret, kwargs):
        init_swap_payload = kwargs
        logger.debug(f"Taker init swap payload: {init_swap_payload}")
        init_swap_response = await self.ping_maker('init_swap', init_swap_payload)
        if init_swap_response['error']:
            # Commented during phase 4 of the taker/controller integration:
            # a bare return told the caller nothing about which of the five
            # failures below it hit.
            # logger.error(f"Error getting swap details from maker: {init_swap_response['error']}")
            # return
            raise SwapRejected(
                f"Error getting swap details from maker: {init_swap_response['error']}"
            )
        init_swap_response_payload = init_swap_response['payload']

        swap_kwargs, maker_info = await self._build_swap_kwargs(swap_type, init_swap_payload, init_swap_response_payload)
        if swap_kwargs is None:
            # Commented during phase 4 of the taker/controller integration.
            # return False
            raise SwapRejected(f"Unknown swap type: {swap_type}")

        maker_p2sh_address = init_swap_response_payload['p2sh_address']
        maker_short_pubkey = f"{init_swap_response['pubkey'][:3]}...{init_swap_response['pubkey'][-3:]}"
        logger.info(f"Swap {swap_type} accepted by maker {maker_short_pubkey} with address {maker_info}")

        logger.debug(f"Taker swap kwargs: {swap_kwargs}")
        swap = AtomicSwap(**swap_kwargs, service_manager=self.sm)

        if swap_type in ['sat2kas', 'kas2sat']:
            await self.decode_ln_invoice(
                swap.invoice,
                swap=swap,
                avoid_self_pay=(swap_type == 'sat2kas'),
            )
        swap.gen_contract_address()
        if swap.contract_address != maker_p2sh_address:
            raise ContractMismatch(
                f"Error, provided p2sh ({maker_p2sh_address}) differs "
                f"from the one we generated ({swap.contract_address})"
            )
        if swap_type in ['btc2kas', 'kas2btc']:
            maker_btc_p2sh_address = init_swap_response_payload['btc_p2sh_address']
            await swap.gen_btc_contract_address()
            if swap.btc_contract_address != maker_btc_p2sh_address:
                raise ContractMismatch(
                    f"Error, provided BTC p2sh ({maker_btc_p2sh_address}) differs "
                    f"from the one we generated ({swap.btc_contract_address})"
                )
        self.swap = swap
        self._db_swap_opened(
            swap_kwargs, init_swap_response['pubkey'], secret,
            dag_checkpoint_hash=await self.current_dag_checkpoint(),
        )
       
        return init_swap_response_payload

    def _db_swap_opened(self, swap_kwargs, remote_pubkey, secret=None,
                       dag_checkpoint_hash=None):
        """Fill in the row created on entry, now that the swap exists.

        The secret is ours alone until we spend with it, and it is what lets a
        restart mid-swap still redeem rather than only refund. Kept on the row
        after settlement.
        """
        row = self.db_swap
        if row is None:
            return False
        row.remote_pubkey = remote_pubkey
        row.ln_invoice = swap_kwargs.get('invoice', None)
        row.payment_hash = self.swap.secret_hash.hex()
        row.secret_hash = self.swap.secret_hash.hex()
        row.sender_address = swap_kwargs['sender_address']
        row.receiver_address = swap_kwargs['receiver_address']
        row.btc_sender_address = swap_kwargs.get('btc_sender_address', '')
        row.btc_receiver_address = swap_kwargs.get('btc_receiver_address', '')
        row.contract = self.swap.contract_script.hex()
        row.btc_contract = self.swap.btc_contract_script.to_hex() if self.swap.btc_contract_script else None
        row.p2sh_address = self.swap.contract_address
        row.btc_p2sh_address = self.swap.btc_contract_address
        row.dwork_amount = int(swap_kwargs['kas_amount'] * 1e8)
        row.sat_amount = swap_kwargs.get('sat_amount', 0)
        row.kas_locktime = self.swap.kas_locktime or self.swap.timelock or None
        row.btc_locktime = self.swap.btc_locktime
        row.dag_checkpoint_hash = dag_checkpoint_hash
        self.swap.vchain_checkpoint = dag_checkpoint_hash
        row.secret = secret.hex() if isinstance(secret, bytes) else secret
        row.output_address = self.swap.output_address or None
        row.btc_output_address = self.swap.btc_output_address or None
        self.set_swap_status(row, 'OPENED')
        return True

    async def _build_swap_kwargs(self, swap_type, request, response):
        """Build AtomicSwap kwargs and a short maker_info label for logging.

        Returns (swap_kwargs, maker_info), or (None, None) for unknown swap_type.
        """
        if swap_type == 'sat2kas':
            sender = response['sender_address']
            invoice = response['ln_invoice']
            return {
                'sender_address': sender,
                'receiver_address': self.address,
                'output_address': self.output_address,
                'invoice': invoice,
                'kas_amount': request.get('kas_amount'),
                'sat_amount': request.get('sat_amount'),
            }, f"{sender} and invoice {invoice}"

        if swap_type == 'kas2sat':
            receiver = response['receiver_address']
            return {
                'sender_address': self.address,
                'receiver_address': receiver,
                'output_address': self.output_address,
                'invoice': request['ln_invoice'],
                'kas_amount': response['kas_amount'],
                'sat_amount': request.get('sat_amount') or response.get('sat_amount'),
            }, f"{receiver}"

        if swap_type == 'btc2kas':
            sender = response['sender_address']
            btc_receiver = response['btc_receiver_address']
            await self._assert_kas_locktime(response['kas_locktime'], 'maker')
            return {
                'sender_address': sender,
                'receiver_address': self.address,
                'output_address': self.output_address,
                'kas_locktime': response['kas_locktime'],
                'btc_sender_address': self.btc_address.to_string(),
                'btc_receiver_address': btc_receiver,
                'btc_output_address': self.btc_output_address,
                'btc_locktime': request['btc_locktime'],
                'secret_hash': request['secret_hash'],
                'kas_amount': request['kas_amount'],
                'sat_amount': response['sat_amount'],
            }, f"{sender} and btc address {btc_receiver}"

        if swap_type == 'kas2btc':
            receiver = response['receiver_address']
            btc_sender = response['btc_sender_address']
            await self._assert_btc_locktime(response['btc_locktime'], 'maker')
            return {
                'sender_address': self.address,
                'receiver_address': receiver,
                'output_address': self.output_address,
                'kas_locktime': request['kas_locktime'],
                'btc_sender_address': btc_sender,
                'btc_receiver_address': self.btc_address.to_string(),
                'btc_output_address': self.btc_output_address,
                'btc_locktime': response['btc_locktime'],
                'secret_hash': request['secret_hash'],
                'kas_amount': response['kas_amount'],
                'sat_amount': response['sat_amount'],
            }, f"{receiver} and btc address {btc_sender}"

        return None, None

    # async def sat2kas(self, kas_amount=1, p2p_price=None, price=None):
    #     # sat -> kas taker routine
    #     self.start_time = time.time()
    #     # ping maker for price
    #     if price is None:
    #         price = await self.query_price('sat2kas', kas_amount=kas_amount, p2p_price=p2p_price)
    #     # ping maker with receiver address and kas amount
    #     logger.info(f"Requesting sat2kas swap with receiver address {self.address}")

    #     init_swap_response = await self.init_swap(
    #         receiver_address=self.address,
    #         kas_amount=kas_amount,
    #         price=price
    #     )
    #     if not init_swap_response:
    #         return

    #     assert self.swap.sat_amount == ceil(kas_amount * price)

    #     # await funding of P2SH address
    #     while True:
    #         utxo_sum = await self.kas_funded_amount(self.swap)
    #         if utxo_sum >= kas_amount:
    #             break
    #         if self.swap.is_expired():
    #             utxo_sum = False
    #             break
    #         await asyncio.sleep(1)
    #     if not utxo_sum:
    #         self.db_set_swap_status('EXPIRED')
    #         return

    #     while not await self.kas_confirmed(self.swap):
    #         await asyncio.sleep(1)

    #     # REDEEM PATH:
    #     # pay the invoice, retrieving the preimage
    #     try:
    #         payment_result = await self.sm.ln_wallet_service.pay_invoice(self.swap.invoice)
    #         secret = None
    #         if payment_result:
    #             secret = payment_result.get('payment_preimage') or payment_result.get('preimage')
    #         if not secret:
    #             raise Exception('LN payment failed or no preimage returned')
    #     except Exception as e:
    #         logger.error(e, exc_info=True)
    #         timeout = int((self.swap.timelock / 1000) - time.time())
    #         logger.info(f"Pay the invoice then paste the preimage of the payment\n\n{self.swap.invoice}\n")
    #         try:
    #             secret = inputimeout('Insert the preimage: ', timeout=timeout).strip()
    #         except TimeoutOccurred:
    #             logger.error(f"Timeout: the invoice is expired, aborting swap")
    #             self.db_set_swap_status('EXPIRED')
    #             return

    #     secret_bytes = bytes.fromhex(secret)
    #     if self.db_swap is not None:
    #         self.db_swap.secret = secret
    #     self.db_set_swap_status('FUNDED', finalize_swap=False)
    #     # set private key
    #     self.swap.receiver_private_key = self.get_secret_key()
    #     # redeem the P2SH utxo with the preimage
    #     swap_result = await self.swap.spend_contract(secret=secret_bytes)
    #     if swap_result:
    #         logger.info(f"Redeem transaction broadcasted, txid: {swap_result}")
    #         self.db_set_swap_status('COMPLETED')
    #     self.update_address_counter()
    #     self.swap = None
    #     logger.info(f"Swap completed in {time.time() - self.start_time:.2f} seconds")
    #     return swap_result
    #     # REFUND PATH:
    #     # invoice is not paid, maker refund after locktime expires
    #     # nothing to do here, we simply avoid paying the invoice

    # async def kas2sat(self, kas_amount=1, p2p_price=None, price=None):
    #     # kas -> sat taker routine
    #     self.start_time = time.time()
    #     # ping maker for price
    #     if price is None:
    #         price = await self.query_price('kas2sat', kas_amount=kas_amount, p2p_price=p2p_price)
    #     # ping maker with ln-invoice and sender address
    #     sat_amount = floor(kas_amount * price)
    #     if self.sm and getattr(self.sm, '_preferred_ln_wallet', 'external') != 'external':
    #         ln_invoice = await self.sm.ln_wallet_service.create_invoice(sat_amount)
    #     else:
    #         ln_invoice = input(f"Generate a LN invoice for {sat_amount} sats and paste it here: ").strip()

    #     logger.info(f"Requesting kas2sat swap with sender address {self.address} and invoice {ln_invoice}")

    #     init_swap_response = await self.init_swap(
    #         sender_address=self.address,
    #         ln_invoice=ln_invoice,
    #         price=price
    #     )
    #     if not init_swap_response:
    #         return

    #     maker_kas_amount = init_swap_response.get('kas_amount')
    #     if maker_kas_amount > kas_amount:
    #         logger.error(f"KAS amount mismatch, our: {kas_amount}, maker: {maker_kas_amount}")
    #         return False
    #     # fund the P2SH address
    #     try:
    #         await self.sm.kaspa_wallet_service.pay(self.swap.contract_address, kas_amount, sm=self.sm)
    #     except Exception as e:
    #         logger.error(e, exc_info=True)
    #         logger.info(f"Pay to {self.swap.contract_address} a minimum of {maker_kas_amount} KAS")
    #     while True:
    #         utxo_sum = await self.kas_funded_amount(self.swap)
    #         if utxo_sum >= maker_kas_amount:
    #             break
    #         if self.swap.is_expired():
    #             utxo_sum = False
    #             break
    #         await asyncio.sleep(1)
    #     if not utxo_sum:
    #         self.db_set_swap_status('EXPIRED')
    #         return False
    #     swap_ongoing = True
    #     self.db_set_swap_status('FUNDED', finalize_swap=False)

    #     swap_result = None
    #     while swap_ongoing:
    #         # REDEEM PATH:
    #         # invoice is paid and maker redeems the P2SH utxo
    #         # check invoice paid if lncli of redeem tx
    #         utxo_sum = await self.kas_funded_amount(self.swap)
    #         if not utxo_sum:
    #             swap_ongoing = False
    #             logger.info(f"Maker redeemed the contract, exiting")
    #             swap_result = True
    #             self.db_set_swap_status('COMPLETED')
    #         # REFUND PATH:
    #         # invoice is not paid and taker refunds after locktime expires
    #         if time.time() * 1000 > self.swap.timelock + 180000 and utxo_sum:
    #             self.swap.sender_private_key = self.get_secret_key()
    #             swap_result = await self.swap.spend_contract()
    #             if swap_result:
    #                 logger.info(f"Refund transaction broadcasted, txid: {swap_result}")
    #                 swap_ongoing = False
    #                 self.db_set_swap_status('REFUNDED')
    #     self.update_address_counter()
    #     self.swap = None
    #     logger.info(f"Swap completed in {time.time() - self.start_time:.2f} seconds")
    #     return swap_result

    # async def btc2kas(self, kas_amount=1000, p2p_price=None, price=None):
    #     if price is None:
    #         price = await self.query_price('btc2kas', kas_amount=kas_amount, p2p_price=p2p_price)

    #     secret = os.urandom(32)
    #     secret_hash = hashlib.sha256(secret).hexdigest()
    #     btc_block_count = await self.sm.bitcoin_service.get_block_height(force_refresh=True)
    #     assert btc_block_count > 0
    #     btc_locktime = btc_block_count + 36

    #     init_swap_response = await self.init_swap(
    #         swap_type='btc2kas',
    #         receiver_address=self.address,
    #         btc_sender_address=self.btc_address.to_string(),
    #         btc_locktime=btc_locktime,
    #         kas_amount=kas_amount,
    #         price=price,
    #         secret_hash=secret_hash
    #     )
    #     logger.info(f"btc2kas init swap response: {init_swap_response}")
    #     if not init_swap_response:
    #         return

    #     assert self.swap.sat_amount == int(kas_amount * price)

    #     # send BTC to the contract address
    #     # automatic tx postponed, just log a warning with the address and amount
    #     logger.info(f"Funding P2WSH address {self.swap.btc_contract_address} with {self.swap.sat_amount / 1e8} BTC")
    #     txid = await self.sm.btc_wallet_service.send_to_address(
    #         self.swap.btc_contract_address,
    #         self.swap.sat_amount,
    #     )

    #     logger.info(f"BTC transaction sent ({txid})")
    #     # monitor BTC utxo, counterparty won't act unless btc p2sh is funded
    #     while not await self.btc_funded(self.swap):
    #         await asyncio.sleep(10)
    #     logger.info('BTC address funded!')

    #     redeemed = False
    #     while not redeemed:
    #         _funded = await self.kas_funded_amount(self.swap)
    #         if _funded >= kas_amount:
    #             if await self.kas_confirmed(self.swap):
    #                 self.swap.receiver_private_key = self.get_secret_key()
    #                 print('.', end='', flush=True)
    #                 txid = await self.swap.spend_contract(secret=secret)
    #                 if txid:
    #                     redeemed = True
    #                     self.db_set_swap_status('COMPLETED')
    #                     break
    #             await asyncio.sleep(30)
    #             continue

    #         daa_score = await self.sm.kaspad_service.get_daa_score()
    #         if daa_score >= self.swap.timelock:
    #             logger.info(f"DAA score {daa_score} is greater than timelock {self.swap.timelock}, triggering refund")
    #             break
        
    #     if not redeemed:
    #         while True:
    #             height = await self.sm.bitcoin_service.get_block_height()
    #             if height >= self.swap.btc_locktime:
    #                 break
    #             await asyncio.sleep(60)
    #         self.swap.btc_sender_private_key = self.get_secret_key(is_btc=True)
    #         res = await self.swap.spend_btc_contract()
    #         logger.info(f"Swap refunded! ({res})")
    #         if res:
    #             self.db_set_swap_status('REFUNDED')

    # async def kas2btc(self, kas_amount=1000, p2p_price=None, price=None):
    #     if price is None:
    #         price = await self.query_price('kas2btc', kas_amount=kas_amount, p2p_price=p2p_price)

    #     secret = os.urandom(32)
    #     secret_hash = hashlib.sha256(secret).hexdigest()
    #     daa_score = await self.sm.kaspad_service.get_daa_score(force_refresh=True)
    #     assert daa_score > 0
    #     kas_locktime = daa_score + (10 * 60 * 60 * 6)

    #     init_swap_response = await self.init_swap(
    #         swap_type='kas2btc',
    #         sender_address=self.address,
    #         btc_receiver_address=self.btc_address.to_string(),
    #         kas_locktime=kas_locktime,
    #         kas_amount=kas_amount,
    #         price=price,
    #         secret_hash=secret_hash
    #     )
    #     if not init_swap_response:
    #         return

    #     assert self.swap.sat_amount == int(kas_amount * price)

    #     # send KAS to the contract address
    #     if self.sm and getattr(self.sm, '_preferred_wallet', 'external') != 'external':
    #         await self.sm.kaspa_wallet_service.pay(self.swap.contract_address, kas_amount, sm=self.sm)
    #     else:
    #         logger.warning(f"Fund P2SH address {self.swap.contract_address} with {kas_amount} KAS")
    #     # monitor KAS utxo, counterparty won't act until the address is funded
    #     while True:
    #         kas_funded = await self.kas_funded_amount(self.swap)
    #         if kas_funded >= kas_amount:
    #             break
    #         if await self.kas_locktime_expired(self.swap):
    #             kas_funded = False
    #             break
    #         await asyncio.sleep(1)

    #     while not await self.btc_funded(self.swap):
    #         if await self.kas_locktime_expired(self.swap):
    #             break
    #         await asyncio.sleep(10)
    #     btc_funded = await self.btc_funded(self.swap)
    #     if btc_funded:
    #         while not await self.btc_confirmed(self.swap):
    #             if await self.kas_locktime_expired(self.swap):
    #                 self.swap.sender_private_key = self.get_secret_key()
    #                 await self.swap.spend_contract()
    #                 return
    #             await asyncio.sleep(self.WATCH_TICK_S)
    #         self.swap.btc_receiver_private_key = self.get_secret_key(is_btc=True)
    #         await self.swap.spend_btc_contract(secret=secret)
    #     else:
    #         # refund after locktime
    #         self.swap.sender_private_key = self.get_secret_key()
    #         await self.swap.spend_contract()

    # --- Watch loops ---
    #
    # One per swap type, each looping over the shared checks in Counterparty
    # and emitting a SwapEvent per tick. These are the only methods here that
    # sleep: the checks below them look once and return, which is what lets
    # the maker reuse them from its own tick.
    #
    # None of them signs or spends. They report where the swap has got to and
    # leave acting to the caller, until the Phase 4 orchestrators take over.
    #
    # is_settled is a temporary seam for the UI, which performs the on-chain
    # redeem itself and needs the loop to stop calling the swap redeemable.
    # The LN pair takes no such argument: there the redeem empties the kaspa
    # contract, which the loop notices on its own.

    async def watch_kas2sat(self, kas_amount=None, on_event=None):
        """We funded the contract; wait for the maker to sweep it.

        Reports the funded amount every tick so a frontend can show partial
        funding, then finishes once the contract is emptied.
        """
        swap = self.swap
        if kas_amount is None:
            kas_amount = swap.kas_amount
        # Resume after a sweep starts with no UTXOs. The live path only
        # treats that as redeemed once this process has already seen funds.
        funded_seen = getattr(self.db_swap, 'status', None) == 'FUNDED'
        while True:
            funding = await self.check_kas_funding(
                swap,
                kas_amount,
                known=kas_amount if funded_seen else None,
                on_event=on_event,
                waiting_status=SwapStatus.MONITORING,
            )
            if funding.status == SwapStatus.EXPIRED:
                if funded_seen:
                    spent = await self.check_contract_spent(swap, kas_amount)
                    if spent.status == SwapStatus.COMPLETED:
                        return await self._report(on_event, SwapStatus.COMPLETED)
                return funding
            if funding.status == SwapStatus.FUNDED:
                funded_seen = True
                if self.db_swap is not None and self.db_swap.status == 'OPENED':
                    self.db_set_swap_status('FUNDED', finalize_swap=False)
                spent = await self.check_contract_spent(swap, kas_amount)
                if spent.status == SwapStatus.COMPLETED:
                    return await self._report(on_event, SwapStatus.COMPLETED)
            await asyncio.sleep(self.WATCH_TICK_S)

    async def watch_sat2kas(self, kas_amount=None, on_event=None, return_when_ready=False):
        """The maker funds the contract; wait for it to confirm, then hold at
        ready-to-pay until the invoice is paid and the contract redeemed.

        Once the contract is full the interesting state is the payment, not
        the funding, so the funding check reports through this method rather
        than emitting for itself.

        return_when_ready hands control back at ready-to-pay instead of
        holding there. A watcher wants the hold, because something else does
        the paying; an orchestrator is the one that will pay, and cannot while
        it is awaiting this loop.
        """
        swap = self.swap
        if kas_amount is None:
            kas_amount = swap.kas_amount
        funded_seen = False
        payment_prompted = False
        while True:
            funding = await self.check_kas_funding(
                swap, kas_amount, known=kas_amount if funded_seen else None
            )
            if funding.status == SwapStatus.EXPIRED:
                return await self.emit(on_event, funding)
            if funding.status != SwapStatus.FUNDED:
                await self.emit(on_event, funding)
                await asyncio.sleep(self.WATCH_TICK_S)
                continue
            funded_seen = True

            confirmations = await self.check_kas_confirmations(swap, funded=funding.funded)
            if confirmations.status == SwapStatus.EXPIRED:
                return await self.emit(on_event, confirmations)
            if confirmations.status != SwapStatus.FUNDED:
                await self.emit(on_event, confirmations)
                await asyncio.sleep(self.WATCH_TICK_S)
                continue

            spent = await self.check_contract_spent(swap, kas_amount)
            if spent.status == SwapStatus.COMPLETED:
                # Deliberately terminal without an event: whoever performed
                # the redeem has already recorded the outcome.
                return spent

            if payment_prompted:
                await self._report(
                    on_event, SwapStatus.WAITING_USER_PAYMENT,
                    funded=funding.funded, time_remaining=funding.time_remaining,
                )
            else:
                payment_prompted = True
                ready = await self._report(
                    on_event, SwapStatus.READY_TO_PAY, confirmed=True,
                    funded=funding.funded, time_remaining=funding.time_remaining,
                )
                if return_when_ready:
                    return ready
            await asyncio.sleep(self.WATCH_TICK_S)

    async def watch_btc2kas(self, kas_amount=None, sat_amount=None, on_event=None, is_settled=None,
                            return_when_ready=False):
        """We fund bitcoin first: wait for our funding and its confirmations,
        then the maker's kaspa (and its confirmations), then hold at
        ready-to-redeem.

        BTC locktime governs our own funding wait (when we can reclaim). Once
        BTC is confirmed, KAS locktime governs the redeem race — the maker's
        shorter lock is when they can reclaim KAS. BTC confirmations are
        reported so a UI is not told the contract is "Confirmed!" before the
        maker will act (maker funds KAS only after BTC confirms).

        return_when_ready as in watch_sat2kas: an orchestrator takes the
        ready-to-redeem event and does the redeem itself.
        """
        swap = self.swap
        if kas_amount is None:
            kas_amount = swap.kas_amount
        btc_funded_seen = False
        btc_confirmed_seen = False
        kas_funded_seen = False
        while True:
            # Asked first, and every tick: once we have redeemed, the contract
            # we were watching is gone, so every check below would report a
            # swap going backwards.
            if is_settled is not None and is_settled():
                return await self._report(on_event, SwapStatus.COMPLETED)

            btc = await self.check_btc_funding(
                swap, sat_amount, known=True if btc_funded_seen else None
            )
            if btc.status == SwapStatus.EXPIRED:
                return await self._settled_or(on_event, btc, is_settled)
            if btc.status != SwapStatus.BTC_FUNDED:
                await self.emit(on_event, btc)
                await asyncio.sleep(self.WATCH_TICK_S)
                continue
            if not btc_funded_seen:
                btc_funded_seen = True
                if self.db_swap is not None and self.db_swap.status == 'OPENED':
                    self.db_set_swap_status('FUNDED', finalize_swap=False)
                await self.emit(on_event, btc)

            # Maker will not fund KAS until BTC is confirmed; surface that wait.
            btc_conf = await self.check_btc_confirmations(
                swap, funded=kas_amount, deadline='btc',
            )
            if btc_conf.status == SwapStatus.EXPIRED:
                return await self._settled_or(on_event, btc_conf, is_settled)
            if btc_conf.status != SwapStatus.BTC_FUNDED:
                await self.emit(on_event, btc_conf)
                await asyncio.sleep(self.WATCH_TICK_S)
                continue
            # Always announce our side is confirmed before looking at KAS.
            # Same race as watch_kas2btc when the maker already funded.
            if not btc_confirmed_seen:
                btc_confirmed_seen = True
                await self._report(
                    on_event, SwapStatus.WAITING_COUNTERPARTY,
                    funded=kas_amount,
                    blocks_remaining=btc_conf.blocks_remaining,
                    daa_remaining=btc_conf.daa_remaining,
                    time_remaining=btc_conf.time_remaining,
                )

            kas = await self.check_kas_funding(
                swap, kas_amount,
                known=kas_amount if kas_funded_seen else None,
                deadline='kas',
                waiting_status=SwapStatus.WAITING_COUNTERPARTY,
            )
            if kas.status == SwapStatus.EXPIRED:
                return await self._settled_or(on_event, kas, is_settled)
            if kas.status != SwapStatus.FUNDED:
                await self.emit(on_event, kas)
                await asyncio.sleep(self.WATCH_TICK_S)
                continue
            if not kas_funded_seen:
                kas_funded_seen = True
                await self.emit(on_event, kas)

            confirmations = await self.check_kas_confirmations(
                swap, funded=kas_amount, deadline='kas',
            )
            if confirmations.status == SwapStatus.EXPIRED:
                return await self._settled_or(on_event, confirmations, is_settled)
            if confirmations.status != SwapStatus.FUNDED:
                await self.emit(on_event, confirmations)
                await asyncio.sleep(self.WATCH_TICK_S)
                continue

            ready = await self._report(
                on_event, SwapStatus.READY_TO_REDEEM,
                funded=kas_amount,
                daa_remaining=confirmations.daa_remaining,
                time_remaining=confirmations.time_remaining,
                blocks_remaining=confirmations.blocks_remaining,
            )
            if return_when_ready:
                return ready
            await asyncio.sleep(self.WATCH_TICK_S)

    async def watch_kas2btc(self, kas_amount=None, sat_amount=None, on_event=None, is_settled=None,
                            return_when_ready=False):
        """We fund kaspa first: wait for our funding and its confirmations,
        then the maker's bitcoin (and its confirmations), then hold at
        ready-to-redeem.

        The bitcoin locktime is the deadline for every active step: that is
        when the maker can reclaim BTC and our redeem window closes. (KAS
        locktime is longer and only matters later, in _refund_kas.) KAS
        confirmations are still reported so a UI is not stuck in
        waiting_counterparty with no signal that our side is confirming.

        return_when_ready as in watch_sat2kas.
        """
        swap = self.swap
        if kas_amount is None:
            kas_amount = swap.kas_amount
        kas_funded_seen = False
        kas_confirmed_seen = False
        btc_funded_seen = False
        while True:
            # Asked first, for the same reason as btc2kas: after our redeem
            # the bitcoin contract is empty and the checks below would walk
            # the swap backwards.
            if is_settled is not None and is_settled():
                return await self._report(on_event, SwapStatus.COMPLETED)

            kas = await self.check_kas_funding(
                swap, kas_amount,
                known=kas_amount if kas_funded_seen else None,
                deadline='btc',
            )
            if kas.status == SwapStatus.EXPIRED:
                return await self._settled_or(on_event, kas, is_settled)
            if kas.status != SwapStatus.FUNDED:
                await self.emit(on_event, kas)
                await asyncio.sleep(self.WATCH_TICK_S)
                continue
            if not kas_funded_seen:
                kas_funded_seen = True
                if self.db_swap is not None and self.db_swap.status == 'OPENED':
                    self.db_set_swap_status('FUNDED', finalize_swap=False)
                await self.emit(on_event, kas)

            # Maker will not fund BTC until KAS is confirmed; surface that wait.
            kas_conf = await self.check_kas_confirmations(
                swap, funded=kas_amount, deadline='btc',
            )
            if kas_conf.status == SwapStatus.EXPIRED:
                return await self._settled_or(on_event, kas_conf, is_settled)
            if kas_conf.status != SwapStatus.FUNDED:
                await self.emit(on_event, kas_conf)
                await asyncio.sleep(self.WATCH_TICK_S)
                continue
            # Always announce our side is confirmed before looking at BTC.
            # If the maker already funded (lower conf threshold), the next
            # check skips WAITING_COUNTERPARTY — without this emit the UI
            # would leave KAS on "Funded, waiting for confirmations...".
            if not kas_confirmed_seen:
                kas_confirmed_seen = True
                await self._report(
                    on_event, SwapStatus.WAITING_COUNTERPARTY,
                    funded=kas_amount,
                    blocks_remaining=kas_conf.blocks_remaining,
                    daa_remaining=kas_conf.daa_remaining,
                    time_remaining=kas_conf.time_remaining,
                )

            btc = await self.check_btc_funding(
                swap, sat_amount,
                known=True if btc_funded_seen else None,
                funded=kas_amount,
                deadline='btc',
                waiting_status=SwapStatus.WAITING_COUNTERPARTY,
            )
            if btc.status == SwapStatus.EXPIRED:
                return await self._settled_or(on_event, btc, is_settled)
            if btc.status != SwapStatus.BTC_FUNDED:
                await self.emit(on_event, btc)
                await asyncio.sleep(self.WATCH_TICK_S)
                continue
            if not btc_funded_seen:
                btc_funded_seen = True
                await self.emit(on_event, btc)

            confirmations = await self.check_btc_confirmations(
                swap, funded=kas_amount, deadline='btc',
            )
            if confirmations.status == SwapStatus.EXPIRED:
                return await self._settled_or(on_event, confirmations, is_settled)
            if confirmations.status != SwapStatus.BTC_FUNDED:
                await self.emit(on_event, confirmations)
                await asyncio.sleep(self.WATCH_TICK_S)
                continue

            ready = await self._report(
                on_event, SwapStatus.READY_TO_REDEEM, btc_funded=True,
                funded=kas_amount, blocks_remaining=confirmations.blocks_remaining,
            )
            if return_when_ready:
                return ready
            await asyncio.sleep(self.WATCH_TICK_S)

    async def _settled_or(self, on_event, event, is_settled=None):
        """Report an expiry, unless the swap settled since the last tick."""
        if is_settled is not None and is_settled():
            return await self._report(on_event, SwapStatus.COMPLETED)
        return await self.emit(on_event, event)

    # --- Orchestrators ---
    #
    # One per swap type, each running a swap end to end: quote, gather what
    # only a human can give, open, fund, watch, settle. They are what a
    # frontend calls instead of reimplementing the sequence, and the legacy
    # four stay beside them untouched until the new path is trusted.
    #
    # Named run_* for now. The rename to the four plain names happens in one
    # mechanical step once regtest has signed these off.

    async def run_kas2sat(self, kas_amount=None, sat_amount=None, base_rate=None,
                          p2p_price=None, on_event=None, interaction=None,
                          wait_confirmation=False):
        """Send KAS, receive sats over Lightning."""
        return await self._guarded(
            self._kas2sat_flow, kas_amount, sat_amount, base_rate, p2p_price,
            on_event=on_event, interaction=interaction,
            wait_confirmation=wait_confirmation,
        )

    async def run_sat2kas(self, kas_amount=None, sat_amount=None, base_rate=None,
                          p2p_price=None, on_event=None, interaction=None,
                          wait_confirmation=False):
        """Pay sats over Lightning, receive KAS."""
        return await self._guarded(
            self._sat2kas_flow, kas_amount, sat_amount, base_rate, p2p_price,
            on_event=on_event, interaction=interaction,
            wait_confirmation=wait_confirmation,
        )

    async def run_btc2kas(self, kas_amount=None, sat_amount=None, base_rate=None,
                          p2p_price=None, on_event=None, interaction=None,
                          wait_confirmation=False):
        """Send BTC on-chain, receive KAS."""
        return await self._guarded(
            self._btc2kas_flow, kas_amount, sat_amount, base_rate, p2p_price,
            on_event=on_event, interaction=interaction,
            wait_confirmation=wait_confirmation,
        )

    async def run_kas2btc(self, kas_amount=None, sat_amount=None, base_rate=None,
                          p2p_price=None, on_event=None, interaction=None,
                          wait_confirmation=False):
        """Send KAS, receive BTC on-chain."""
        return await self._guarded(
            self._kas2btc_flow, kas_amount, sat_amount, base_rate, p2p_price,
            on_event=on_event, interaction=interaction,
            wait_confirmation=wait_confirmation,
        )

    async def run_resume(self, on_event=None, interaction=None, wait_confirmation=False):
        """Hydrate OPENED/FUNDED rows and finish them one at a time, oldest first.

        __init__ schedules this when a loop is already running, so a caller
        only awaits it directly when it constructed the taker outside one.
        """
        if interaction is None:
            interaction = self.interaction
        if on_event is None:
            on_event = self.on_event
        result = None
        for row, swap in await self.resume_live_swaps():
            self.db_swap, self.swap = row, swap
            result = await self._guarded(
                self._continue_resumed, on_event=on_event, interaction=interaction,
                wait_confirmation=wait_confirmation,
            )
            self.swap = None
            self.db_swap = None
        return result

    async def _guarded(self, flow, *args, on_event=None, interaction=None,
                       wait_confirmation=False):
        """Run a flow, then leave the taker ready for the next swap.

        Every way a swap can end converges here, which is why both the
        cancellation handling and the key rotation live here rather than in
        each flow: a swap that was cancelled burned its key exactly like one
        that completed.

        A startup resume has to finish before a new swap opens, or the two
        would run on the same key. Its failure is its own, though: we log it
        and carry on rather than raising it at whoever asked for a new swap.

        wait_confirmation: when True, _settle_broadcast awaits the settlement
        task (COMPLETED/REFUNDED) before the flow returns. Default False so
        UI can leave on COMPLETING/REFUNDING while promote continues in background.
        """
        if interaction is None:
            interaction = self.interaction
        task = self._resume_task
        if task is not None and task is not asyncio.current_task():
            if not task.done():
                logger.warning('Resume task is not ended, waiting before starting a new swap')
            await asyncio.wait([task])
            self._resume_task = None
            if not task.cancelled() and task.exception() is not None:
                logger.error(f"Startup resume failed: {task.exception()}")
        self.start_time = time.time()
        prev_wait = getattr(self, '_wait_confirmation', False)
        self._wait_confirmation = wait_confirmation
        if not self._settlements_resumed:
            self.resume_pending_settlements(on_event=on_event)
            self._settlements_resumed = True
        skipped = False
        try:
            return await flow(*args, on_event=on_event, interaction=interaction)
        except asyncio.CancelledError:
            await self._abandon('EXPIRED', on_event)
            raise
        except Exception as e:
            # Everything, not just SwapError. Almost nothing that goes wrong
            # in a swap is one: an aiohttp error talking to the maker, a
            # KeyError on a malformed response, an RPC failure inside a wallet
            # service. Those used to escape, leaving the taker holding a swap
            # it thought was still running and a row nobody would revisit.
            if isinstance(e, SwapRejected) and str(e) == 'A swap is already running':
                logger.error(e)
                skipped = True
                return None
            logger.error(e, exc_info=not isinstance(e, SwapError))
            await self._abandon('FAILED', on_event, error=str(e))
            return None
        finally:
            self._wait_confirmation = prev_wait
            if not skipped:
                self.rotate_key()
                logger.info(f"Swap finished in {time.time() - self.start_time:.2f} seconds")

    async def _abandon(self, status, on_event=None, error=None):
        """Stop running this swap, writing a terminal status only if it is safe.

        Terminal means invisible: FAILED and EXPIRED are outside LIVE_STATUSES,
        so resume never looks at the row again. Written over a funded contract
        that is a permanent loss, and it is the same money whether the flow hit
        an exception or the user pressed cancel - a cancellation is a decision
        about a swap, not about the funds already in it.

        So once our capital is out there the row keeps its live status and the
        UI is told the swap is still going. The next resume pass picks it up
        and drives it to a redeem or, at worst, a refund after locktime.
        """
        row, swap = self.db_swap, self.swap
        self.swap = None
        try:
            # Bounded, and cancellation-proof: this also runs while handling a
            # CancelledError, where a further await can be cancelled again the
            # moment it suspends. Anything short of a clear "nothing is out
            # there" keeps the row live, so failing here fails safe.
            committed = await asyncio.wait_for(
                self.capital_committed(row, swap), self.ABANDON_PROBE_S,
            )
        except BaseException as e:
            logger.warning(f"Cannot tell whether the swap is funded: {e!r}")
            committed = True
        if committed:
            logger.warning(
                f"Not writing {status}: the swap holds funds, leaving it "
                f"{getattr(row, 'status', 'live')} for resume"
            )
            self.db_swap = None
            await self._report_quietly(
                on_event, SwapStatus.MONITORING,
                error=error or f"Swap interrupted ({status.lower()}), will resume",
            )
            return False
        self.db_set_swap_status(status)
        if status == 'FAILED':
            await self._report_quietly(on_event, SwapStatus.FAILED, error=error)
        return True

    def can_cancel(self):
        """Whether stopping the running swap now is still just a cancellation.

        Once our money is in a contract there is nothing left to cancel: the
        swap either redeems or refunds after locktime, and both need the flow
        that is running. So a frontend asks here before offering the option.

        Cheap and synchronous, because a button handler cannot await. It reads
        the row's status only, so the narrow window where funding is out but
        the row has not caught up answers True - _abandon does the thorough
        check and declines to write a terminal status there.
        """
        row = self.db_swap
        if row is None:
            return True
        return row.status not in self.COMMITTED_STATUSES

    async def _report_quietly(self, on_event, status, **fields):
        """Emit on a path that is already unwinding, where a raise helps nobody."""
        try:
            return await self._report(on_event, status, **fields)
        except BaseException as e:
            logger.warning(f"Could not report {status}: {e!r}")
            return None

    def rotate_key(self):
        """Move past the key this swap used, whatever the outcome.

        A key is spent the moment it is offered to the maker - funded or not,
        completed or not - so this runs on every terminal path, which is the
        one thing the legacy flows only did when a swap reached its end.

        The target is the row count rather than counter + 1: an attempt that
        failed before writing a row consumed no index, and incrementing
        regardless would put the counter ahead of the rows and trip
        assert_key_space on the next start.
        """
        rows = Swap.select().where(Swap.side == self.SIDE).count()
        if rows > self.wallet.address_counter:
            self.update_address_counter(new_counter=rows)

    async def _kas2sat_flow(self, kas_amount, sat_amount, base_rate, p2p_price, on_event=None, interaction=None):
        quote = await self._resolve_quote(
            'kas2sat', kas_amount, sat_amount, base_rate, p2p_price,
        )
        kas_lock, sat_lock = quote['kas_amount'], quote['sat_amount']
        price = quote['price']
        if price is None:
            raise SwapRejected('Quote has no price')

        if self._wallet_is_external('ln'):
            invoice = await interaction.request_ln_invoice(sat_lock)
        else:
            invoice = await self.create_ln_invoice(sat_lock)
        logger.info(f"Requesting kas2sat swap with sender address {self.address} and invoice {invoice}")

        await self.open_swap(
            swap_type='kas2sat',
            sender_address=self.address,
            ln_invoice=invoice,
            kas_amount=kas_lock,
            sat_amount=sat_lock,
            price=price,
        )
        self._assert_init_matches_quote(quote)
        await self._emit_initialized(on_event)

        # Watch must run even if Pay/External is never tapped: the contract is
        # already on screen, so a send to that address has to be seen. Pay still
        # sends through this task when the user taps.
        fund_task = asyncio.create_task(
            self._fund('kas', interaction, on_event, amount=kas_lock)
        )

        event = await self.watch_kas2sat(kas_amount=kas_lock, on_event=on_event)
        if not fund_task.done() and hasattr(interaction, 'abandon'):
            interaction.abandon()
        if event.status == SwapStatus.COMPLETED:
            logger.info('Maker redeemed the contract')
            self._settled('COMPLETED')
            return True
        return await self._refund_kas(on_event, interaction)

    async def _sat2kas_flow(self, kas_amount, sat_amount, base_rate, p2p_price, on_event=None, interaction=None):
        quote = await self._resolve_quote(
            'sat2kas', kas_amount, sat_amount, base_rate, p2p_price,
        )
        kas_lock, sat_lock = quote['kas_amount'], quote['sat_amount']
        price = quote['price']
        if price is None:
            raise SwapRejected('Quote has no price')
        logger.info(f"Requesting sat2kas swap with receiver address {self.address}")

        await self.open_swap(
            swap_type='sat2kas',
            receiver_address=self.address,
            kas_amount=kas_lock,
            sat_amount=sat_lock,
            price=price,
        )
        self._assert_init_matches_quote(quote)
        await self._emit_initialized(on_event)

        event = await self.watch_sat2kas(kas_amount=kas_lock, on_event=on_event, return_when_ready=True)
        if event.status != SwapStatus.READY_TO_PAY:
            # Includes the loop reporting the contract as spent. That means
            # the maker reclaimed it before we paid, which is an expiry for
            # us, not a completion: watch_sat2kas calls a swept contract
            # COMPLETED because the UI reaches that state after its own
            # redeem, and an orchestrator returns before ever redeeming.
            return self._settled('EXPIRED')

        min_daa = getattr(self.sm.kaspad_service, 'min_daa_confirmations', None)
        if min_daa is None:
            min_daa = int(os.getenv('MIN_DAA_CONFIRMATIONS', 150))
        if self.swap.timelock / 1000 - time.time() < max(30, min_daa * 4 / 10):
            logger.info('Too little invoice time left to pay, not honoring')
            return self._settled('EXPIRED')

        secret = None
        if not self._wallet_is_external('ln') and not hasattr(interaction, 'answer'):
            secret = await self._pay_invoice_resolved(on_event)
        if not secret:
            secret = await interaction.request_preimage(
                self.swap.invoice,
                payment_hash=self.swap.secret_hash.hex(),
                timeout=int((self.swap.timelock / 1000) - time.time()),
            )
        if not self.swap.validate_preimage(secret):
            raise PreimageInvalid('The preimage does not match the invoice payment hash')
        if self.db_swap is not None:
            self.db_swap.secret = secret
        self.db_set_swap_status('FUNDED', finalize_swap=False)
        await self._report(on_event, SwapStatus.BTC_FUNDED)

        return await self._redeem(
            'kas', bytes.fromhex(secret), on_event=on_event, interaction=interaction,
        )

    async def _btc2kas_flow(self, kas_amount, sat_amount, base_rate, p2p_price, on_event=None, interaction=None):
        quote = await self._resolve_quote(
            'btc2kas', kas_amount, sat_amount, base_rate, p2p_price,
        )
        kas_lock, sat_lock = quote['kas_amount'], quote['sat_amount']
        price = quote['price']
        if price is None:
            raise SwapRejected('Quote has no price')
        secret = os.urandom(32)

        await self.open_swap(
            swap_type='btc2kas',
            receiver_address=self.address,
            btc_sender_address=self.btc_address.to_string(),
            btc_locktime=await self.gen_btc_locktime('taker'),
            kas_amount=kas_lock,
            sat_amount=sat_lock,
            price=price,
            secret_hash=hashlib.sha256(secret).hexdigest(),
            secret=secret,
        )
        # We are buying KAS, so the sat amount rounds up, against us: same
        # rule as sat2kas, and the same rule the maker applies. Truncating
        # here instead put the two sides one sat apart on every amount whose
        # product was not already whole.
        self._assert_init_matches_quote(quote)
        await self._emit_initialized(on_event)

        fund_task = asyncio.create_task(
            self._fund('btc', interaction, on_event)
        )

        event = await self.watch_btc2kas(kas_amount=kas_lock, on_event=on_event, return_when_ready=True)
        if not fund_task.done() and hasattr(interaction, 'abandon'):
            interaction.abandon()
        if event.status == SwapStatus.READY_TO_REDEEM:
            return await self._redeem(
                'kas', secret, on_event=on_event, interaction=interaction,
            )
        return await self._refund_btc(on_event, interaction)

    async def _kas2btc_flow(self, kas_amount, sat_amount, base_rate, p2p_price, on_event=None, interaction=None):
        quote = await self._resolve_quote(
            'kas2btc', kas_amount, sat_amount, base_rate, p2p_price,
        )
        kas_lock, sat_lock = quote['kas_amount'], quote['sat_amount']
        price = quote['price']
        if price is None:
            raise SwapRejected('Quote has no price')
        secret = os.urandom(32)

        await self.open_swap(
            swap_type='kas2btc',
            sender_address=self.address,
            btc_receiver_address=self.btc_address.to_string(),
            kas_locktime=await self.gen_kas_locktime('taker'),
            kas_amount=kas_lock,
            sat_amount=sat_lock,
            price=price,
            secret_hash=hashlib.sha256(secret).hexdigest(),
            secret=secret,
        )
        # Selling KAS, so the sat amount rounds down, against us again, and
        # matching the maker's floor.
        self._assert_init_matches_quote(quote)
        await self._emit_initialized(on_event)

        fund_task = asyncio.create_task(
            self._fund('kas', interaction, on_event)
        )

        event = await self.watch_kas2btc(kas_amount=kas_lock, on_event=on_event, return_when_ready=True)
        if not fund_task.done() and hasattr(interaction, 'abandon'):
            interaction.abandon()
        if event.status == SwapStatus.READY_TO_REDEEM:
            return await self._redeem(
                'btc', secret, on_event=on_event, interaction=interaction,
            )
        return await self._refund_kas(on_event, interaction)

    async def _continue_resumed(self, on_event=None, interaction=None):
        """Pick up after funding: same watch/redeem/refund tails as the live flows."""
        await self._emit_initialized(on_event)
        st = self.db_swap.swap_type
        kas_amount = self.swap.kas_amount
        secret = self.swap.secret
        if isinstance(secret, str):
            secret = bytes.fromhex(secret)

        if st == 'kas2sat':
            event = await self.watch_kas2sat(kas_amount=kas_amount, on_event=on_event)
            if event.status == SwapStatus.COMPLETED:
                logger.info('Maker redeemed the contract')
                self._settled('COMPLETED')
                return True
            return await self._refund_kas(on_event, interaction)

        if st == 'sat2kas':
            event = await self.watch_sat2kas(
                kas_amount=kas_amount, on_event=on_event, return_when_ready=True,
            )
            if event.status != SwapStatus.READY_TO_PAY:
                if secret:
                    return self._settled('COMPLETED')
                return self._settled('EXPIRED')
            secret_hex = secret.hex() if isinstance(secret, bytes) else secret
            if not self.swap.validate_preimage(secret_hex):
                raise PreimageInvalid('The preimage does not match the invoice payment hash')
            return await self._redeem(
                'kas', secret, on_event=on_event, interaction=interaction,
            )

        if st == 'btc2kas':
            event = await self.watch_btc2kas(
                kas_amount=kas_amount, on_event=on_event, return_when_ready=True,
            )
            if event.status == SwapStatus.READY_TO_REDEEM:
                return await self._redeem(
                    'kas', secret, on_event=on_event, interaction=interaction,
                )
            return await self._refund_btc(on_event, interaction)

        if st == 'kas2btc':
            event = await self.watch_kas2btc(
                kas_amount=kas_amount, on_event=on_event, return_when_ready=True,
            )
            if event.status == SwapStatus.READY_TO_REDEEM:
                return await self._redeem(
                    'btc', secret, on_event=on_event, interaction=interaction,
                )
            return await self._refund_kas(on_event, interaction)

        raise SwapError(f"Cannot resume swap type {st}")

    # --- Orchestrator plumbing ---

    async def _quote(self, swap_type, kas_amount, p2p_price):
        """One price for this swap, from the maker's offers.

        query_price answers (price_or_offers, valid_until), and hands back the
        whole book unless p2p_price pins an offer down, so picking from it is
        the caller's job.
        """
        quote = await self.query_price(swap_type, kas_amount=kas_amount, p2p_price=p2p_price)
        if not quote:
            raise SwapRejected(f"Maker has no {swap_type} offer for {kas_amount} KAS")
        price, _valid_until = quote
        if isinstance(price, dict):
            # Same choice query_price itself makes: best price for our side.
            best = sorted(price, key=lambda k: price[k][0], reverse=(swap_type in ('kas2sat', 'kas2btc')))[0]
            price = price[best][0]
        return price

    async def _resolve_quote(self, swap_type, kas_amount=None, sat_amount=None,
                             base_rate=None, p2p_price=None):
        given_kas = kas_amount is not None
        given_sat = sat_amount is not None
        if not given_kas and not given_sat:
            raise SwapRejected('Quote needs kas_amount and/or sat_amount')

        # Caller already has the lock pair (UI Start). Do not re-quote or
        # recompute; open_swap matches this pair to the maker's locked slot.
        if given_kas and given_sat:
            rate = base_rate
            if rate is None:
                try:
                    rate = await self._quote(swap_type, kas_amount, p2p_price)
                except SwapRejected:
                    rate = None
            return {
                'type': 'swap_quote',
                'kas_amount': kas_amount,
                'sat_amount': sat_amount,
                'price': rate,
                'p2p_price': p2p_price,
                'fee': None,
                'valid_until': None,
            }

        remote = await self.query_quote(
            swap_type, kas_amount=kas_amount, sat_amount=sat_amount,
        )
        if not remote:
            raise SwapRejected(f"Maker has no {swap_type} quote")
        valid_until = remote.get('valid_until') or 0
        if valid_until and valid_until < time.time():
            raise SwapRejected('Quote expired')
        rate = base_rate if base_rate is not None else remote['price']
        if base_rate is not None and abs(remote['price'] - base_rate) > 1e-9:
            raise SwapRejected(
                f"Quoted price {remote['price']} != base_rate {base_rate}"
            )
        await self._assert_quote_sane(
            swap_type, remote, rate, named_kas=given_kas,
        )
        return remote

    async def _assert_quote_sane(self, swap_type, quote, price, named_kas):
        kas_lock = quote['kas_amount']
        sat_lock = quote['sat_amount']
        if kas_lock <= 0 or sat_lock <= 0:
            raise SwapRejected('Quoted lock is not positive')

        local_fees = await self._quote_fees(
            swap_type, invoice_sats=sat_lock, kas_amount=kas_lock,
        )
        maker_fees = quote.get('fee')
        if maker_fees:
            self._assert_fees_not_fraud(maker_fees, local_fees)
            fees = {
                'kas_spend': maker_fees.get('kas_spend') or 0,
                'btc_spend': maker_fees.get('btc_spend') or 0,
                'ln_fee': maker_fees.get('ln_fee') or 0,
                'kas_fund': maker_fees.get('kas_fund') or 0,
                'btc_fund': maker_fees.get('btc_fund') or 0,
            }
        else:
            fees = {
                'kas_spend': local_fees.get('kas_spend') or 0,
                'btc_spend': local_fees.get('btc_spend') or 0,
                'ln_fee': local_fees.get('ln_fee') or 0,
                'kas_fund': local_fees.get('kas_fund') or 0,
                'btc_fund': local_fees.get('btc_fund') or 0,
            }

        # Recover the named amount from the lock so identities that add
        # spend (receive-KAS, kas2btc sat-named) recompute the same pair.
        if named_kas:
            if swap_type in ('sat2kas', 'btc2kas'):
                named = kas_lock - fees['kas_spend']
            else:
                named = kas_lock
            expected_kas, expected_sat = self._expected_locks(
                swap_type, kas_amount=named, sat_amount=None,
                price=price, fees=fees,
            )
        else:
            if swap_type == 'kas2btc':
                named = sat_lock - fees['btc_spend']
            else:
                named = sat_lock
            expected_kas, expected_sat = self._expected_locks(
                swap_type, kas_amount=None, sat_amount=named,
                price=price, fees=fees,
            )
        if expected_kas is None or expected_sat is None:
            raise SwapRejected('Could not recompute locks from rate and fees')
        if abs(expected_kas - kas_lock) > 1e-8:
            raise SwapRejected(
                f"KAS lock mismatch, ours: {expected_kas}, quoted: {kas_lock}"
            )
        if abs(expected_sat - sat_lock) > 1:
            raise SwapRejected(
                f"Sat lock mismatch, ours: {expected_sat}, quoted: {sat_lock}"
            )

    def _assert_fees_not_fraud(self, maker_fees, local_fees):
        """Reject absurd maker fees. Local estimate is a cap, not a match. (values are not calibrated and really absurd)"""
        ratio = 10
        absurd_kas = 100.0
        absurd_sats = 100_000_000  # 1 BTC; a 10 BTC fee is the fraud example
        kas_keys = ('kas_spend', 'kas_fund')
        sat_keys = ('btc_spend', 'btc_fund', 'ln_fee')
        for key in kas_keys + sat_keys:
            maker_v = maker_fees.get(key) or 0
            local_v = local_fees.get(key) or 0
            cap = absurd_kas if key in kas_keys else absurd_sats
            if maker_v < 0 or maker_v > cap:
                raise SwapRejected(f"Maker {key} is absurd: {maker_v}")
            if local_v > 0 and maker_v > local_v * ratio:
                raise SwapRejected(
                    f"Maker {key} {maker_v} is far above local estimate {local_v}"
                )

    def _assert_init_matches_quote(self, quote):
        kas_lock, sat_lock = quote['kas_amount'], quote['sat_amount']
        if abs(self.swap.kas_amount - kas_lock) > 1e-8:
            raise SwapRejected(
                f"KAS amount mismatch, ours: {kas_lock}, maker: {self.swap.kas_amount}"
            )
        swap_sats = self.swap.sat_amount or 0
        if abs(swap_sats - sat_lock) > 1:
            raise SwapRejected(
                f"Sat amount mismatch, ours: {sat_lock}, maker: {swap_sats}"
            )

    async def _quote_fees(self, swap_type, invoice_sats=None, kas_amount=None):
        btc_spend = 0
        btc_fund = 0
        ln_fee = 0
        lock_sompi = max(int((kas_amount or 0) * 1e8), 1)
        spend_mass = estimate_spend_mass(
            lock_sompi,
            sigscript_size=(
                P2SH_REDEEM_SIGSCRIPT_DAA if swap_type in ('btc2kas', 'kas2btc')
                else P2SH_REDEEM_SIGSCRIPT_LN
            ),
        )
        fund_mass = estimate_funding_mass(lock_sompi)
        kas_spend_sompi = 0
        kas_fund_sompi = 0
        if self.sm is not None:
            kas_spend_sompi, _ = await self.sm.kaspad_service.estimate_send_fee(spend_mass)
            kas_fund_sompi, _ = await self.sm.kaspad_service.estimate_send_fee(fund_mass)
            btc_spend, _ = await self.sm.bitcoin_service.estimate_send_fee(BTC_SPEND_VSIZE)
            btc_fund, _ = await self.sm.bitcoin_service.estimate_send_fee(BTC_FUNDING_VSIZE)
            if invoice_sats:
                ln_fee = await self.sm.ln_wallet_service.estimate_route_fee(
                    sat_amount=invoice_sats,
                )
        if not kas_spend_sompi:
            kas_spend_sompi = 100 * spend_mass
        if not kas_fund_sompi:
            kas_fund_sompi = 100 * fund_mass
        if not btc_spend:
            btc_spend = BTC_SPEND_VSIZE  # 1 sat/vB
        if not btc_fund:
            btc_fund = BTC_FUNDING_VSIZE
        kas_spend = kas_spend_sompi / 1e8
        kas_fund = kas_fund_sompi / 1e8
        return {
            'kas_spend': kas_spend,
            'btc_spend': btc_spend,
            'ln_fee': ln_fee,
            'kas_fund': kas_fund,
            'btc_fund': btc_fund,
        }

    def _expected_locks(self, swap_type, kas_amount=None, sat_amount=None, price=None, fees=None):
        """Same eight identities as Maker.quote. Exactly one of kas/sat."""
        if (kas_amount is None) == (sat_amount is None) or not price or not fees:
            return None, None
        given_kas = kas_amount is not None
        kas_spend = fees['kas_spend']
        btc_spend = fees['btc_spend']
        kas_fund = fees['kas_fund']
        btc_fund = fees['btc_fund']
        ln_fee = fees['ln_fee']

        if swap_type == 'kas2sat' and given_kas:
            kas_lock = kas_amount
            kas_net = kas_lock - kas_spend
            if kas_net <= 0:
                return None, None
            sat_value = floor(kas_net * price)
            sat_lock = sat_value - ln_fee
        elif swap_type == 'kas2sat' and not given_kas:
            sat_lock = sat_amount
            sat_cost = sat_lock + ln_fee
            kas_net = sat_cost / price
            kas_lock = ceil((kas_net + kas_spend) * 1e8) / 1e8
        elif swap_type == 'sat2kas' and not given_kas:
            sat_lock = sat_amount
            kas_lock = floor(sat_lock / price * 1e8) / 1e8 - kas_fund
        elif swap_type == 'sat2kas' and given_kas:
            kas_lock = kas_amount + kas_spend
            sat_lock = ceil((kas_lock + kas_fund) * price) + ln_fee
        elif swap_type == 'btc2kas' and not given_kas:
            sat_lock = sat_amount
            sat_net = sat_lock - btc_spend
            if sat_net <= 0:
                return None, None
            kas_lock = floor(sat_net / price * 1e8) / 1e8 - kas_fund
        elif swap_type == 'btc2kas' and given_kas:
            kas_lock = kas_amount + kas_spend
            sat_lock = ceil((kas_lock + kas_fund) * price) + btc_spend
        elif swap_type == 'kas2btc' and given_kas:
            kas_lock = kas_amount
            kas_net = kas_lock - kas_spend
            if kas_net <= 0:
                return None, None
            sat_lock = floor(kas_net * price) - btc_fund
        elif swap_type == 'kas2btc' and not given_kas:
            sat_lock = sat_amount + btc_spend
            kas_net = (sat_lock + btc_fund) / price
            kas_lock = ceil((kas_net + kas_spend) * 1e8) / 1e8
        else:
            return None, None

        if kas_lock <= 0 or sat_lock <= 0:
            return None, None
        return kas_lock, sat_lock

    def _wallet_is_external(self, kind):
        """Whether `kind` ('kas' / 'ln' / 'btc') is a wallet we cannot drive.

        No service manager means no wallet we can drive either, which is the
        CLI case the legacy methods handled with a getattr on _preferred_*.
        """
        if self.sm is None:
            return True
        return getattr(self.sm, f"{kind}_wallet_is_external")()

    async def _emit_initialized(self, on_event):
        """The contract exists and both sides agreed on it."""
        swap = self.swap
        return await self._report(
            on_event, SwapStatus.SWAP_INITIALIZED,
            contract_address=swap.contract_address,
            btc_contract_address=swap.btc_contract_address,
            invoice=swap.invoice,
            kas_amount=swap.kas_amount,
            sat_amount=swap.sat_amount,
        )

    async def _fund(self, kind, interaction, on_event, amount=None):
        """Send from our wallet after the frontend confirms.

        Watch already sees money on the contract address. This only
        broadcasts when confirm_funding is True. FUNDED is written when
        the send returns a txid; otherwise the watch loop records it
        when it sees a UTXO.
        """
        swap = self.swap
        if kind == 'kas':
            address = swap.contract_address
            amount = swap.kas_amount if amount is None else amount
        else:
            address = swap.btc_contract_address
            amount = swap.sat_amount if amount is None else amount

        txid = None
        try:
            if self.sm is None:
                raise ExternalWalletRequired('No service manager, fund this one yourself')
            try:
                confirmed = await interaction.confirm_funding(kind, address, amount)
            except asyncio.CancelledError:
                # Watch finished (or the UI left); do not send.
                return None
            if not confirmed:
                return None
            if kind == 'kas':
                txid = await self.fund_kas_contract(swap, amount)
            else:
                txid = await self.fund_btc_contract(swap, amount)
            logger.info(f"Funded {address} with {amount} ({kind}, {txid or 'txid unknown'})")
        except ExternalWalletRequired as e:
            logger.info(f"{e}")
            return None
        if txid:
            if kind == 'kas':
                swap.kas_funding_txid = txid
                self.set_swap_status(self.db_swap, 'FUNDED', txid=txid)
            else:
                swap.btc_funding_txid = txid
                self.set_swap_status(self.db_swap, 'FUNDED', btc_txid=txid)
        return txid

    async def _pay_invoice_resolved(self, on_event=None):
        """Pay the swap's invoice, and do not return until we know its fate.

        A payment whose response we lost is not a payment that failed. It may
        still settle, and treating the silence as a failure sends us straight
        to asking the user for a preimage - which invites them to pay the same
        invoice a second time. So when the call gives us nothing, we ask the
        wallet what became of the hash rather than assuming.

        Returns the preimage, or None when the user has to supply it: either
        the payment definitively failed, or it settled without handing one
        back, or nothing automated the payment in the first place.
        """
        swap = self.swap
        min_daa = getattr(self.sm.kaspad_service, 'min_daa_confirmations', None)
        if min_daa is None:
            min_daa = int(os.getenv('MIN_DAA_CONFIRMATIONS', 150))
        if swap.timelock / 1000 - time.time() < max(30, min_daa * 4 / 10):
            logger.info('Too little invoice time left to pay, not honoring')
            return None
        try:
            secret = await self.pay_ln_invoice(swap.invoice, swap=swap)
            if secret:
                return secret
        except ExternalWalletRequired as e:
            # Nothing was sent and nothing will be: the user pays by hand.
            logger.info(f"{e}")
            return None
        except Exception as e:
            logger.error(e, exc_info=True)

        payment_hash = swap.secret_hash.hex()
        logger.info(f"No preimage from the payment call, tracking {payment_hash}")
        while True:
            remaining = int(swap.timelock / 1000 - time.time())
            if remaining <= 0:
                break
            # check_ln_payment blocks until the payment resolves, so this
            # returns the moment there is news rather than on the next tick.
            payment = await self.check_ln_payment(
                payment_hash, timeout=min(remaining, 30),
            )
            status = payment.get('status')
            if status == PaymentStatus.SETTLED:
                preimage = payment.get('preimage')
                if preimage and swap.validate_preimage(preimage):
                    logger.info('Recovered the preimage of an in-flight payment')
                    return preimage
                logger.error(
                    'The invoice was paid but the wallet returned no usable '
                    'preimage; asking the user for it'
                )
                return None
            if status == PaymentStatus.FAILED:
                logger.info('The payment failed, nothing left the wallet')
                return None
            await self._report(
                on_event, SwapStatus.MONITORING, time_remaining=remaining,
            )
            await asyncio.sleep(self.WATCH_TICK_S)

        # WARNING: the invoice expired with our payment still unresolved. The
        # sats may yet leave while the maker's contract is already refundable,
        # which loses them. It should not be reachable - a payment resolves in
        # seconds and the invoice lives for minutes - and is left unhandled on
        # purpose rather than guessed at. Revisit.
        logger.warning(
            f"Invoice expired with payment {payment_hash} still unresolved"
        )
        return None

    def _settled(self, status, txid=None, btc_txid=None):
        """Record a terminal status and drop the swap.

        Returns the txid so a flow can hand it back to its caller, which is
        what the legacy methods returned.
        """
        if self.db_swap is not None:
            if txid is not None:
                self.db_swap.txid = txid
            if btc_txid is not None:
                self.db_swap.btc_txid = btc_txid
        # Saves the row and drops our handle on it.
        self.db_set_swap_status(status)
        self.swap = None
        return txid if txid is not None else btc_txid

    async def _settle_broadcast(self, pending_db, pending_event, final_db, chain, on_event,
                                txid=None, btc_txid=None, addresses=None):
        """Mark COMPLETING/REFUNDING and start promote; optionally await it.

        By default returns after broadcast (promote runs in background). When
        run_* was called with wait_confirmation=True, await COMPLETED/REFUNDED.
        """
        watch_txid = txid if chain == 'kas' else btc_txid
        await self._report(on_event, pending_event, txid=watch_txid)
        row_id = self.db_swap.id
        # addresses: kas payout for UTXO/DAA only; btc settle is txid-only.
        self._settled(pending_db, txid=txid, btc_txid=btc_txid)
        task = self.start_settlement(
            row_id, chain, watch_txid, final_db, on_event=on_event, addresses=addresses,
        )
        if getattr(self, '_wait_confirmation', False) and task is not None:
            logger.info(f"Waiting for settlement confirmations on {watch_txid}")
            await task
        return watch_txid

    async def _redeem(self, chain, secret, on_event=None, interaction=None):
        """Take the counterparty's contract with the secret, then settle.

        Every flow arrives here the moment the secret is in hand, and the
        contract is ours to spend until the counterparty's locktime lets them
        reclaim it. So a broadcast that does not go out is retried for as long
        as the contract still holds the money, rather than raising: the
        transaction is unique to us, nobody else can send it, and the only
        thing a single failed attempt proves is that the node was unhappy once.

        Returns the txid, or None when the retries ran out - and on None the
        row stays FUNDED with the swap live, which is what resume_live_swaps
        picks up. Writing FAILED here would file a contract we can still spend
        as a closed case.
        """
        is_btc = chain == 'btc'
        output_address = await self.resolve_payout_address(
            self.swap, interaction, is_btc=is_btc,
        )
        n_key = self.key_index(self.db_swap)

        async def spend():
            if is_btc:
                return await self.redeem_btc(
                    self.swap, secret, n_key=n_key, output_address=output_address,
                )
            return await self.redeem_kas(
                self.swap, secret, n_key=n_key, output_address=output_address,
            )

        txid = await self.broadcast_with_retry(
            self.swap, chain, spend,
            deadline=self._redeem_deadline(chain),
            on_attempt=lambda n: self._report(
                on_event, SwapStatus.READY_TO_REDEEM,
                error=f"Redeem broadcast failed ({n}), retrying",
            ),
        )
        if not txid:
            logger.error(f"{chain} redeem never went out, leaving the swap live")
            return None
        logger.info(f"Redeem transaction broadcasted, txid: {txid}")
        return await self._settle_broadcast(
            'COMPLETING', SwapStatus.COMPLETING, 'COMPLETED', chain, on_event,
            txid=None if is_btc else txid,
            btc_txid=txid if is_btc else None,
            addresses=None if is_btc else [output_address],
        )

    def _redeem_deadline(self, chain):
        """When our redeem window shuts, when that is a wall clock.

        Only the LN-derived kaspa contracts carry a timestamp locktime, and
        their refund window opens a grace period after it. Everything else is
        locked to a daa score or a block height, where there is no deadline to
        compare against and the retry loop stops on the emptied contract
        instead - which is the same event, observed rather than predicted.
        """
        if chain != 'kas' or self.swap is None:
            return None
        if not self.swap.is_timestamp_locktime():
            return None
        return (self.swap.timelock + self.swap.REFUND_GRACE_MS) / 1000

    async def _refund_kas(self, on_event=None, interaction=None):
        """Wait out the kaspa locktime, then reclaim the contract."""
        await self._report(on_event, SwapStatus.EXPIRED)
        if not await self._kas_refundable(on_event=on_event):
            return self._settled('EXPIRED')
        output_address = await self.resolve_payout_address(
            self.swap, interaction, refund=True,
        )
        n_key = self.key_index(self.db_swap)

        async def spend():
            return await self.refund_kas(
                self.swap, n_key=n_key, output_address=output_address,
            )

        # No deadline: past its locktime the contract is ours alone, so there
        # is no window to miss and no reason to stop short of success.
        txid = await self.broadcast_with_retry(
            self.swap, 'kas', spend, refund=True,
            on_attempt=lambda n: self._report(
                on_event, SwapStatus.EXPIRED,
                error=f"Refund broadcast failed ({n}), retrying",
            ),
        )
        if not txid:
            logger.error('KAS refund never went out, leaving the swap live')
            return None
        logger.info(f"Refund transaction broadcasted, txid: {txid}")
        return await self._settle_broadcast(
            'REFUNDING', SwapStatus.REFUNDING, 'REFUNDED', 'kas',
            on_event, txid=txid, addresses=[output_address],
        )

    async def _refund_btc(self, on_event=None, interaction=None):
        """Wait out the bitcoin locktime, then reclaim the contract."""
        await self._report(on_event, SwapStatus.EXPIRED)
        while True:
            try:
                if await self.btc_locktime_expired(self.swap):
                    break
                blocks = await self.btc_blocks_remaining(self.swap)
            except Exception as e:
                logger.warning(f"btc refund tip probe failed: {e}")
                await asyncio.sleep(self.WATCH_TICK_S)
                continue
            await self._report(
                on_event, SwapStatus.MONITORING, blocks_remaining=blocks,
            )
            await asyncio.sleep(self.WATCH_TICK_S)
        # What _kas_refundable does for the other side: an empty contract is
        # nothing to reclaim, and refunding it anyway means building a
        # transaction with no inputs and a negative output. Reaching here
        # without funding is ordinary - watch_btc2kas expires whether or not
        # we ever got our bitcoin in.
        if not await self._btc_refundable():
            return self._settled('EXPIRED')
        output_address = await self.resolve_payout_address(
            self.swap, interaction, is_btc=True, refund=True,
        )
        n_key = self.key_index(self.db_swap)

        async def spend():
            return await self.refund_btc(
                self.swap, n_key=n_key, output_address=output_address,
            )

        # No deadline, as in _refund_kas: the contract is ours from its
        # locktime on.
        txid = await self.broadcast_with_retry(
            self.swap, 'btc', spend, refund=True,
            on_attempt=lambda n: self._report(
                on_event, SwapStatus.EXPIRED,
                error=f"Refund broadcast failed ({n}), retrying",
            ),
        )
        if not txid:
            logger.error('BTC refund never went out, leaving the swap live')
            return None
        logger.info(f"BTC refund transaction broadcasted, txid: {txid}")
        return await self._settle_broadcast(
            'REFUNDING', SwapStatus.REFUNDING, 'REFUNDED', 'btc',
            on_event, btc_txid=txid,
        )

    async def _kas_refundable(self, on_event=None):
        """Block until the kaspa contract can be reclaimed, if it can be.

        Two clocks: an LN swap's timelock is a timestamp with a grace period
        on top of it, an on-chain swap's is a daa score with none. An empty
        contract is nothing to reclaim, which is the maker having swept it.

        Emits each tick so a frontend countdown stays live through the grace
        window; check_refund_window only means something for timestamp
        locktimes, so the daa path reports remaining seconds itself.
        """
        swap = self.swap
        while True:
            try:
                if await self.kas_funded_amount(swap) <= 0:
                    return False
                if swap.is_timestamp_locktime():
                    # Grace-aware remaining: swap.time_remaining() hits zero at
                    # locktime, but the refund window opens REFUND_GRACE_MS later.
                    grace_left = int(
                        (swap.timelock + swap.REFUND_GRACE_MS) / 1000 - time.time()
                    )
                    if grace_left <= 0:
                        await self._report(on_event, SwapStatus.EXPIRED, time_remaining=0)
                        return True
                    await self._report(
                        on_event, SwapStatus.MONITORING, time_remaining=grace_left,
                    )
                else:
                    remaining = await self.kas_daa_remaining(swap)
                    await self._report(
                        on_event,
                        SwapStatus.EXPIRED if remaining <= 0 else SwapStatus.MONITORING,
                        daa_remaining=remaining,
                    )
                    if remaining <= 0:
                        return True
            except Exception as e:
                logger.warning(f"kas refund probe failed: {e}")
            await asyncio.sleep(self.WATCH_TICK_S)

    async def _btc_refundable(self):
        """Whether the bitcoin contract still holds anything to reclaim.

        Honor filter (non-dust, max 3), not btc_funded's sat_amount test: a
        refund sweeps what is there, so a partial fund is still worth taking
        back, where the amount test would answer False and leave the caller
        building a transaction with no inputs.

        An unreadable chain answers True. Not seeing the money is not evidence
        that there is none, and the broadcast path retries where this one
        settles, so guessing that way costs a retry loop instead of an expiry
        written over live funds.
        """
        swap = self.swap
        try:
            dust_limit, max_n = self._utxo_limits('btc')
            lock = int(swap.sat_amount or 0)
            outputs = await self.sm.bitcoin_service.check_utxos_for_address(
                swap.btc_contract_address,
                min_confirmations=0,
                include_unconfirmed=True,
                min_utxo_size=min(dust_limit, lock) if lock else dust_limit,
                max_utxo_count=max_n,
            )
        except Exception as e:
            logger.warning(f"btc refund funding probe failed: {e}")
            return True
        if not outputs:
            return False
        swap.btc_utxos = [
            {'txid': o.txid, 'vout': o.vout, 'amount': o.amount_sats / 1e8}
            for o in outputs
        ]
        return True

    async def query_price(self, swap_type, kas_amount=0, p2p_price=None, endpoint=None):
        if endpoint is None:
            endpoint = self.maker_endpoint
        price_response = await self.ping_maker('price', {'swap_type': swap_type, 'p2p_price': p2p_price}, endpoint)
        if not price_response['error']:
            valid_until = price_response['payload']['valid_until']
            if price_response['payload'].get('price'):
                price = price_response['payload']['price']
                res = price
            elif price_response['payload'].get('offers'):
                offers = price_response['payload']['offers']
                # filter valid offers if a kas amount is provided
                if kas_amount:
                    offers = {k: v for k, v in offers.items() if v[1] <= kas_amount <= v[2]}
                    # if no offers are left, return False
                    if not offers:
                        return False
                # we don't trust the maker, so we sort the offers and select lowest price
                # offer format is int_price: (float_price, min_amt, max_amt)
                best_offer_key = sorted(offers,
                                        key=lambda x: offers[x][0],
                                        reverse=(swap_type in ('kas2sat', 'kas2btc'))
                                        )[0]
                best_offer = offers[best_offer_key]
                price = best_offer[0]
                if kas_amount and not (best_offer[1] <= kas_amount <= best_offer[2]):
                    return False
                if p2p_price is None:
                    res = offers
                else:
                    res = price
            else:
                # we should not hit this -> (time passes) -> actually, we can hit this if the maker has no offers
                return False
            logger.debug(f"Maker price is {price}")
        else:
            logger.error(f"Error getting quote from maker: {price_response['error']}")
            return False

        return res, valid_until

    async def query_quote(self, swap_type, kas_amount=None, sat_amount=None, endpoint=None):
        if (kas_amount is None) == (sat_amount is None):
            return None
        payload = {
            'swap_type': swap_type,
            'kas_amount': kas_amount,
            'sat_amount': sat_amount,
        }
        resp = await self.ping_maker('quote', payload, endpoint)
        if resp.get('error'):
            logger.error(f"Error getting quote from maker: {resp['error']}")
            return None
        quoted = resp.get('payload') or {}
        if quoted.get('type') != 'swap_quote':
            return None
        return quoted

    @staticmethod
    def _endpoint_pin_key(endpoint):
        if not endpoint:
            return ''
        key = endpoint.strip().rstrip('/')
        for prefix in ('https://', 'http://'):
            if key.lower().startswith(prefix):
                key = key[len(prefix):]
                break
        return key.lower()

    def _check_and_pin_maker(self, endpoint, data):
        """After a valid signature: pin pubkey to this endpoint, or require a match.

        Returns None if the pin holds (or is first sight), else an error string.
        """
        pin_key = self._endpoint_pin_key(endpoint)
        remote = (data.get('pubkey') or '').lower()
        if not pin_key or not remote:
            return 'Missing counterparty pubkey'
        pinned = self._maker_pubkeys.get(pin_key)
        if pinned is None:
            self._maker_pubkeys[pin_key] = remote
            return None
        if pinned != remote:
            logger.error(
                f"Maker pubkey changed for {pin_key}: "
                f"pinned {pinned[:8]}... got {remote[:8]}..."
            )
            return 'Counterparty identity mismatch'
        return None

    async def ping_maker(self, msg_type, payload, endpoint=None):
        if endpoint is None:
            endpoint = self.maker_endpoint
        # msg_type is a string
        # payload is a dict
        signature = self.sign_message(msg_type, payload, node_key=True)
        req_msg = {
            'type': msg_type,
            'payload': payload,
            'pubkey': self.node_pubkey.hex(),
            'signature': signature.hex()
        }
        logger.debug(req_msg)
        if endpoint.endswith('onion'):
            connector = ProxyConnector.from_url('socks5://127.0.0.1:9050', rdns=True)
            if not endpoint.startswith('http'):
                endpoint = f"http://{endpoint}"
        else:
            # enforce https for clearnet endpoint
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            if not endpoint.startswith('https'):
                endpoint = f"https://{endpoint}"
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(endpoint, data=json.dumps(req_msg).encode()) as res:
                response = await res.text()
                logger.debug(f"Got response: {response}")
                try:
                    data = json.loads(response)
                except Exception as e:
                    logger.error(e, exc_info=True)
                    data = {'error': 'something went wrong'}
                if data.get('error'):
                    return data
                try:
                    sig_ok = self.verify_signature(data)
                except (KeyError, ValueError, TypeError) as e:
                    logger.error(f"Malformed signed response: {e}")
                    sig_ok = False
                if not sig_ok:
                    data['error'] = 'Signature verification failed'
                    return data
                pin_error = self._check_and_pin_maker(endpoint, data)
                if pin_error:
                    data['error'] = pin_error
                return data

    @staticmethod
    def db_load_swaps(limit=10):
        swaps = Swap.select().order_by(Swap.created_at.desc()).limit(limit)
        return list(swaps)

    async def db_load_swap(self, row):
        swap = await self.hydrate_swap_from_row(row)
        if swap is None:
            return False
        self.db_swap = row
        self.swap = swap
        return True

    def db_set_swap_status(self, status, finalize_swap=True):
        if not self.set_swap_status(self.db_swap, status):
            return False
        if finalize_swap:
            self.db_swap = None
        return True

    def db_set_swap_txid(self, txid):
        if self.db_swap is None:
            return False
        self.db_swap.txid = txid
        self.db_swap.save()
        return True


async def main():
    taker = Taker(
        output_address='kaspa:qr2y4cg72p09fhpwfs3dxudwz5duxlx774ejwvwgvr9yf5p4a8edzdrt50e8q',
        interaction=CliInteraction(),
    )
    await taker.run_sat2kas(1, on_event=lambda e: logger.info(e))
    # await taker.run_kas2sat(1, on_event=lambda e: logger.info(e))

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
