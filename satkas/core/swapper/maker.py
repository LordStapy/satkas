
import asyncio
import os
import time
import json
import math
import logging

from aiohttp import web
from stem.control import Controller

from satkas.core.db.models import MakerWallet, Swap
from satkas.core.services.base_service import PaymentStatus
from satkas.core.swapper.counterparty import Counterparty
from satkas.core.blib.btx_size import BTC_FUNDING_VSIZE, BTC_SPEND_VSIZE
from satkas.core.klib.ktx_mass import (
    estimate_funding_mass, estimate_spend_mass,
    P2SH_REDEEM_SIGSCRIPT_LN, P2SH_REDEEM_SIGSCRIPT_DAA,
)
from satkas.core.swapper.atomic_swap import AtomicSwap
from satkas.core.swapper.swap_errors import InvoiceInvalid


logger = logging.getLogger('maker')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)-8s - %(name)-16s - %(message)s'
)

class Maker(Counterparty):
    SIDE = 'maker'

    def __init__(self,
                 output_address=None,
                 btc_output_address=None,
                 auto_pick_address=False,
                 wallet_index=1,
                 wallet_passwd=None,
                 lport=38080,
                 apiport=None,
                 swap_endpoint=None,
                 service_manager=None,
                 plugins=None):
        super().__init__(wallet_db_table=MakerWallet,
                         keep_unlocked=True,
                         wallet_index=wallet_index,
                         wallet_passwd=wallet_passwd,
                         swap_endpoint=swap_endpoint,
                         service_manager=service_manager,
                         plugins=plugins)
        self.server = None
        self.runner = None
        self.site = None
        self.lport = lport
        self.apiserver = None
        self.apirunner = None
        self.apisite = None
        self.apiport = apiport
        self.tor_controller = None
        self.hidden_service = None

        self.output_address = output_address if output_address is not None else self.address
        self.btc_output_address = (
            btc_output_address if btc_output_address is not None
            else self.btc_address.to_string()
        )
        self.auto_pick_address = auto_pick_address
        self._kas_payout_fixed = output_address is not None
        self._btc_payout_fixed = btc_output_address is not None

        self.price_offers = {
            'sat2kas': {},
            'kas2sat': {},
            'btc2kas': {},
            'kas2btc': {}
        }
        self.locked_offers = {}
        self.valid_until = int(os.getenv('OFFER_VALIDITY_SECONDS', 60))

        # swaps format: {'contract_address': swap_object}
        self.swaps = {
            'sat2kas': {},
            'kas2sat': {},
            'btc2kas': {},
            'kas2btc': {}
        }

        self.refresh_delay = int(os.getenv('MAKER_REFRESH_DELAY', 5))
        self._settlements_resumed = False

    async def start(self, init_server=True):
        if init_server:
            await self.init_server()
        logger.info('Maker server initialized')
        if not self._settlements_resumed:
            self.resume_pending_settlements()
            self._settlements_resumed = True
        try:
            for row, swap in await self.resume_live_swaps():
                self.swaps[row.swap_type][swap.contract_address] = swap
        except Exception as e:
            logger.error(e, exc_info=True)
        try:
            while True:
                try:
                    await self.update_offers()
                except Exception as e:
                    logger.error(e, exc_info=True)
                try:
                    self.clear_expired_locked_offers()
                except Exception as e:
                    logger.error(e, exc_info=True)
                try:
                    await self.monitor_swaps()
                except Exception as e:
                    logger.error(e, exc_info=True)
                await asyncio.sleep(self.refresh_delay)
        finally:
            if self.hidden_service:
                self.remove_hidden_service()

    def _maker_settle(self, swap_type, swap, db_swap, pending_status, final_status,
                      chain, txid=None, btc_txid=None, addresses=None):
        """COMPLETING/REFUNDING + background promote; drop in-memory swap.

        addresses: optional kas payout address(es) for UTXO/DAA confirm.
        Unused for btc (txid-only via bitcoin_service).
        """
        watch = txid if chain == 'kas' else btc_txid
        self.set_swap_status(db_swap, pending_status, txid=txid, btc_txid=btc_txid)
        self.start_settlement(
            db_swap.id, chain, watch, final_status, addresses=addresses,
        )
        self.swaps[swap_type].pop(swap.contract_address, None)
        logger.info(f"[{swap_type}] {pending_status} ({watch}), waiting for confirmations")

    async def readmit_swap(self, row):
        """Put a demoted row back on the monitor tick.

        _maker_settle drops the swap the moment it broadcasts, so a
        settlement that later turns out never to have landed has nothing
        watching it. Without this the row would wait for a restart.
        """
        swap = await self.hydrate_swap_from_row(row)
        if swap is None:
            logger.error(f"Cannot readmit swap {row.id} ({row.swap_type})")
            return
        self.swaps[row.swap_type][swap.contract_address] = swap
        logger.info(f"Readmitted {row.swap_type} swap {row.id} to the monitors")

    # --- Taking a contract ---
    #
    # Every redeem and every refund the maker makes goes through the one step
    # below. It is written to be called again: the monitor tick is the maker's
    # retry loop, where the taker has broadcast_with_retry. Blocking inside a
    # monitor would stall every other swap in the same round, so a spend that
    # does not go out simply gets another attempt on the next pass, for as
    # long as the contract still holds the money.

    # After this many failed attempts, and every this many after that, the
    # transaction also goes out through the public explorer.
    FALLBACK_EVERY = 5

    async def _spend_step(self, swap_type, swap, db_swap, chain, secret=None):
        """One attempt at taking a contract, and the verdict once it is empty.

        With a secret this is a redeem, without one a refund. Returns True
        when the swap is done with - settled, or written off - and False when
        it wants another attempt on a later tick.
        """
        if self._probed(swap, 'spend_retry') is not self._NOT_PROBED:
            return False
        attempts = (getattr(swap, 'spend_attempts', 0) or 0) + 1
        swap.spend_attempts = attempts
        self._remember(swap, 'spend_retry', attempts)
        n_key = self.key_index(db_swap)
        refund = secret is None

        try:
            if chain == 'btc':
                payout = swap.btc_output_address or self.btc_output_address
                if refund:
                    txid = await self.refund_btc(swap, n_key=n_key, output_address=payout)
                else:
                    txid = await self.redeem_btc(
                        swap, secret, n_key=n_key, output_address=payout,
                    )
            elif refund:
                txid = await self.refund_kas(swap, n_key=n_key)
            else:
                txid = await self.redeem_kas(swap, secret, n_key=n_key)
        except Exception as e:
            txid = None
            logger.error(
                f"[{swap_type}] {chain} {'refund' if refund else 'redeem'} "
                f"attempt {attempts} failed: {e}", exc_info=True,
            )

        if not txid and attempts % self.FALLBACK_EVERY == 0:
            # Last resort, and deliberately not over Tor. We are here because
            # our own node has refused this transaction several times running,
            # and at that point losing the contract outranks the privacy of
            # the broadcast - an exit that is blocked or slow is the same
            # failure this path exists to route around. An operator who wants
            # both points KAS_EXPLORER_BASE_URL / MEMPOOL_BASE_URL at their
            # own explorer.
            logger.warning(
                f"[{swap_type}] {chain} spend has failed {attempts} times, "
                f"broadcasting through the public explorer over clearnet"
            )
            txid = await self._broadcast_via_fallback(swap, chain)

        if txid:
            pending, final = (
                ('REFUNDING', 'REFUNDED') if refund else ('COMPLETING', 'COMPLETED')
            )
            if chain == 'btc':
                swap.btc_transaction = txid
                self._maker_settle(
                    swap_type, swap, db_swap, pending, final, 'btc', btc_txid=txid,
                )
            else:
                dest = swap.output_address or (
                    swap.sender_address if refund else swap.receiver_address
                )
                self._maker_settle(
                    swap_type, swap, db_swap, pending, final, 'kas', txid=txid,
                    addresses=[dest] if dest else None,
                )
            return True

        if refund or await self._still_spendable(swap, chain):
            return False

        # The contract is empty and no attempt of ours was ever acknowledged.
        # Who emptied it is readable from the branch they spent: the redeem
        # branch needs our key and the secret, so a redeem-shaped spend can
        # only be ours - a broadcast that landed while its answer went
        # missing. A refund-shaped one is the counterparty's.
        spend = await (
            self.extract_btc_htlc_secret(swap) if chain == 'btc'
            else self.extract_kas_htlc_secret(swap)
        )
        if isinstance(spend, bytes):
            logger.warning(
                f"[{swap_type}] our {chain} redeem landed after all, with no txid to watch"
            )
            self.set_swap_status(db_swap, 'COMPLETED')
            self.swaps[swap_type].pop(swap.contract_address, None)
            return True
        if spend is True:
            # The locktime asymmetry is meant to make this unreachable: the
            # maker always acts first. Reaching it means the spend was never
            # broadcastable - see the fee and key-index findings.
            logger.error(
                f"[{swap_type}] the counterparty reclaimed the {chain} contract "
                f"before our redeem went out, swap {db_swap.id} is a loss"
            )
            self.set_swap_status(db_swap, 'FAILED')
            self.swaps[swap_type].pop(swap.contract_address, None)
            return True
        logger.warning(
            f"[{swap_type}] the {chain} contract is empty and the spend is unreadable, "
            f"retrying"
        )
        return False

    async def init_server(self, start_hidden_service=True):
        self.server = web.Server(self.post_handler)
        self.runner = web.ServerRunner(self.server)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, '0.0.0.0', self.lport)
        await self.site.start()

        if self.apiport is not None:
            self.apiserver = web.Server(self.api_handler)
            self.apirunner = web.ServerRunner(self.apiserver)
            await self.apirunner.setup()
            self.apisite = web.TCPSite(self.apirunner, '127.0.0.1', self.apiport)
            await self.apisite.start()
        if start_hidden_service:
            self.init_hidden_service()

    def init_hidden_service(self, extra_port=None):
        key_path = os.getenv('HIDDEN_SERVICE_KEY_PATH', os.path.join(os.getcwd(), 'sk_hidden_service_key'))
        self.tor_controller = Controller.from_port()
        self.tor_controller.authenticate()
        hidden_service_ports = {80: self.lport}
        if extra_port:
            hidden_service_ports[extra_port[0]] = extra_port[1]
        if not os.path.exists(key_path):
            service = self.tor_controller.create_ephemeral_hidden_service(
                hidden_service_ports,
                await_publication=True
            )
            with open(key_path, 'w') as key_file:
                key_file.write(f"{service.private_key_type}:{service.private_key}")
        else:
            with open(key_path) as key_file:
                key_type, key_content = key_file.read().split(':', 1)
            logger.info(f"Starting hidden service with key type: {key_type}")
            service = self.tor_controller.create_ephemeral_hidden_service(
                hidden_service_ports,
                key_type=key_type,
                key_content=key_content,
                await_publication=True,
                detached=False
            )
        self.hidden_service = f"{service.service_id}.onion"
        logger.info(f"Started hidden service with address {self.hidden_service}")

    def remove_hidden_service(self):
        self.tor_controller.remove_ephemeral_hidden_service(self.hidden_service[:-6])

    async def api_handler(self, request):
        content = await request.content.read()
        path = request.path
        logger.debug(f"{path}: {content}")
        if path == '/update_offers':
            offers = json.loads(content)
            logger.info(offers)
            for kind in ('sat2kas', 'kas2sat', 'btc2kas', 'kas2btc'):
                book = offers.get(kind, {})
                self.price_offers[kind] = {int(k): v for k, v in book.items()}
            return web.json_response(self.price_offers)
        return web.json_response({'error': 'Something went wrong'})

    async def post_handler(self, request):
        content = await request.content.read()
        try:
            data = json.loads(content)
            logger.info(f"Received data: {data}")
        except json.JSONDecodeError:
            logger.error(f"Error decoding request: {request} Content: {content}")
            return web.Response(text='Nope')

        if not self.verify_signature(data):
            return web.Response(text='Signature verification failed')

        msg_type = data['type']
        msg_payload = data['payload']
        remote_pubkey = data['pubkey']

        match msg_type:
            case 'price':
                response_payload = await self.handle_price_req(msg_payload, remote_pubkey)
                if not response_payload:
                    return web.json_response({'error': 'Wrong swap_type'})
            case 'quote':
                response_payload = await self.handle_quote_req(msg_payload, remote_pubkey)
                if not response_payload:
                    return web.json_response({'error': 'Failed quote'})
            case 'init_swap':
                response_payload = await self.handle_init_swap(msg_payload, remote_pubkey)
                if not response_payload:
                    return web.json_response({'error': 'Failed swap init'})
            case _:
                return web.json_response({'error': 'Method not implemented'})

        response_signature = self.sign_message(msg_type, response_payload, node_key=True)
        response = {
            'type': msg_type,
            'payload': response_payload,
            'pubkey': self.node_pubkey.hex(),
            'signature': response_signature.hex(),
            'error': None
        }
        return web.json_response(response)

    async def handle_price_req(self, msg_payload, remote_pubkey):
        swap_type = msg_payload.get('swap_type')
        if swap_type not in self.price_offers:
            return False
        self._ensure_locked_slot(remote_pubkey, swap_type)
        quote = self._build_price_quote(swap_type, msg_payload)
        if quote is None:
            return False
        fields, offers = quote
        response_payload = {'type': 'swap_price', **fields}
        valid_until = int(time.time()) + self.valid_until
        if not self._lock_offers(remote_pubkey, swap_type, offers, valid_until):
            return False
        response_payload['valid_until'] = valid_until
        return response_payload

    async def handle_quote_req(self, msg_payload, remote_pubkey):
        return await self.quote(
            msg_payload.get('swap_type'),
            kas_amount=msg_payload.get('kas_amount'),
            sat_amount=msg_payload.get('sat_amount'),
            remote_pubkey=remote_pubkey,
        )

    async def quote(self, swap_type, kas_amount=None, sat_amount=None, remote_pubkey=None):
        if swap_type not in self.price_offers:
            return None
        if (kas_amount is None) == (sat_amount is None):
            return None

        taker_sends_kas = swap_type in ('kas2sat', 'kas2btc')
        given_kas = kas_amount is not None

        btc_spend_sats = 0
        btc_fund = 0
        if self.sm is not None:
            btc_spend_sats, _ = await self.sm.bitcoin_service.estimate_send_fee(BTC_SPEND_VSIZE)
            btc_fund, _ = await self.sm.bitcoin_service.estimate_send_fee(BTC_FUNDING_VSIZE)
        if not btc_spend_sats:
            btc_spend_sats = BTC_SPEND_VSIZE  # 1 sat/vB
        if not btc_fund:
            btc_fund = BTC_FUNDING_VSIZE
        btc_spend = btc_spend_sats

        book = self.price_offers[swap_type]
        candidates = []
        for key, offer in book.items():
            price, min_kas, max_kas = offer
            if given_kas:
                implied_kas = kas_amount
            else:
                implied_kas = sat_amount / price
            if min_kas <= implied_kas <= max_kas:
                candidates.append((key, price, min_kas, max_kas))
        if not candidates:
            return None

        # Best price: highest if taker sells KAS, lowest if taker buys KAS.
        reverse = taker_sends_kas
        key, price, min_kas, max_kas = sorted(
            candidates, key=lambda c: c[1], reverse=reverse
        )[0]

        implied_kas = kas_amount if given_kas else sat_amount / price
        lock_sompi = max(int(implied_kas * 1e8), 1)
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
        if not kas_spend_sompi:
            kas_spend_sompi = 100 * spend_mass
        if not kas_fund_sompi:
            kas_fund_sompi = 100 * fund_mass
        kas_spend = kas_spend_sompi / 1e8
        kas_fund = kas_fund_sompi / 1e8

        kas_lock = None
        sat_lock = None
        ln_fee = 0
        fee = {
            'kas_spend': kas_spend, 'btc_spend': 0, 'ln_fee': 0,
            'kas_fund': 0, 'btc_fund': 0,
        }

        if swap_type == 'kas2sat' and given_kas:
            # Taker sends KAS gross. Invoice shaved by kas spend (maker redeem)
            # and LN routing (maker pays).
            kas_lock = kas_amount
            kas_net = kas_lock - kas_spend
            if kas_net <= 0:
                return None
            sat_value = math.floor(kas_net * price)
            ln_fee = 0
            if self.sm is not None:
                ln_fee = await self.sm.ln_wallet_service.estimate_route_fee(sat_amount=sat_value)
            sat_lock = sat_value - ln_fee
            fee['ln_fee'] = ln_fee

        elif swap_type == 'kas2sat' and not given_kas:
            # Taker receives sats net (invoice). Maker pays invoice + routing;
            # requests KAS so redeem nets enough.
            sat_lock = sat_amount
            ln_fee = 0
            if self.sm is not None:
                ln_fee = await self.sm.ln_wallet_service.estimate_route_fee(sat_amount=sat_lock)
            sat_cost = sat_lock + ln_fee
            kas_net = sat_cost / price
            kas_lock = math.ceil((kas_net + kas_spend) * 1e8) / 1e8
            fee['ln_fee'] = ln_fee

        elif swap_type == 'sat2kas' and not given_kas:
            # Taker sends sats gross (pays this invoice; their routing is extra).
            # Book KAS minus maker wallet fee to fund the contract; taker
            # redeem then nets kas_lock - kas_spend.
            sat_lock = sat_amount
            kas_lock = math.floor(sat_lock / price * 1e8) / 1e8 - kas_fund
            fee['kas_fund'] = kas_fund

        elif swap_type == 'sat2kas' and given_kas:
            # Taker receives KAS net. Maker locks extra for redeem and pays
            # kas_fund to send it; invoice charged on lock + fund fee.
            kas_lock = kas_amount + kas_spend
            sat_lock = math.ceil((kas_lock + kas_fund) * price)
            ln_fee = 0
            if self.sm is not None:
                ln_fee = await self.sm.ln_wallet_service.estimate_route_fee(sat_amount=sat_lock)
            sat_lock = sat_lock + ln_fee
            fee['ln_fee'] = ln_fee
            fee['kas_fund'] = kas_fund

        elif swap_type == 'btc2kas' and not given_kas:
            # Taker sends BTC gross. Maker BTC net = lock - btc_spend.
            # KAS sized on that net, minus kas_fund (maker funds KAS).
            sat_lock = sat_amount
            sat_net = sat_lock - btc_spend
            if sat_net <= 0:
                return None
            kas_lock = math.floor(sat_net / price * 1e8) / 1e8 - kas_fund
            fee['btc_spend'] = btc_spend
            fee['kas_fund'] = kas_fund

        elif swap_type == 'btc2kas' and given_kas:
            # Taker receives KAS net. Maker locks kas_net + kas_spend.
            # Sats cover lock + kas_fund, plus btc_spend so maker's BTC net holds.
            kas_lock = kas_amount + kas_spend
            sat_value = math.ceil((kas_lock + kas_fund) * price)
            sat_lock = sat_value + btc_spend
            fee['btc_spend'] = btc_spend
            fee['kas_fund'] = kas_fund

        elif swap_type == 'kas2btc' and given_kas:
            # Taker sends KAS gross. Sats priced on maker's kas net, then
            # minus btc_fund (maker wallet send into P2WSH). Taker nets
            # sat_lock - btc_spend. Maker total BTC out = sat_lock + btc_fund.
            kas_lock = kas_amount
            kas_net = kas_lock - kas_spend
            if kas_net <= 0:
                return None
            sat_value = math.floor(kas_net * price)
            sat_lock = sat_value - btc_fund
            fee['btc_spend'] = btc_spend
            fee['btc_fund'] = btc_fund

        elif swap_type == 'kas2btc' and not given_kas:
            # Taker receives BTC net. Maker locks sat_net + btc_spend and pays
            # btc_fund to send it. KAS covers lock + fund + kas redeem.
            sat_lock = sat_amount + btc_spend
            kas_net = (sat_lock + btc_fund) / price
            kas_lock = math.ceil((kas_net + kas_spend) * 1e8) / 1e8
            fee['btc_spend'] = btc_spend
            fee['btc_fund'] = btc_fund

        else:
            return None

        if kas_lock <= 0 or sat_lock <= 0:
            return None

        implied_kas = kas_amount if given_kas else sat_amount / price
        valid_until = int(time.time()) + self.valid_until
        if remote_pubkey is not None:
            self._ensure_locked_slot(remote_pubkey, swap_type)
            self._lock_offers(
                remote_pubkey, swap_type,
                [(price, min_kas, max_kas)],
                valid_until,
            )
            self.locked_offers[remote_pubkey][swap_type]['quote'] = {
                'kas_amount': kas_lock,
                'sat_amount': sat_lock,
                'price': price,
                'p2p_price': key,
                'fee': fee,
                'implied_kas': implied_kas,
                'valid_until': valid_until,
            }

        return {
            'type': 'swap_quote',
            'kas_amount': kas_lock,
            'sat_amount': sat_lock,
            'price': price,
            'p2p_price': key,
            'fee': fee,
            'implied_kas': implied_kas,
            'valid_until': valid_until,
        }

    @staticmethod
    def _empty_locked_type():
        return {'offers': [], 'valid_until': 0, 'quote': None}

    @staticmethod
    def _empty_locked_bucket():
        return {
            t: Maker._empty_locked_type()
            for t in ('sat2kas', 'kas2sat', 'btc2kas', 'kas2btc')
        }

    def _ensure_locked_slot(self, remote_pubkey, swap_type):
        if not self.locked_offers.get(remote_pubkey):
            self.locked_offers[remote_pubkey] = self._empty_locked_bucket()
        elif self.locked_offers.get(remote_pubkey, {}).get(swap_type, {}).get('offers', []):
            self.locked_offers[remote_pubkey][swap_type] = self._empty_locked_type()

    def _lock_offers(self, remote_pubkey, swap_type, offers, valid_until):
        if isinstance(offers, dict):
            locked = [v for v in offers.values()]
        elif isinstance(offers, list):
            locked = offers
        else:
            return False
        slot = self.locked_offers[remote_pubkey][swap_type]
        slot['offers'] = locked
        slot['valid_until'] = valid_until
        slot['quote'] = None
        return True

    def _get_locked_quote(self, remote_pubkey, swap_type):
        """Unexpired slot['quote'], or None. Mirrors get_locked_offers expiry."""
        try:
            entry = self.locked_offers[remote_pubkey][swap_type]
        except KeyError:
            return None
        if (entry.get('valid_until') or 0) <= int(time.time()):
            self.locked_offers[remote_pubkey][swap_type] = self._empty_locked_type()
            return None
        return entry.get('quote')

    def _match_amount_offer(self, swap_type, amount):
        """Match an offer by msg 'amount'. Returns (fields, offers_list)."""
        logger.debug('Loading offers')
        for offer in self.price_offers[swap_type].values():
            logger.debug(f"{offer}")
            _price, _min, _max = offer
            if swap_type in ('sat2kas', 'btc2kas'):
                kas_amount = int(int(amount) / _price)
                logger.debug(f"{kas_amount}")
                if _min <= kas_amount <= _max:
                    logger.debug('Offer found')
                    return {'amount': kas_amount, 'price': _price}, [offer]
            elif swap_type == 'kas2sat':
                sat_amount = math.floor(amount * _price)
                logger.debug(f"{sat_amount}")
                if _min <= int(amount) <= _max:
                    logger.debug('Offer found')
                    return {'amount': sat_amount, 'price': _price}, [offer]
            elif swap_type == 'kas2btc':
                sat_amount = int(int(amount) * _price)
                logger.debug(f"{sat_amount}")
                if _min <= amount <= _max:
                    logger.debug('Offer found')
                    return {'amount': amount, 'price': _price}, [offer]
        return {'amount': 0}, []

    def _match_sat_amount_offer(self, swap_type, sat_amount):
        """Match an offer by msg 'sat_amount' (kas2sat / kas2btc). Returns (fields, offers_list)."""
        for offer in self.price_offers[swap_type].values():
            _price, _min, _max = offer
            kas_amount = round(sat_amount / _price, 3)
            if _min <= kas_amount <= _max:
                logger.debug('Offer found')
                return {'kas_amount': kas_amount, 'price': _price}, [offer]
        return {'kas_amount': 0}, []

    def _build_price_quote(self, swap_type, msg_payload):
        """Build response fields and the offers structure used for locking.

        Returns (fields, offers) where:
        - full book: fields includes 'offers', offers is the book dict
        - p2p/amount/sat_amount: fields has price/amount/kas_amount; offers is a list for locking
          (not copied into the response except via fields when full book)

        p2p_price miss: fields {'price': None}, offers [] (not full book, not [None]).
        """
        if swap_type not in self.price_offers:
            return None

        if p2p_price := msg_payload.get('p2p_price'):
            offer = self.price_offers[swap_type].get(p2p_price)
            if offer is None:
                return {'price': None}, []
            return {'price': offer}, [offer]

        if amount := msg_payload.get('amount'):
            return self._match_amount_offer(swap_type, amount)

        if swap_type in ('kas2sat', 'kas2btc') and (sat_amount := msg_payload.get('sat_amount')):
            return self._match_sat_amount_offer(swap_type, sat_amount)

        offers = self.price_offers[swap_type]
        return {'offers': offers}, offers

    async def handle_init_swap(self, msg_payload, remote_node_pubkey):
        db_swap = Swap.create(side='maker')
        n_key = self.key_index(db_swap)
        address = self.get_next_address(n_key=n_key)
        btc_address = self.get_next_address(n_key=n_key, is_btc=True)
        self.update_address_counter(new_counter=n_key)

        logger.debug(f"Init swap from {remote_node_pubkey}, payload: {msg_payload}")

        built = await self._build_init_swap(msg_payload, remote_node_pubkey, address, btc_address)
        if built is None:
            return False
        swap_type, swap_kwargs, response_payload, kas_amount = built
        bucket = self.locked_offers.get(remote_node_pubkey)
        if bucket is not None:
            bucket[swap_type] = self._empty_locked_type()

        logger.debug(swap_kwargs)
        swap = AtomicSwap(service_manager=self.sm, **swap_kwargs)

        if swap_type in ['sat2kas', 'kas2sat']:
            try:
                decode_out = await self.decode_ln_invoice(
                    swap.invoice,
                    swap=swap,
                    avoid_self_pay=(swap_type == 'kas2sat')
                )
            except InvoiceInvalid as e:
                logger.error(e)
                return False
            logger.debug(decode_out)
            if swap.timelock / 1000 < time.time():
                # invoice is already expired, abort swap
                return False
        logger.info('Calculating P2SH address')
        swap.gen_contract_address()
        logger.info('P2SH calculated')
        if swap_type in ['btc2kas', 'kas2btc']:
            await swap.gen_btc_contract_address()
            logger.info(f"P2WSH [BTC] calculated ({swap.btc_contract_address})")

        self.swaps[swap_type][swap.contract_address] = swap

        db_swap.swap_type = swap_type
        db_swap.side = 'maker'
        db_swap.remote_pubkey = remote_node_pubkey
        db_swap.ln_invoice = swap.invoice
        db_swap.payment_hash = swap.secret_hash.hex()
        db_swap.sender_address = swap.sender_address
        db_swap.receiver_address = swap.receiver_address
        db_swap.contract = swap.contract_script.hex()
        db_swap.p2sh_address = swap.contract_address
        db_swap.dwork_amount = int(swap.kas_amount * 1e8)
        # on-chain stuff
        db_swap.secret_hash = swap.secret_hash.hex() if swap.secret_hash else None
        db_swap.btc_sender_address = swap.btc_sender_address.to_string() if swap.btc_sender_address else None
        db_swap.btc_receiver_address = swap.btc_receiver_address.to_string() if swap.btc_receiver_address else None
        db_swap.btc_contract = swap.btc_contract_script.to_hex() if swap.btc_contract_script else None
        db_swap.btc_p2sh_address = swap.btc_contract_address
        db_swap.sat_amount = swap.sat_amount
        db_swap.kas_locktime = swap.kas_locktime or swap.timelock or None
        db_swap.btc_locktime = swap.btc_locktime
        db_swap.dag_checkpoint_hash = await self.current_dag_checkpoint()
        swap.vchain_checkpoint = db_swap.dag_checkpoint_hash
        db_swap.output_address = swap.output_address or None
        db_swap.btc_output_address = swap.btc_output_address or None

        self.set_swap_status(db_swap, 'OPENED')

        response_payload['p2sh_address'] = swap.contract_address
        response_payload['contract'] = swap.contract_script.hex()  # <- can we remove this?
        if swap_type in ['btc2kas', 'kas2btc']:
            response_payload['btc_p2sh_address'] = swap.btc_contract_address

        # sat2kas funding happens in monitor_sat2kas (once, gated on the row).
        if swap_type == 'kas2sat':
            swap.kas_amount = kas_amount

        return response_payload

    def _validate_offer(self, remote_pubkey, swap_type, swap_price, implied_kas):
        """Match price and implied KAS (named size, not kas_lock) to a locked or book offer."""
        if implied_kas is None or implied_kas <= 0:
            return False
        maker_sells_kas = swap_type in ('sat2kas', 'btc2kas')
        locked_offers = self.get_locked_offers(remote_pubkey, swap_type)
        logger.debug(f"Locked offers: {self.locked_offers}")
        for locked_offer in locked_offers:
            price, min_amt, max_amt = locked_offer
            price_ok = swap_price >= price if maker_sells_kas else swap_price <= price
            if price_ok and min_amt <= implied_kas <= max_amt:
                return True
        p2p_price = math.ceil(swap_price) if maker_sells_kas else math.floor(swap_price)
        offer = self.price_offers[swap_type].get(p2p_price)
        if not offer:
            return False
        price_ok = swap_price >= offer[0] if maker_sells_kas else swap_price <= offer[0]
        return price_ok and offer[1] <= implied_kas <= offer[2]

    async def _resolve_init_amounts(self, swap_type, payload, remote_pubkey,
                                    invoice_sats=None):
        taker_kas = payload.get('kas_amount')
        taker_sat = payload.get('sat_amount')
        if taker_sat is None and invoice_sats is not None:
            taker_sat = invoice_sats
        payload_price = payload.get('price')

        locked = self._get_locked_quote(remote_pubkey, swap_type)
        if locked:
            kas_lock, sat_lock = locked['kas_amount'], locked['sat_amount']
            if taker_kas is not None and abs(taker_kas - kas_lock) > 1e-8:
                return None
            if taker_sat is not None and abs(taker_sat - sat_lock) > 1:
                return None
            return kas_lock, sat_lock, locked['price'], locked.get('implied_kas')

        taker_sends_kas = swap_type in ('kas2sat', 'kas2btc')
        both = taker_kas is not None and taker_sat is not None
        if taker_sends_kas:
            q = await self.quote(
                swap_type,
                kas_amount=taker_kas if taker_kas is not None else None,
                sat_amount=None if taker_kas is not None else taker_sat,
                remote_pubkey=None,
            )
        else:
            q = await self.quote(
                swap_type,
                kas_amount=None if taker_sat is not None else taker_kas,
                sat_amount=taker_sat if taker_sat is not None else None,
                remote_pubkey=None,
            )
        if not q:
            return None
        q_kas, q_sat, q_price = q['kas_amount'], q['sat_amount'], q['price']
        implied_kas = q.get('implied_kas')

        if not both:
            return q_kas, q_sat, q_price, implied_kas

        # Taker guessed a pair: must be at least as good for the maker as quote().
        if taker_sends_kas:
            if taker_kas + 1e-8 < q_kas or taker_sat > q_sat + 1:
                return None
        else:
            if taker_sat + 1 < q_sat or taker_kas > q_kas + 1e-8:
                return None
        return taker_kas, taker_sat, payload_price or q_price, implied_kas

    async def _build_init_swap(self, msg_payload, remote_node_pubkey, address, btc_address):
        """Build swap_type, AtomicSwap kwargs, response payload, and kas_amount.

        Returns (swap_type, swap_kwargs, response_payload, kas_amount), or None on reject.
        """
        swap_type = msg_payload.get('swap_type', None)
        
        kas_out = self.output_address if self._kas_payout_fixed else address
        btc_out = (
            self.btc_output_address if self._btc_payout_fixed
            else btc_address.to_string()
        )
        if self.auto_pick_address and self.sm:
            if not self._kas_payout_fixed and not self.sm.kas_wallet_is_external():
                try:
                    kas_out = await self.sm.kaspa_wallet_service.get_new_address() or kas_out
                except Exception:
                    pass
            if (
                swap_type in ('btc2kas', 'kas2btc')
                and not self._btc_payout_fixed
                and not self.sm.btc_wallet_is_external()
            ):
                try:
                    btc_out = await self.sm.btc_wallet_service.get_new_address() or btc_out
                except Exception:
                    pass

        if swap_type == 'sat2kas':
            swap_type = 'sat2kas'
            amounts = await self._resolve_init_amounts(
                'sat2kas', msg_payload, remote_node_pubkey,
            )
            if amounts is None:
                return None
            kas_lock, sat_lock, swap_price, implied_kas = amounts
            if not self._validate_offer(remote_node_pubkey, 'sat2kas', swap_price, implied_kas):
                return None
            receiver_address = msg_payload['receiver_address']
            ln_invoice = await self.sm.ln_wallet_service.create_invoice(int(sat_lock))
            swap_kwargs = {
                'invoice': ln_invoice,
                'receiver_address': receiver_address,
                'kas_amount': kas_lock,
                'sat_amount': sat_lock,
                'sender_address': address,
                'output_address': kas_out,
            }
            response_payload = {
                'sender_address': address,
                'ln_invoice': ln_invoice,
                'kas_amount': kas_lock,
                'sat_amount': sat_lock,
            }
            counterparty_short_pubkey = f"{remote_node_pubkey[:3]}...{remote_node_pubkey[-3:]}"
            logger.info(f"Got sat2kas request from {counterparty_short_pubkey} with address: {receiver_address}")
            return swap_type, swap_kwargs, response_payload, kas_lock

        if swap_type == 'kas2sat':
            swap_type = 'kas2sat'
            ln_invoice = msg_payload['ln_invoice']
            try:
                decoded_invoice = await self.decode_ln_invoice(
                    ln_invoice
                )
            except InvoiceInvalid as e:
                logger.error(e)
                return None
            invoice_sats = int(decoded_invoice['amount_msat']) // 1000
            amounts = await self._resolve_init_amounts(
                'kas2sat', msg_payload, remote_node_pubkey,
                invoice_sats=invoice_sats,
            )
            if amounts is None:
                return None
            kas_lock, sat_lock, swap_price, implied_kas = amounts
            if abs(invoice_sats - sat_lock) > 1:
                return None
            logger.info(f"KAS amount from quote: {kas_lock}")
            if not self._validate_offer(remote_node_pubkey, 'kas2sat', swap_price, implied_kas):
                return None
            sender_address = msg_payload['sender_address']
            swap_kwargs = {
                'invoice': ln_invoice,
                'receiver_address': address,
                'kas_amount': kas_lock,
                'sat_amount': sat_lock,
                'sender_address': sender_address,
                'output_address': kas_out,
            }
            response_payload = {
                'receiver_address': address,
                'kas_amount': kas_lock,
                'sat_amount': sat_lock,
            }
            counterparty_short_pubkey = f"{remote_node_pubkey[:3]}...{remote_node_pubkey[-3:]}"
            logger.info(f"Got kas2sat request from {counterparty_short_pubkey} with address: {sender_address}")
            return swap_type, swap_kwargs, response_payload, kas_lock

        if swap_type == 'btc2kas':
            amounts = await self._resolve_init_amounts(
                'btc2kas', msg_payload, remote_node_pubkey,
            )
            if amounts is None:
                return None
            kas_lock, sat_lock, swap_price, implied_kas = amounts
            if not self._validate_offer(remote_node_pubkey, 'btc2kas', swap_price, implied_kas):
                return None
            await self._assert_btc_locktime(msg_payload['btc_locktime'], 'taker')
            kas_locktime = await self.gen_kas_locktime('maker')
            btc_receiver_address = btc_address.to_string()
            swap_kwargs = {
                'receiver_address': msg_payload['receiver_address'],
                'kas_amount': kas_lock,
                'sender_address': address,
                'btc_sender_address': msg_payload['btc_sender_address'],
                'btc_receiver_address': btc_receiver_address,
                'btc_locktime': msg_payload['btc_locktime'],
                'kas_locktime': kas_locktime,
                'secret_hash': msg_payload['secret_hash'],
                'sat_amount': sat_lock,
                'output_address': kas_out,
                'btc_output_address': btc_out,
            }
            response_payload = {
                'sender_address': address,
                'btc_receiver_address': btc_receiver_address,
                'kas_amount': kas_lock,
                'sat_amount': sat_lock,
                'kas_locktime': kas_locktime,
            }
            return swap_type, swap_kwargs, response_payload, kas_lock

        if swap_type == 'kas2btc':
            amounts = await self._resolve_init_amounts(
                'kas2btc', msg_payload, remote_node_pubkey,
            )
            if amounts is None:
                return None
            kas_lock, sat_lock, swap_price, implied_kas = amounts
            if not self._validate_offer(remote_node_pubkey, 'kas2btc', swap_price, implied_kas):
                return None
            await self._assert_kas_locktime(msg_payload['kas_locktime'], 'taker')
            btc_locktime = await self.gen_btc_locktime('maker')
            btc_sender_address = btc_address.to_string()
            swap_kwargs = {
                'receiver_address': address,
                'kas_amount': kas_lock,
                'sender_address': msg_payload['sender_address'],
                'btc_sender_address': btc_sender_address,
                'btc_receiver_address': msg_payload['btc_receiver_address'],
                'btc_locktime': btc_locktime,
                'kas_locktime': msg_payload['kas_locktime'],
                'secret_hash': msg_payload['secret_hash'],
                'sat_amount': sat_lock,
                'output_address': kas_out,
                'btc_output_address': btc_out,
            }
            response_payload = {
                'receiver_address': address,
                'btc_sender_address': btc_sender_address,
                'kas_amount': kas_lock,
                'sat_amount': sat_lock,
                'btc_locktime': btc_locktime,
            }
            return swap_type, swap_kwargs, response_payload, kas_lock

        return None

    async def monitor_swaps(self):
        """One pass over every live swap.

        This tick is the maker's retry loop: a redeem or refund that did not
        go out is attempted again on the next pass, for as long as the
        contract still holds the money. That is why nothing here may raise
        past a single swap - one unreachable node used to take the whole
        round down with it, and with it every other swap's funding watch and
        refund deadline.
        """
        for swap_type, monitor in (
            ('sat2kas', self.monitor_sat2kas),
            ('kas2sat', self.monitor_kas2sat),
            ('btc2kas', self.monitor_btc2kas),
            ('kas2btc', self.monitor_kas2btc),
        ):
            swaps = list(self.swaps[swap_type].values())
            if not swaps:
                continue
            results = await asyncio.gather(
                *(monitor(swap) for swap in swaps), return_exceptions=True,
            )
            for swap, result in zip(swaps, results):
                if isinstance(result, asyncio.CancelledError):
                    raise result
                if isinstance(result, BaseException):
                    logger.error(
                        f"[{swap_type}] monitor failed for {swap.contract_address}: "
                        f"{result}",
                        exc_info=result,
                    )

    async def monitor_sat2kas(self, swap):
        db_swap = self.db_swap_by_contract(swap.contract_address)
        if db_swap is None:
            logger.error(f"[sat2kas] No maker row for {swap.contract_address}")
            return
        utxo_sum = await self.kas_funded_amount(swap)
        if utxo_sum and db_swap.status == 'OPENED':
            self.set_swap_status(db_swap, 'FUNDED')

        # Fund once: the row is the gate. Write FUNDED after the call whatever
        # the wallet answered — a missing txid must not leave us re-paying.
        if not swap.kas_funding_txid and db_swap.status != 'FUNDED' and not swap.is_expired():
            txid = None
            try:
                txid = await self.sm.kaspa_wallet_service.pay(
                    swap.contract_address, swap.kas_amount, sm=self.sm
                )
            except Exception as e:
                logger.error(f"[sat2kas] KAS funding failed: {e}", exc_info=True)
            swap.kas_funding_txid = txid or None
            self.set_swap_status(db_swap, 'FUNDED', txid=txid or None)
            logger.info(f"[sat2kas] KAS funding sent ({txid or 'txid unknown'})")
            return

        # Refund after invoice expiry + grace
        if utxo_sum and swap.refund_window_open():
            if await self._spend_step('sat2kas', swap, db_swap, 'kas'):
                return

        # Invoice paid (and contract emptied) → COMPLETED. Keep checking through
        # the refund grace so a settle near the deadline is still seen.
        if not swap.refund_window_open():
            try:
                out_data = await self.sm.ln_wallet_service.check_invoice(
                    swap.secret_hash.hex()
                )
                if not out_data:
                    return
                state = out_data.get('state') or out_data.get('status')
                settled = (
                    state == 'SETTLED'
                    or state == 'complete'
                    or out_data.get('paid') is True
                    or bool(out_data.get('preimage') or out_data.get('payment_preimage'))
                )
                if settled and not utxo_sum:
                    self.set_swap_status(db_swap, 'COMPLETED')
                    del self.swaps['sat2kas'][swap.contract_address]
                    logger.info(f"[sat2kas] Exiting after redeem\n{'-'*10}\n")
            except Exception as e:
                logger.error(f"check_invoice failed: {e}", exc_info=True)
            return

        # Past grace with an empty contract: funding never landed (or was already
        # swept without us seeing the invoice settle). Drop the swap.
        if not utxo_sum:
            logger.warning(
                f"[sat2kas] {swap.kas_amount} KAS never appeared on {swap.contract_address}, "
                f"expiring swap {db_swap.id}"
            )
            self.set_swap_status(db_swap, 'EXPIRED')
            del self.swaps['sat2kas'][swap.contract_address]

    async def monitor_kas2sat(self, swap):
        if not swap.kas_amount:
            return
        db_swap = self.db_swap_by_contract(swap.contract_address)
        if db_swap is None:
            logger.error(f"[kas2sat] No maker row for {swap.contract_address}")
            return

        secret = db_swap.secret
        if not secret:
            if not await self.kas_funded(swap):
                # check if invoice is expired
                # if yes, then delete swap from list and set it as EXPIRED in db
                if int(swap.timelock / 1e3 - time.time()) < 0:
                    self.set_swap_status(db_swap, 'EXPIRED')
                    del self.swaps['kas2sat'][swap.contract_address]
                    logger.info(f"[kas2sat] Exiting after invoice expiry\n{'-'*10}\n")
                return

            # wait for N confirmations before sweeping the P2SH utxo(s)
            if not await self.kas_confirmed(swap):
                return

            secret = await self._paid_invoice(swap)
            if not secret:
                return
            # Written before the redeem is attempted: the preimage is the only
            # thing that can take the contract, and a crash between paying and
            # spending would otherwise leave sats gone with nothing to show.
            db_swap.secret = secret
            self.set_swap_status(db_swap, 'FUNDED')

        return await self._spend_step(
            'kas2sat', swap, db_swap, 'kas', secret=bytes.fromhex(secret),
        )

    async def _paid_invoice(self, swap):
        """The preimage for this swap's invoice, paying it if we have not.

        Asked before every attempt, because the monitor comes back every tick
        and a payment that is still finding a route must not be sent twice.
        The three answers that are not "settled" all mean "not yet": in flight
        is a payment that may still land, and a wallet we cannot read is not
        evidence of anything.
        """
        payment_hash = swap.secret_hash.hex()
        payment = await self.check_ln_payment(payment_hash)
        status = payment.get('status')
        if status == PaymentStatus.SETTLED:
            preimage = payment.get('preimage')
            if preimage and swap.validate_preimage(preimage):
                return preimage
            logger.error(
                f"[kas2sat] {payment_hash} is settled but the wallet gave us no "
                f"usable preimage"
            )
            return None
        if status == PaymentStatus.IN_FLIGHT:
            logger.info(f"[kas2sat] payment {payment_hash} is still in flight")
            return None
        if status == PaymentStatus.UNKNOWN:
            logger.warning(
                f"[kas2sat] cannot read payment {payment_hash}, not paying blind"
            )
            return None
        min_daa = getattr(self.sm.kaspad_service, 'min_daa_confirmations', None)
        if min_daa is None:
            min_daa = int(os.getenv('MIN_DAA_CONFIRMATIONS', 150))
        if swap.timelock / 1000 - time.time() < max(30, min_daa * 4 / 10):
            logger.info('[kas2sat] too little invoice time left, not paying')
            return None
        return await self.pay_ln_invoice(swap.invoice, swap=swap)

    async def monitor_btc2kas(self, swap):
        db_swap = self.db_swap_by_contract(swap.contract_address)
        if db_swap is None:
            logger.error(f"[btc2kas] No maker row for {swap.contract_address}")
            return
        # Everything below the secret is pre-commitment work, and it is skipped
        # once we hold one. What must not be skipped is the redeem: this gate
        # used to wrap it too, so a single rejected broadcast left every later
        # tick doing nothing while our bitcoin sat in a contract we could
        # still take.
        if swap.secret is None:
            # wait for btc funds (absolute kas locktime only — no init tolerance)
            if not swap.btc_utxos:
                await self.btc_funded(swap)
            if not swap.btc_utxos and db_swap.status != 'FUNDED':
                if await self.kas_locktime_expired(swap):
                    self.set_swap_status(db_swap, 'EXPIRED')
                    del self.swaps['btc2kas'][swap.contract_address]
                    logger.info(
                        f"[btc2kas] Exiting: kas locktime reached while waiting for BTC\n{'-'*10}\n"
                    )
                return

            # Fund KAS only after BTC confirmations; once only
            if not swap.kas_funding_txid and db_swap.status != 'FUNDED':
                if not await self.btc_confirmed(swap):
                    if await self.kas_locktime_expired(swap):
                        self.set_swap_status(db_swap, 'EXPIRED')
                        del self.swaps['btc2kas'][swap.contract_address]
                        logger.info(
                            f"[btc2kas] Exiting: kas locktime reached while waiting for BTC confirmations\n{'-'*10}\n"
                        )
                    return
                txid = None
                try:
                    txid = await self.sm.kaspa_wallet_service.pay(
                        swap.contract_address, swap.kas_amount, sm=self.sm
                    )
                except Exception as e:
                    logger.error(f"[btc2kas] KAS funding failed: {e}", exc_info=True)
                swap.kas_funding_txid = txid or None
                self.set_swap_status(db_swap, 'FUNDED', txid=txid or None)
                logger.info(f"[btc2kas] KAS funding sent ({txid or 'txid unknown'})")

            if not swap.utxos:
                if await self.kas_funded(swap):
                    logger.info('Kas UTXO detected! Starting monitoring for btc2kas...')
                    if db_swap.status == 'OPENED':
                        self.set_swap_status(db_swap, 'FUNDED')

            # Own funding known even if UTXO snapshot missed (funded-then-spent)
            if not swap.utxos and not swap.kas_funding_txid and db_swap.status != 'FUNDED':
                if await self.kas_locktime_expired(swap):
                    self.set_swap_status(db_swap, 'EXPIRED')
                    del self.swaps['btc2kas'][swap.contract_address]
                    logger.info(
                        f"[btc2kas] Exiting: kas locktime reached before KAS funding\n{'-'*10}\n"
                    )
                return
            # monitor KAS utxo being spent
            secret = await self.extract_kas_htlc_secret(swap)
            if secret is True:
                # Same as the kas2btc twin: our own refund, seen instead of
                # acknowledged, so there is no txid to settle on.
                logger.warning(
                    f"[btc2kas] refund detected with no txid to hand the settler, "
                    f"recording REFUNDED on an unconfirmed sighting"
                )
                self.set_swap_status(db_swap, 'REFUNDED')
                del self.swaps['btc2kas'][swap.contract_address]
                logger.info(f"[btc2kas] Exited after refund detected\n{'-' * 10}\n")
                return
            if not isinstance(secret, bytes):
                if swap.utxos and await self.kas_locktime_expired(swap):
                    await self._spend_step('btc2kas', swap, db_swap, 'kas')
                return

            logger.info(f"Secret detected: {secret.hex()}")
            swap.secret = secret
            # Persisted before the redeem is tried. The kaspa reveal is found
            # by a checkpointed virtual-chain scan, so a secret seen once and
            # not written down is gone: the scan floor has already moved past
            # the block that held it.
            db_swap.secret = secret.hex()
            db_swap.save()

        # Reached on the tick the secret appears and on every tick after it,
        # until the redeem lands.
        return await self._spend_step(
            'btc2kas', swap, db_swap, 'btc', secret=swap.secret,
        )

    async def monitor_kas2btc(self, swap):
        db_swap = self.db_swap_by_contract(swap.contract_address)
        if db_swap is None:
            logger.error(f"[kas2btc] No maker row for {swap.contract_address}")
            return

        # Nothing of ours is at stake until we send, so everything the taker
        # owes us is demanded here: the agreed amount (not merely a utxo, which
        # let a dust-funded contract buy a full BTC payment) and enough
        # confirmations. Absolute btc locktime only, no init tolerance.
        #
        # Once we have funded, this gate is never asked again. A momentary
        # empty read of a contract we are about to sweep must not stop the
        # secret watch below, which is the only thing standing between a
        # revealed secret and our BTC.
        if not swap.btc_funding_txid and db_swap.status != 'FUNDED':
            if not await self.kas_funded(swap) or not await self.kas_confirmed(swap):
                if await self.btc_locktime_expired(swap):
                    self.set_swap_status(db_swap, 'EXPIRED')
                    del self.swaps['kas2btc'][swap.contract_address]
                    logger.info(
                        f"[kas2btc] Exiting: btc locktime reached while waiting for KAS\n{'-'*10}\n"
                    )
                return
            logger.info(
                f"Funding P2WSH address {swap.btc_contract_address} with {swap.sat_amount / 1e8} BTC"
            )
            txid = None
            try:
                txid = await self.sm.btc_wallet_service.send_to_address(
                    swap.btc_contract_address,
                    swap.sat_amount,
                )
            except Exception as e:
                logger.error(f"[kas2btc] BTC funding failed: {e}", exc_info=True)
            swap.btc_funding_txid = txid or None
            self.set_swap_status(db_swap, 'FUNDED', btc_txid=txid or None)
            logger.info(f"[kas2btc] BTC funding sent ({txid or 'txid unknown'})")

        if not swap.btc_utxos:
            await self.btc_funded(swap)
            if swap.btc_utxos:
                logger.info('Btc UTXO detected, starting monitoring for kas2btc...')
                if db_swap.status == 'OPENED':
                    self.set_swap_status(db_swap, 'FUNDED')

        # Own funding known even if UTXO snapshot missed (funded-then-spent)
        if not swap.btc_utxos and not swap.btc_funding_txid and db_swap.status != 'FUNDED':
            if await self.btc_locktime_expired(swap):
                self.set_swap_status(db_swap, 'EXPIRED')
                del self.swaps['kas2btc'][swap.contract_address]
                logger.info(
                    f"[kas2btc] Exiting: btc locktime reached before BTC funding\n{'-'*10}\n"
                )
            return

        # The bitcoin reveal is found by scanning the funding outpoint, so
        # unlike the kaspa side it can be read again on any later tick. That
        # is what turned a failing redeem here into an endless one: rescanning
        # forever is not the same as retrying. Once seen, the secret is
        # written down and this scan is done with.
        if swap.secret is None:
            secret = await self.extract_btc_htlc_secret(swap)
            if secret is True:
                # Our own refund, found on chain rather than acknowledged when
                # we sent it. A refund whose broadcast answered with a txid
                # never reaches here - it settles and drops the swap in the
                # same breath - so this is the one that got away.
                logger.warning(
                    f"[kas2btc] refund detected with no txid to hand the settler, "
                    f"recording REFUNDED on an unconfirmed sighting"
                )
                self.set_swap_status(db_swap, 'REFUNDED')
                del self.swaps['kas2btc'][swap.contract_address]
                logger.info('[kas2btc] Swap refunded (detected)')
                return
            if not isinstance(secret, bytes):
                if swap.btc_utxos and await self.btc_locktime_expired(swap):
                    await self._spend_step('kas2btc', swap, db_swap, 'btc')
                return

            logger.debug(f"[kas2btc] Secret detected: {secret.hex()}")
            swap.secret = secret
            db_swap.secret = secret.hex()
            db_swap.save()

        return await self._spend_step(
            'kas2btc', swap, db_swap, 'kas', secret=swap.secret,
        )

    async def update_offers(self):
        pass

    def clear_expired_locked_offers(self):
        """Drop expired per-peer locked quotes (background hygiene)."""
        now = int(time.time())
        empty_pubkeys = []
        for pubkey, by_type in self.locked_offers.items():
            for swap_type, entry in by_type.items():
                if entry.get('offers') and entry.get('valid_until', 0) <= now:
                    logger.debug(
                        f"Clearing expired locked offers for {pubkey[:8]}... ({swap_type})"
                    )
                    by_type[swap_type] = self._empty_locked_type()
            if all(not entry.get('offers') for entry in by_type.values()):
                empty_pubkeys.append(pubkey)
        for pubkey in empty_pubkeys:
            del self.locked_offers[pubkey]

    def get_locked_offers(self, remote_pubkey, swap_type):
        """Return locked offers if still within valid_until; else clear and return []."""
        try:
            entry = self.locked_offers[remote_pubkey][swap_type]
        except KeyError:
            return []
        offers = entry.get('offers') or []
        if not offers:
            return []
        if entry.get('valid_until', 0) <= int(time.time()):
            logger.debug(
                f"Locked offers expired for {remote_pubkey[:8]}... ({swap_type})"
            )
            self.locked_offers[remote_pubkey][swap_type] = self._empty_locked_type()
            return []
        return offers


async def main():
    maker = Maker(output_address='kaspa:qr2y4cg72p09fhpwfs3dxudwz5duxlx774ejwvwgvr9yf5p4a8edzdrt50e8q')
    await maker.start()


if __name__ == '__main__':
    asyncio.run(main())
