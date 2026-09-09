
import asyncio
import datetime
import os
import json
import hashlib
import logging
import time
from gc import collect as gc_collect

from getpass import getpass
from mnemonic import Mnemonic
from bip44 import Wallet

from satkas.core.db.models import Swap
from satkas.core.services.base_service import ExternalWalletRequired, PaymentStatus
from satkas.core.klib.kaddress import p2pk_address
from satkas.core.blib.baddress import p2wpkh_address
from satkas.core.klib.ksign import new_message_signing_hash_writer, sign_hash, verify_signature
from satkas.core.swapper.swap_errors import (
    InteractionRequired, InvoiceInvalid, KeySpaceMismatch, LocktimeRejected, TipUnavailable,
)
from satkas.core.swapper.swap_events import SwapEvent, SwapStatus
from satkas.core.swapper.atomic_swap import AtomicSwap


DERIVATION_PATH = "m/44'/111111'/0'/"
BTC_DERIVATION_PATH = "m/84'/0'/0'/0/"
logger = logging.getLogger('counterparty')
# logging.basicConfig(level=logging.INFO)


# ToDo: move methods to async
class Counterparty:
    # Which side of the book this is, 'maker' or 'taker'. Both sides write
    # their swaps into one satkas.db while deriving from separate mnemonics,
    # so every row query here is scoped by it.
    SIDE = None

    # Locktimes are keyed by the side that generates them. The taker always
    # locks for longer than the maker, so the maker has room to act first.
    KAS_LOCKTIME_DAA = {'taker': 10 * 60 * 60 * 6, 'maker': 10 * 60 * 60 * 2}
    KAS_LOCKTIME_TOLERANCE = {'taker': 60 * 60 * 6, 'maker': 60 * 60 * 2}
    BTC_LOCKTIME_BLOCKS = {'taker': 36, 'maker': 12}
    BTC_LOCKTIME_TOLERANCE = {'taker': 3, 'maker': 2}
    MAX_KAS_LOCKTIME = 500_000_000_000
    MAX_BTC_LOCKTIME = 500_000_000

    # Kaspa blocks per second, for turning a daa distance into a countdown.
    KAS_BPS = 10

    # How often each condition may be probed, in seconds. This paces the
    # network, not the caller: a loop can tick faster than this and still get
    # an answer, it just gets the previous probe back. Subclass it to go
    # slower, e.g. a maker watching many swaps:
    #   POLL_INTERVALS = {**Counterparty.POLL_INTERVALS, 'btc_funding': 30}
    POLL_INTERVALS = {
        'kas_funding': 1,
        'kas_confirmations': 1,
        'btc_funding': 2,
        'btc_confirmations': 2,
        'contract_spent': 1,
        # Paces a retried redeem or refund for a caller whose loop is a tick
        # rather than a wait, so the retry does not follow the tick's cadence.
        'spend_retry': 30,
    }

    _NOT_PROBED = object()

    def __init__(
            self,
            wallet_db_table=None,
            keep_unlocked=False,
            wallet_index=1,
            wallet_passwd=None,
            swap_endpoint=None,
            service_manager=None,
            plugins=None,
            ):
        self.sm = service_manager
        self.swap_endpoint = swap_endpoint
        self.wallet_db_table = wallet_db_table
        if self.wallet_db_table is None:
            raise Exception('wallet_db_table not defined')
        self.wallet_passwd = wallet_passwd
        self.wallet = self.wallet_db_table.get_or_none(self.wallet_db_table.id == wallet_index)
        logger.debug(f"Init wallet index {wallet_index}")
        if self.wallet is None:
            self.init_wallet()

        self.passphrase = None
        wallet = self.get_wallet(keep_unlocked)
        self.node_privkey = wallet.derive_secret_key(f"{DERIVATION_PATH}0")
        self.node_pubkey = wallet.derive_public_key(f"{DERIVATION_PATH}0")[1:]
        self.pubkey = wallet.derive_public_key(f"{DERIVATION_PATH}{self.wallet.address_counter + 1}")[1:]
        self.address = p2pk_address(self.pubkey)
        self.btc_pubkey = wallet.derive_public_key(f"{BTC_DERIVATION_PATH}{self.wallet.address_counter + 1}")
        self.btc_address = p2wpkh_address(self.btc_pubkey)

        # logger.info(f"address: {self.address}, db_address: {self.wallet.next_address}")
        assert self.wallet.next_address == self.address
        self.assert_key_space()

        # row_id -> asyncio.Task promoting COMPLETING/REFUNDING → final status
        self._settlement_tasks = {}
        self._plugins = []
        for plugin in plugins or []:
            self.register_plugin(plugin)

        del wallet
        gc_collect()

    def init_wallet(self):
        logger.info('Wallet not initialized... Generating a new one!')
        logger.info('Enter password for wallet encryption (leave empty for unencrypted wallet)')
        if self.wallet_passwd is None:
            passphrase = getpass('Password: ').strip()
        else:
            passphrase = self.wallet_passwd
        mn = Mnemonic('english').generate(256)
        logger.info(f"Your wallet mnemonic is:\n{mn}")
        self.wallet = self.wallet_db_table.create(
            mnemonic=mn,
            is_encrypted=bool(passphrase),
            next_address=p2pk_address(Wallet(mn, passphrase=passphrase).derive_public_key(f"{DERIVATION_PATH}1")[1:])
        )
        del mn, passphrase
        gc_collect()

    def get_wallet(self, keep_unlocked=False):
        if self.wallet.is_encrypted:
            if self.wallet_passwd:
                self.passphrase = self.wallet_passwd
            if self.passphrase is None:
                passphrase = getpass("Wallet password: ").strip()
                if keep_unlocked:
                    self.passphrase = passphrase
            else:
                passphrase = self.passphrase
        else:
            passphrase = ''
        return Wallet(self.wallet.mnemonic, passphrase=passphrase)

    def get_next_pubkey(self, wallet=None, n_key=0, is_btc=False):
        if wallet is None:
            wallet = self.get_wallet()
        if n_key:
            wanted_key = n_key
        else:
            wanted_key = self.wallet.address_counter + 1
        derivation_path = BTC_DERIVATION_PATH if is_btc else DERIVATION_PATH
        pubkey = wallet.derive_public_key(f"{derivation_path}{wanted_key}")
        pubkey = pubkey if is_btc else pubkey[1:]
        pubkey_len = 33 if is_btc else 32
        assert len(pubkey) == pubkey_len
        return pubkey

    def get_next_address(self, wallet=None, n_key=0, is_btc=False):
        pubkey = self.get_next_pubkey(wallet, n_key, is_btc=is_btc)
        address = p2wpkh_address(pubkey) if is_btc else p2pk_address(pubkey)
        return address

    def get_secret_key(self, n_key=0, is_btc=False):
        if n_key:
            wanted_key = n_key
        else:
            wanted_key = self.wallet.address_counter + 1
        derivation_path = BTC_DERIVATION_PATH if is_btc else DERIVATION_PATH
        return self.get_wallet().derive_secret_key(f"{derivation_path}{wanted_key}")

    # --- Which key belongs to which swap ---
    #
    # A swap's derivation index is its row's position among its own side's
    # rows. Nothing stores it: position is recoverable from a row that is
    # already there, and the maker and taker derive from separate mnemonics
    # (MakerWallet / TakerWallet), so counting the other side's rows would
    # only make both sequences sparse.

    def key_index(self, db_swap):
        """Derivation index for a swap row.

        Computed from db_swap.id, which never changes once written, so two
        processes inserting at the same moment cannot land on one index - the
        read-then-insert this replaced could.

        Holds while rows are never deleted. A deletion shifts every index
        after it, which the address check in scan_and_sweep is there to catch.
        """
        return (Swap
                .select()
                .where((Swap.side == db_swap.side) & (Swap.id <= db_swap.id))
                .count())

    def db_swap_by_contract(self, contract_address):
        """This side's row for a contract address, or None.

        The side filter is what keeps a maker monitor off a taker row: on
        regtest both sides run against one database and derive the same p2sh.
        """
        return (Swap
                .select()
                .where((Swap.p2sh_address == contract_address) & (Swap.side == self.SIDE))
                .get_or_none())

    async def current_dag_checkpoint(self):
        """Current virtual-chain hash to persist as a per-swap scan floor."""
        if self.sm is None:
            return None
        try:
            dag = await self.sm.kaspad_service.get_dag_info()
        except Exception as e:
            logger.warning(f"current_dag_checkpoint failed: {e}")
            return None
        if not dag:
            return None
        return dag.get('sink') or (dag.get('virtualParentHashes') or [None])[0] or dag.get('pruningPointHash')

    async def hydrate_swap_from_row(self, row):
        """Rebuild AtomicSwap from a persisted OPENED/FUNDED row.

        Regenerated P2SH/P2WSH must match the row.
        """
        if not row or not row.contract or not row.p2sh_address:
            logger.error('hydrate_swap_from_row: row missing contract')
            return None

        kwargs = {
            'service_manager': self.sm,
            'invoice': row.ln_invoice or None,
            'sender_address': row.sender_address,
            'receiver_address': row.receiver_address,
            'output_address': (
                row.output_address
                or getattr(self, 'output_address', None)
                or self.address
            ),
            'btc_output_address': (
                row.btc_output_address
                or getattr(self, 'btc_output_address', None)
            ),
            'kas_amount': (row.dwork_amount or 0) / 1e8,
            'sat_amount': row.sat_amount or 0,
            'secret_hash': row.secret_hash or '',
            'kas_locktime': row.kas_locktime,
            'btc_locktime': row.btc_locktime,
        }
        if row.btc_sender_address and row.btc_receiver_address:
            kwargs['btc_sender_address'] = row.btc_sender_address
            kwargs['btc_receiver_address'] = row.btc_receiver_address

        swap = AtomicSwap(**kwargs)
        if row.kas_locktime:
            swap.timelock = row.kas_locktime
        swap.vchain_checkpoint = row.dag_checkpoint_hash

        if row.secret:
            try:
                swap.secret = bytes.fromhex(row.secret)
            except ValueError:
                logger.error('hydrate_swap_from_row: invalid secret hex')
                return None

        swap.gen_contract_address()
        if swap.contract_address != row.p2sh_address:
            logger.error(
                f"hydrate_swap_from_row: kas p2sh mismatch "
                f"row={row.p2sh_address} gen={swap.contract_address}"
            )
            return None
        if row.btc_p2sh_address:
            await swap.gen_btc_contract_address()
            if swap.btc_contract_address != row.btc_p2sh_address:
                logger.error(
                    f"hydrate_swap_from_row: btc p2wsh mismatch "
                    f"row={row.btc_p2sh_address} gen={swap.btc_contract_address}"
                )
                return None
        # FUNDED is the only status where these columns are still the funding
        # outpoint; COMPLETING/REFUNDING overwrite them with the spend.
        if row.status == 'FUNDED':
            if row.txid:
                swap.kas_funding_txid = row.txid
            if row.btc_txid:
                swap.btc_funding_txid = row.btc_txid
        return swap

    def assert_key_space(self):
        """Refuse to start when the next index would already be spent.

        Rows written before the per-side index were keyed from a count of both
        sides' rows, so a database holding both leaves this side's counter
        above its own row count. Deriving from position again would hand out a
        key a past swap has already given to a counterparty.

        The other direction is safe: a counter behind the row count only skips
        indices, which costs nothing.
        """
        if self.SIDE is None:
            return
        rows = Swap.select().where(Swap.side == self.SIDE).count()
        if self.wallet.address_counter > rows:
            raise KeySpaceMismatch(
                f"{self.SIDE} wallet is at index {self.wallet.address_counter} with "
                f"{rows} {self.SIDE} rows: this database predates the per-side "
                f"key index and needs a fresh wallet"
            )

    def update_address_counter(self, new_counter=0, keep_unlocked=True):
        wallet = self.get_wallet(keep_unlocked=True)
        if new_counter:
            # Monotonic, so two swaps opening at once cannot walk the counter
            # backwards onto an index the other one is using. Nothing derives
            # from it any more - it is a high-water mark that keeps the
            # argument-less get_secret_key() off a spent key.
            self.wallet.address_counter = max(new_counter, self.wallet.address_counter)
        else:
            self.wallet.address_counter += 1
        self.pubkey = self.get_next_pubkey(wallet)
        self.address = p2pk_address(self.pubkey)
        # The bitcoin pair shares the index on its own derivation path, and was
        # left behind here until phase 4 of the taker/controller integration.
        # It did not show while the on-chain flows never rotated: now that they
        # do, a second on-chain swap in one process would build its contract
        # with the old btc pubkey and sign the refund with the new key.
        self.btc_pubkey = self.get_next_pubkey(wallet, is_btc=True)
        self.btc_address = p2wpkh_address(self.btc_pubkey)
        self.wallet.next_address = self.get_next_address(wallet)
        assert self.wallet.next_address == self.address
        self.wallet.save()
        if not keep_unlocked:
            self.passphrase = None
        del wallet
        gc_collect()

    async def _kas_tip(self, force_refresh=True):
        """Current daa score, raising if it cannot be read.

        kaspad_service returns 0 when the node is unreachable, whereas the
        bitcoin services raise. Normalise that here so both chains fail the
        same way and no caller has to special-case a zero tip.

        Validation forces a fresh read; a repeated poll passes force_refresh
        False and takes whatever the service has cached.
        """
        daa_score = await self.sm.kaspad_service.get_daa_score(force_refresh=force_refresh)
        if daa_score <= 0:
            raise TipUnavailable('kaspad reported no daa score')
        return daa_score

    async def _btc_tip(self, force_refresh=True):
        """Current block height. The bitcoin services already raise on failure."""
        return await self.sm.bitcoin_service.get_block_height(force_refresh=force_refresh)

    async def gen_kas_locktime(self, side):
        daa_score = await self._kas_tip()
        return daa_score + self.KAS_LOCKTIME_DAA[side]

    async def gen_btc_locktime(self, side):
        btc_block_count = await self._btc_tip()
        return btc_block_count + self.BTC_LOCKTIME_BLOCKS[side]

    async def _assert_kas_locktime(self, kas_locktime, side):
        """Reject a kaspa locktime that leaves us too little room to act.

        `side` is whoever generated the locktime.
        """
        daa_score = await self._kas_tip()
        if kas_locktime >= self.MAX_KAS_LOCKTIME:
            raise LocktimeRejected(f"Kaspa locktime {kas_locktime} is not a block daa score")
        remaining_blocks = kas_locktime - daa_score
        logger.debug(f"Remaining kas blocks: {remaining_blocks}")
        if side not in self.KAS_LOCKTIME_DAA:
            return
        minimum = self.KAS_LOCKTIME_DAA[side] - self.KAS_LOCKTIME_TOLERANCE[side]
        if remaining_blocks < minimum:
            raise LocktimeRejected(
                f"Kaspa locktime leaves {remaining_blocks} daa, {side} side needs {minimum}"
            )

    async def _assert_btc_locktime(self, btc_locktime, side):
        """Reject a bitcoin locktime that leaves us too few blocks to act.

        `side` is whoever generated the locktime.
        """
        btc_block_count = await self._btc_tip()
        if btc_locktime >= self.MAX_BTC_LOCKTIME:
            raise LocktimeRejected(f"Bitcoin locktime {btc_locktime} is not a block height")
        remaining_blocks = btc_locktime - btc_block_count
        logger.debug(f"Remaining btc blocks: {remaining_blocks}")
        if side not in self.BTC_LOCKTIME_BLOCKS:
            return
        minimum = self.BTC_LOCKTIME_BLOCKS[side] - self.BTC_LOCKTIME_TOLERANCE[side]
        if remaining_blocks < minimum:
            raise LocktimeRejected(
                f"Bitcoin locktime leaves {remaining_blocks} blocks, {side} side needs {minimum}"
            )

    def register_plugin(self, plugin):
        if plugin and plugin not in self._plugins:
            self._plugins.append(plugin)

    def unregister_plugin(self, plugin):
        if plugin in self._plugins:
            self._plugins.remove(plugin)

    def set_swap_status(self, db_swap, status, txid=None, btc_txid=None):
        if db_swap is None:
            return False
        changed = (
            db_swap.status != status or txid is not None or btc_txid is not None
        )
        db_swap.status = status
        db_swap.updated_at = datetime.datetime.now()
        if txid is not None:
            db_swap.txid = txid
        if btc_txid is not None:
            db_swap.btc_txid = btc_txid
        db_swap.save()
        if changed:
            for plugin in self._plugins:
                handler = getattr(plugin, 'handle_db_status_update', None)
                if handler is None:
                    continue
                try:
                    asyncio.create_task(
                        handler(self, db_swap, status, txid=txid, btc_txid=btc_txid)
                    )
                except RuntimeError:
                    break
        return True

    # Broadcast → COMPLETING/REFUNDING, then promote when the terminal tx is deep
    # enough. UI may leave; these tasks (and DB status) keep running.
    SETTLEMENT_POLL_S = 5
    # How long a settlement transaction may go missing before we stop calling
    # the swap settled. Generous, because a bitcoin transaction can sit in a
    # mempool for hours and still be perfectly alive - only one that no node
    # has heard of counts against this.
    SETTLEMENT_DEADLINE_S = 3 * 3600
    PENDING_SETTLEMENT_STATUSES = ('COMPLETING', 'REFUNDING')
    LIVE_STATUSES = ('OPENED', 'FUNDED')
    # Statuses that say our money is already out there, on their own.
    COMMITTED_STATUSES = ('FUNDED', 'COMPLETING', 'REFUNDING')

    async def capital_committed(self, row, swap=None):
        """Whether this swap still has our money riding on it.

        Asked before writing FAILED or EXPIRED, because neither is in
        LIVE_STATUSES: a terminal status over a funded contract means resume
        never looks at the row again and the money sits there until someone
        sweeps it by hand. The worst a live row costs us is another pass.

        So the burden of proof is on "not committed", and every uncertain
        answer - an unreachable node, a payment still in flight, a row with no
        contract to probe - counts as committed. Only INIT is safe by
        construction: the row is written before the maker is even asked.
        """
        if row is None:
            return False
        if row.status in self.COMMITTED_STATUSES:
            return True
        if row.status == 'INIT':
            return False
        if swap is None:
            return True
        try:
            moved = await self.our_capital_on_chain(row, swap)
        except Exception as e:
            logger.warning(f"Cannot tell whether swap {row.id} is funded: {e}")
            return True
        return moved is not False

    def start_settlement(self, row_id, chain, txid, final_status, on_event=None,
                         addresses=None):
        """Start confirmation wait for a COMPLETING/REFUNDING row; return the task."""
        if not row_id or not txid:
            logger.warning(f"start_settlement missing row_id/txid ({row_id}, {txid})")
            return None
        existing = self._settlement_tasks.get(row_id)
        if existing is not None and not existing.done():
            return existing
        task = asyncio.create_task(
            self._promote_settlement(
                row_id, chain, txid, final_status, on_event=on_event, addresses=addresses,
            )
        )
        self._settlement_tasks[row_id] = task
        return task

    async def _promote_settlement(self, row_id, chain, txid, final_status,
                                  on_event=None, addresses=None):
        started = time.monotonic()
        try:
            while True:
                if chain == 'btc':
                    confs = await self.sm.bitcoin_service.check_tx_confirmations(
                        txid, timeout=False,
                    )
                    min_c = self.sm.bitcoin_service.min_confirmations
                    if confs is not None and confs >= min_c:
                        break
                    # No answer at all is different from "not yet": the
                    # transaction is not in any mempool we can see, so it was
                    # either never accepted or has been evicted since.
                    missing = confs is None
                else:
                    if await self.sm.kaspad_service.kas_tx_confirmed(
                        txid, addresses=addresses,
                    ):
                        break
                    # Kaspa has no such distinction to offer, and does not
                    # need one: acceptance takes seconds, so a transaction
                    # still unconfirmed at the deadline is gone, not late.
                    missing = True
                if missing and time.monotonic() - started > self.SETTLEMENT_DEADLINE_S:
                    await self._demote_settlement(row_id, chain, txid)
                    return
                await asyncio.sleep(self.SETTLEMENT_POLL_S)

            row = Swap.get_by_id(row_id)
            if row.status not in self.PENDING_SETTLEMENT_STATUSES:
                return
            self.set_swap_status(row, final_status)
            event_status = (
                SwapStatus.COMPLETED if final_status == 'COMPLETED' else SwapStatus.REFUNDED
            )
            # SwapEvent only has txid; use it for both kas and btc settlement ids.
            await self._report(on_event, event_status, txid=txid)
            logger.info(f"Settlement confirmed: {final_status} ({txid})")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Settlement promote failed for row {row_id}: {e}", exc_info=True)
        finally:
            self._settlement_tasks.pop(row_id, None)

    async def _demote_settlement(self, row_id, chain, txid):
        """Hand a settlement that never landed back to the retry paths.

        COMPLETING and REFUNDING are promises that a transaction is on its
        way. Waiting on one that never arrives is the quiet way to lose a
        contract: the row reads as settled, so nothing rebroadcasts and no
        one watches the money. FUNDED is both the honest status and the one
        the monitors and resume know what to do with.
        """
        row = Swap.get_by_id(row_id)
        if row.status not in self.PENDING_SETTLEMENT_STATUSES:
            return
        logger.warning(
            f"Settlement {txid} ({chain}) never landed, swap {row_id} goes back to FUNDED"
        )
        self.set_swap_status(row, 'FUNDED')
        await self.readmit_swap(row)

    async def readmit_swap(self, row):
        """Put a demoted row back wherever this side's retry loop looks.

        The taker finds it through resume, so it has nothing to do here.
        """
        return None

    def settlement_chain_and_txid(self, row):
        """Which chain/txid a pending COMPLETING/REFUNDING row is waiting on."""
        st = row.swap_type
        side = row.side
        if row.status == 'COMPLETING':
            if st == 'btc2kas' and side == 'maker':
                return 'btc', row.btc_txid
            if st == 'kas2btc' and side == 'taker':
                return 'btc', row.btc_txid
            if st == 'kas2btc' and side == 'maker':
                return 'kas', row.txid
            return 'kas', row.txid
        if row.status == 'REFUNDING':
            if st == 'kas2btc' and side == 'maker':
                return 'btc', row.btc_txid
            if st == 'btc2kas' and side == 'taker':
                return 'btc', row.btc_txid
            return 'kas', row.txid
        return None, None

    def resume_pending_settlements(self, on_event=None):
        """Re-attach waiters for COMPLETING/REFUNDING rows after restart."""
        rows = Swap.select().where(
            (Swap.side == self.SIDE) &
            (Swap.status.in_(list(self.PENDING_SETTLEMENT_STATUSES)))
        )
        for row in rows:
            chain, txid = self.settlement_chain_and_txid(row)
            if not chain or not txid:
                logger.warning(
                    f"Cannot resume settlement for swap {row.id} "
                    f"({row.swap_type}/{row.status}): missing txid"
                )
                continue
            final = 'COMPLETED' if row.status == 'COMPLETING' else 'REFUNDED'
            # txid-only on resume (kas virtual-chain / btc gettransaction).
            self.start_settlement(
                row.id, chain, txid, final, on_event=on_event,
            )

    async def our_capital_on_chain(self, row, swap):
        """Whether this side already moved funds for this swap.

        Three answers, not two: True moved, False definitely did not, and
        None for cannot tell. A lightning payment can sit in flight for
        minutes and still go either way, so "no evidence yet" has to be
        distinguishable from "nothing was sent" - the first must not expire a
        swap, the second may.
        """
        st = row.swap_type
        if self.SIDE == 'taker':
            if st == 'btc2kas':
                return bool(await self.btc_funded(swap))
            if st in ('kas2btc', 'kas2sat'):
                if await self.kas_funded_amount(swap) > 0:
                    return True
                return await self.extract_kas_htlc_secret(swap) is not None
            if st == 'sat2kas':
                if row.secret or getattr(swap, 'secret', None):
                    return True
                # Our capital here is an invoice payment, and there is no
                # contract to look at. We may well have paid and lost the
                # response before recording the preimage, so ask the wallet:
                # only its word that nothing was ever sent frees this row.
                payment = await self.check_ln_payment(
                    row.payment_hash or swap.secret_hash.hex(), timeout=5,
                )
                status = payment.get('status')
                if status == PaymentStatus.SETTLED:
                    return True
                if status == PaymentStatus.FAILED:
                    return False
                return None
            return False
        if st in ('sat2kas', 'btc2kas'):
            if await self.kas_funded_amount(swap) > 0:
                return True
            return await self.extract_kas_htlc_secret(swap) is not None
        if st == 'kas2btc':
            if await self.btc_funded(swap):
                return True
            return await self.extract_btc_htlc_secret(swap) is not None
        if st == 'kas2sat':
            return bool(row.secret or getattr(swap, 'secret', None))
        return False

    async def resume_live_swaps(self):
        """Hydrate OPENED/FUNDED rows and return the ones still worth driving.

        A taker row we never paid for is expired here; the maker keeps its
        rows and lets monitor_swaps expire them.
        """
        rows = list(Swap
                    .select()
                    .where((Swap.side == self.SIDE) &
                           (Swap.status.in_(list(self.LIVE_STATUSES))))
                    .order_by(Swap.id.asc()))
        if not rows:
            return []
        live = []
        for row in rows:
            swap = await self.hydrate_swap_from_row(row)
            if swap is None:
                logger.error(f"Cannot hydrate swap {row.id} ({row.swap_type})")
                continue
            if row.status == 'OPENED':
                try:
                    paid = await self.our_capital_on_chain(row, swap)
                except Exception as e:
                    logger.warning(f"Resume funding probe failed for swap {row.id}: {e}")
                    paid = None
                if paid:
                    self.set_swap_status(row, 'FUNDED')
                # Only the taker drops a swap here: expiring is irreversible,
                # while the maker's monitors reach the same verdict on their
                # own tick and can change their mind until locktime.
                #
                # None means the probe could not tell - an unreachable node, or
                # a lightning payment still in flight. The row stays OPENED and
                # gets driven, which is the harmless way to be wrong.
                elif paid is not None and self.SIDE == 'taker':
                    self.set_swap_status(row, 'EXPIRED')
                    logger.info(f"Expired unfunded {row.swap_type} swap {row.id}")
                    continue
            live.append((row, swap))
            logger.info(f"Resumed {row.swap_type} swap {row.id} ({row.status})")
        return live

    # --- Wallet-facing actions ---
    #
    # Shared by taker and maker. None of them writes to the database: the two
    # sides record swap state differently, so status handling stays with the
    # caller.
    #
    # None of them reads the configured wallet flavor either. An external
    # wallet makes the service raise ExternalWalletRequired, which the caller
    # surfaces as "do this one yourself".

    async def create_ln_invoice(self, sat_amount, memo=''):
        """Create an invoice for sat_amount, returning the bolt11 string."""
        return await self.sm.ln_wallet_service.create_invoice(sat_amount, memo)

    async def decode_ln_invoice(self, invoice, swap=None, avoid_self_pay=True):
        """Decode a bolt11 invoice via the configured LN wallet.

        When swap is given, stamps sat_amount, timelock and secret_hash on it,
        and rejects an expiry past MAX_LN_INVOICE_EXPIRY or below
        max(150, min_daa * 10 / 10) seconds remaining.

        avoid_self_pay: refuse when the payee is our own node. External wallets
        raise rather than answering who we are; that is ignored (the user is
        the check). LNbits learns the pubkey from a dummy invoice at enable.
        """
        try:
            decoded = await self.sm.ln_wallet_service.decode_invoice(invoice)
        except InvoiceInvalid:
            raise
        except Exception as e:
            raise InvoiceInvalid(str(e) or 'Could not decode invoice') from e
        if not decoded:
            raise InvoiceInvalid('Could not decode invoice')

        amount_msat = decoded.get('amount_msat')
        date = decoded.get('date')
        expiry = decoded.get('expiry')
        payment_hash = decoded.get('payment_hash')
        payee = decoded.get('payee')

        if swap is not None:
            if amount_msat is None or date is None or expiry is None or not payment_hash:
                raise InvoiceInvalid('Decoded invoice is missing amount, expiry or payment hash')
            expiry_ts = int(date) + int(expiry)
            expiry_ts_max = int(time.time()) + int(os.getenv('MAX_LN_INVOICE_EXPIRY', 3600))
            if expiry_ts > expiry_ts_max:
                raise InvoiceInvalid('Invoice expiry time is too big.')
            min_daa = getattr(self.sm.kaspad_service, 'min_daa_confirmations', None)
            if min_daa is None:
                min_daa = int(os.getenv('MIN_DAA_CONFIRMATIONS', 150))
            if expiry_ts - time.time() < max(150, min_daa * 10 / 10):
                raise InvoiceInvalid('Invoice expiry time is too short.')
            swap.sat_amount = int(amount_msat) // 1000
            swap.timelock = expiry_ts * 1000
            swap.secret_hash = bytes.fromhex(str(payment_hash))

        if avoid_self_pay and payee:
            our_pubkey = await self._our_ln_pubkey()
            if our_pubkey and str(payee) == str(our_pubkey):
                raise InvoiceInvalid('Payment to ourself')

        return decoded

    async def _our_ln_pubkey(self):
        """This node's LN pubkey, or None when the wallet cannot say."""
        get_info = getattr(self.sm.ln_wallet_service, 'get_node_info', None)
        if get_info is None:
            return None
        try:
            info = await get_info()
        except (ExternalWalletRequired, NotImplementedError):
            return None
        except Exception as e:
            logger.warning(f"get_node_info failed: {e}")
            return None
        if not info:
            return None
        return info.get('identity_pubkey') or info.get('pubkey')

    async def pay_ln_invoice(self, invoice, swap=None):
        """Pay an invoice and return its preimage, or None if we got none.

        The services disagree on which key holds the preimage, hence the two
        lookups.

        Given a swap, the preimage is checked against its secret hash before
        being handed back. A preimage is the only thing that redeems the other
        chain, so a wallet answering with something that does not hash right
        is worth nothing, and finding that out here beats carrying it to the
        redeem and failing there.
        """
        payment_result = await self.sm.ln_wallet_service.pay_invoice(invoice)
        if not payment_result:
            return None
        preimage = payment_result.get('payment_preimage') or payment_result.get('preimage')
        if not preimage:
            return None
        if swap is not None and not swap.validate_preimage(preimage):
            logger.error(
                f"{self.sm.ln_wallet_service.service_name} returned a preimage that "
                f"does not hash to the invoice's payment hash, discarding it"
            )
            return None
        return preimage

    async def check_ln_payment(self, payment_hash, timeout=10):
        """Status of an outgoing lightning payment, as a PaymentStatus.

        The wrapper exists for the external wallet, which raises rather than
        answering. That reads as UNKNOWN here, which is the cautious verdict
        and the same one a wallet we cannot reach earns: the money may be
        gone, so nothing may be written off on the strength of it.
        """
        try:
            result = await self.sm.ln_wallet_service.check_payment(
                payment_hash, timeout=timeout,
            )
        except ExternalWalletRequired:
            return {'status': PaymentStatus.UNKNOWN, 'preimage': None}
        except Exception as e:
            logger.warning(f"Cannot read lightning payment {payment_hash}: {e}")
            return {'status': PaymentStatus.UNKNOWN, 'preimage': None}
        if not isinstance(result, dict):
            return {'status': PaymentStatus.UNKNOWN, 'preimage': None}
        return result

    async def fund_kas_contract(self, swap, amount=None):
        """Send KAS to the swap's contract address."""
        if amount is None:
            amount = swap.kas_amount
        return await self.sm.kaspa_wallet_service.pay(swap.contract_address, amount, sm=self.sm)

    async def fund_btc_contract(self, swap, sat_amount=None):
        """Send sats to the swap's BTC contract address, returning the txid."""
        if sat_amount is None:
            sat_amount = swap.sat_amount
        return await self.sm.btc_wallet_service.send_to_address(
            swap.btc_contract_address,
            sat_amount,
        )

    # The four spends below are not called yet: every existing call site still
    # does this inline. They are here for those sites to adopt once we decide
    # to, so review them on their merits rather than assuming a green swap run
    # covered them.
    #
    # n_key follows get_secret_key: 0 means the current counter (what a taker
    # wants), while the maker passes the swap id to get its per-swap key.
    #
    # Resolve a concrete payout address before calling these: swap field first,
    # then interaction (UI/CLI), then self.address / self.btc_address.

    async def resolve_payout_address(self, swap, interaction, *, is_btc=False,
                                     refund=False):
        """Concrete payout address for redeem/refund + settlement watch.

        1. Use the address already on the swap when present (same chain).
        2. Otherwise ask interaction (base raises InteractionRequired).
        3. If interaction returns empty, the wrong chain, or cannot answer,
           this party's address on the requested chain.

        Kaspa and Bitcoin are never mixed: a Kaspa string is not a BTC
        payout, and the reverse is also rejected.
        """
        from satkas.core.swapper.swap_interaction import SwapInteraction

        if is_btc:
            existing = getattr(swap, 'btc_output_address', None)
            if existing:
                existing = self._same_chain_or_none(
                    self._btc_address_str(existing), is_btc=True,
                )
                if existing:
                    return existing
        else:
            existing = getattr(swap, 'output_address', None)
            if existing:
                existing = self._same_chain_or_none(existing, is_btc=False)
                if existing:
                    return existing

        if interaction is None:
            interaction = SwapInteraction()
        try:
            addr = await interaction.request_output_address(
                refund=refund, is_btc=is_btc,
            )
        except InteractionRequired:
            addr = None
        if addr:
            if is_btc:
                addr = self._btc_address_str(addr)
            addr = self._same_chain_or_none(addr, is_btc=is_btc)
            if addr:
                return addr
        if is_btc:
            return self.btc_address.to_string()
        return self.address

    @staticmethod
    def _payout_chain(addr):
        """'kas', 'btc', or None when the string is not a known prefix."""
        if not addr or not isinstance(addr, str):
            return None
        text = addr.strip().lower()
        kas_prefix = os.getenv('KAS_NETWORK_PREFIX', 'kaspa').lower()
        kas_prefixes = {f'{kas_prefix}:', 'kaspa:', 'kaspatest:', 'kaspadev:'}
        if any(text.startswith(p) for p in kas_prefixes):
            return 'kas'
        if text.startswith(('bc1', 'tb1', 'bcrt1')):
            return 'btc'
        return None

    @classmethod
    def _same_chain_or_none(cls, addr, *, is_btc):
        """Drop a cross-chain address rather than spend to the wrong network."""
        chain = cls._payout_chain(addr)
        want = 'btc' if is_btc else 'kas'
        if chain is None or chain == want:
            return addr
        logger.warning(
            f"Ignoring {chain} address offered as {want} payout"
        )
        return None

    async def redeem_kas(self, swap, secret, n_key=0, output_address=None):
        """Spend the kaspa contract with the secret, returning the txid."""
        swap.receiver_private_key = self.get_secret_key(n_key)
        if output_address:
            swap.output_address = output_address
        return await swap.spend_contract(secret=secret)

    async def refund_kas(self, swap, n_key=0, output_address=None):
        """Reclaim the kaspa contract after its locktime, returning the txid."""
        swap.sender_private_key = self.get_secret_key(n_key)
        if output_address:
            swap.output_address = output_address
        return await swap.spend_contract()

    async def redeem_btc(self, swap, secret, n_key=0, output_address=None):
        """Spend the bitcoin contract with the secret, returning the txid."""
        swap.btc_receiver_private_key = self.get_secret_key(n_key, is_btc=True)
        if output_address:
            swap.btc_output_address = self._btc_address_str(output_address)
        return await swap.spend_btc_contract(secret=secret)

    async def refund_btc(self, swap, n_key=0, output_address=None):
        """Reclaim the bitcoin contract after its locktime, returning the txid."""
        swap.btc_sender_private_key = self.get_secret_key(n_key, is_btc=True)
        if output_address:
            swap.btc_output_address = self._btc_address_str(output_address)
        return await swap.spend_btc_contract()

    @staticmethod
    def _btc_address_str(address):
        """Accept a P2wpkhAddress or a string, return the string form.

        spend_btc_contract feeds this to bech32, which needs the string.
        """
        return address if isinstance(address, str) else address.to_string()

    # --- Getting a spend out ---
    #
    # A redeem or a refund is the last step between us and our money, and it
    # is one transaction against one contract that nobody else can spend the
    # way we are spending it. That makes it worth retrying for as long as it
    # stays spendable: submits fail for reasons that pass, a sequence-lock
    # race, a node restarting, a fee-rate rejection, a dropped connection.

    BROADCAST_ATTEMPTS = 5
    BROADCAST_BACKOFF_S = 1
    BROADCAST_RETRY_S = 60

    async def broadcast_with_retry(self, swap, chain, spend, *, refund=False,
                                   deadline=None, on_attempt=None):
        """Land a redeem or refund transaction, or say why we gave up.

        `spend` is awaited to build and broadcast one attempt, and returns a
        txid or something falsy. It is retried BROADCAST_ATTEMPTS times with
        exponential backoff, then through the public explorer, then on a slow
        loop until `deadline` passes. A refund has no deadline by default:
        the contract is ours from its locktime onward and nobody is racing us
        for it, so the only reason to stop is success.

        The loop ends on an empty contract rather than on a return code.
        submit_transaction reports every failure as False, including "already
        in the mempool", so a transaction that landed while its response went
        missing would otherwise be retried forever. Asking the chain instead
        answers the real question - is there still anything here to spend -
        and covers the case where the counterparty got there first.

        Returns the txid, or None. Never raises for a failed broadcast:
        the caller's swap is still live and still refundable, which is a
        state to leave alone rather than an error to report.
        """
        attempt = 0
        while True:
            if attempt and not await self._still_spendable(swap, chain):
                logger.info(
                    f"{chain} contract is empty, nothing left to broadcast"
                )
                return None
            attempt += 1
            try:
                txid = await spend()
            except Exception as e:
                txid = None
                logger.warning(f"{chain} broadcast attempt {attempt} failed: {e}")
            if txid:
                if attempt > 1:
                    logger.info(f"{chain} broadcast succeeded on attempt {attempt}")
                return txid
            if on_attempt is not None:
                try:
                    await on_attempt(attempt)
                except Exception as e:
                    logger.warning(f"broadcast progress report failed: {e}")

            if attempt == self.BROADCAST_ATTEMPTS:
                # The configured node has had its five chances. Whatever is
                # wrong with it, a public explorer does not share it.
                fallback = await self._broadcast_via_fallback(swap, chain)
                if fallback:
                    logger.info(f"{chain} broadcast went out through the public explorer")
                    return fallback

            if attempt < self.BROADCAST_ATTEMPTS:
                delay = self.BROADCAST_BACKOFF_S * (2 ** (attempt - 1))
            else:
                delay = self.BROADCAST_RETRY_S
                if deadline is not None and time.time() >= deadline:
                    logger.error(
                        f"Giving up on the {chain} "
                        f"{'refund' if refund else 'redeem'} after {attempt} attempts"
                    )
                    return None
            await asyncio.sleep(delay)

    async def _still_spendable(self, swap, chain):
        """Whether the contract we are spending still holds anything.

        An unreadable chain answers True: we cannot show the money is gone,
        and stopping on that would abandon a spend we may still need.
        """
        try:
            if chain == 'btc':
                dust_limit, max_n = self._utxo_limits('btc')
                lock = int(swap.sat_amount or 0)
                utxos = await self.sm.bitcoin_service.check_utxos_for_address(
                    swap.btc_contract_address,
                    min_confirmations=0,
                    include_unconfirmed=True,
                    min_utxo_size=min(dust_limit, lock) if lock else dust_limit,
                    max_utxo_count=max_n,
                )
                return bool(utxos)
            return await self.kas_funded_amount(swap) > 0
        except Exception as e:
            logger.warning(f"Cannot read the {chain} contract: {e}")
            return True

    async def _broadcast_via_fallback(self, swap, chain):
        """Resubmit the transaction we already built through a public explorer.

        The same bytes, not a rebuild: if the original did reach a node, this
        is the same transaction id, which the explorer either accepts as new
        or rejects as known - and the empty-contract check above sorts out
        which of those happened.
        """
        service = self.sm.fallback_broadcaster(chain) if self.sm else None
        if service is None:
            return None
        try:
            if chain == 'btc':
                raw = getattr(swap, 'btc_raw_tx', None)
                if not raw:
                    return None
                return await service.send_raw_transaction(raw)
            rpc_tx = getattr(swap, 'kas_rpc_tx', None)
            if not rpc_tx:
                return None
            return await service.submit_transaction(rpc_tx)
        except Exception as e:
            logger.warning(f"{chain} fallback broadcast failed: {e}")
            return None

    # --- Watching a swap ---
    #
    # Shared by taker and maker, and none of it blocks. Every helper looks
    # once and returns, so a taker can wrap it in a loop while a maker calls
    # it from its tick, and neither needs to know how the other is driven.
    #
    # Nothing here writes to the database or decides what an expiry costs:
    # the same expired locktime means "drop the swap" in one monitor and
    # "reclaim the contract" in another, so that call stays with the caller.

    def _utxo_limits(self, chain):
        # Honor dust limits / cap. Become env/setting later. Spend uses max 10, not this count.
        if chain == 'btc':
            return 1000, 3
        return 100_000_000, 3

    async def kas_funded_amount(self, swap, address=None):
        """How much KAS is on the contract address right now.

        Reads through kaspad_service rather than the swap's own gRPC call, and
        loads swap.utxos on the way, the same deal as btc_funded: extract_kas_htlc_secret
        / check_htlc_spend use them to spot the counterparty spending the contract.
        Honor filter: dust skipped, at most 3, amount is the sum of that list.
        """
        if address is None:
            address = swap.contract_address
        dust_limit, max_n = self._utxo_limits('kas')
        lock = round((swap.kas_amount or 0) * 1e8)
        utxos = await self.sm.kaspad_service.get_utxos_by_addresses(
            address,
            min_utxo_size=min(dust_limit, lock) if lock else dust_limit,
            max_utxo_count=max_n,
        )
        swap.utxos = utxos
        return sum(int(u['utxoEntry']['amount']) for u in utxos) / 1e8

    async def kas_funded(self, swap, kas_amount=None, address=None):
        """Whether the KAS contract holds kas_amount, loading swap.utxos.

        The contract here matches btc_funded on purpose: the threshold is
        checked in this method rather than by the caller, and swap.utxos is
        only replaced once it is met, so a caller may read the snapshot as
        "funded to the agreed amount". kas_funded_amount is the other half of
        the pair - a balance with no threshold, latching unconditionally,
        which is what the spend-detection paths want.

        Asking for a balance and testing it yourself is what let a kas2btc
        maker send bitcoin against a contract holding dust.

        Compared in sompi. The agreed amount is a float that has been through
        JSON, so a KAS-unit comparison can miss by a sompi and stay missed.
        """
        if kas_amount is None:
            kas_amount = swap.kas_amount
        if address is None:
            address = swap.contract_address
        dust_limit, max_n = self._utxo_limits('kas')
        lock = round((kas_amount or 0) * 1e8)
        utxos = await self.sm.kaspad_service.get_utxos_by_addresses(
            address,
            min_utxo_size=min(dust_limit, lock) if lock else dust_limit,
            max_utxo_count=max_n,
        )
        funded = sum(int(u['utxoEntry']['amount']) for u in utxos)
        if funded < lock:
            return False
        swap.utxos = utxos
        return True

    async def btc_funded(self, swap, sat_amount=None, address=None):
        """Whether the BTC contract holds sat_amount, loading swap.btc_utxos.

        Reads through bitcoin_service instead of the bitcoind wallet, and
        stores the outputs in the shape spend_btc_contract consumes. Skipping
        that second half is why the UI could see a funded contract and still
        build a transaction with no inputs.
        """
        if sat_amount is None:
            sat_amount = swap.sat_amount
        if address is None:
            address = swap.btc_contract_address
        dust_limit, max_n = self._utxo_limits('btc')
        lock = int(sat_amount or 0)
        outputs = await self.sm.bitcoin_service.check_utxos_for_address(
            address,
            min_confirmations=0,
            include_unconfirmed=True,
            min_utxo_size=min(dust_limit, lock) if lock else dust_limit,
            max_utxo_count=max_n,
        )
        if sum(o.amount_sats for o in outputs) < lock:
            return False
        swap.btc_utxos = [
            {'txid': o.txid, 'vout': o.vout, 'amount': o.amount_sats / 1e8}
            for o in outputs
        ]
        return True

    async def kas_confirmed(self, swap, min_confirmations=None):
        """Whether the latched contract utxos are buried deep enough to spend."""
        if min_confirmations is None:
            min_confirmations = getattr(
                self.sm.kaspad_service, 'min_daa_confirmations', None
            )
            if min_confirmations is None:
                min_confirmations = int(os.getenv('MIN_DAA_CONFIRMATIONS', 150))
        utxos = swap.utxos
        if not utxos:
            return False
        utxo_daa_score = max(int(u['utxoEntry']['blockDaaScore']) for u in utxos)
        network_daa_score = await self.sm.kaspad_service.get_daa_score()
        return utxo_daa_score + min_confirmations <= network_daa_score

    async def btc_confirmed(self, swap, min_confirmations=None):
        """Whether the bitcoin contract holds enough confirmed value to act."""
        svc = self.sm.bitcoin_service
        if min_confirmations is None:
            min_confirmations = getattr(svc, 'min_confirmations', 1)
        if swap.btc_utxos:
            for u in swap.btc_utxos:
                out = await svc.check_output_confirmed(
                    u['txid'], int(u.get('vout', 0)),
                    min_confirmations=min_confirmations, timeout=False,
                )
                if not out:
                    return False
            return True
        dust_limit, max_n = self._utxo_limits('btc')
        lock = int(swap.sat_amount or 0)
        outputs = await svc.check_utxos_for_address(
            swap.btc_contract_address,
            min_confirmations=min_confirmations,
            include_unconfirmed=False,
            min_utxo_size=min(dust_limit, lock) if lock else dust_limit,
            max_utxo_count=max_n,
        )
        if sum(o.amount_sats for o in outputs) < lock:
            return False
        swap.btc_utxos = [
            {'txid': o.txid, 'vout': o.vout, 'amount': o.amount_sats / 1e8}
            for o in outputs
        ]
        return True

    def daa_seconds(self, daa_remaining):
        """Rough seconds left, given a distance in daa score."""
        return int(daa_remaining / self.KAS_BPS)

    async def kas_daa_remaining(self, swap):
        """Daa score left before the kaspa locktime. On-chain swaps only."""
        if not swap.kas_locktime:
            return 0
        return max(swap.kas_locktime - await self._kas_tip(force_refresh=False), 0)

    async def btc_blocks_remaining(self, swap):
        """Blocks left before the bitcoin locktime."""
        if not swap.btc_locktime:
            return 0
        return max(swap.btc_locktime - await self._btc_tip(force_refresh=False), 0)

    async def kas_locktime_expired(self, swap):
        if swap.is_timestamp_locktime():
            return swap.is_expired()
        return await self.kas_daa_remaining(swap) <= 0

    async def btc_locktime_expired(self, swap):
        return await self.btc_blocks_remaining(swap) <= 0

    async def _deadline(self, swap, deadline):
        """Countdown fields for an event, plus whether the deadline passed.

        Which deadline governs is a property of the flow rather than of the
        thing being checked: waiting for kaspa during a btc2kas swap still
        runs out when the bitcoin locktime does.

        A tip we cannot read counts as unknown, never as expired. A node
        outage must not look like an expiry, same rule as the locktime checks.
        """
        try:
            if deadline == 'btc':
                blocks = await self.btc_blocks_remaining(swap)
                return {'blocks_remaining': blocks}, blocks <= 0
            if deadline == 'kas':
                if swap.is_timestamp_locktime():
                    remaining = swap.time_remaining()
                    return {'time_remaining': remaining}, remaining <= 0
                # On-chain KAS locktimes are DAA scores — report them as such
                # instead of a fake wall-clock conversion.
                daa_remaining = await self.kas_daa_remaining(swap)
                return {'daa_remaining': daa_remaining}, daa_remaining <= 0
        except Exception as e:
            logger.warning(f"Cannot read the {deadline} tip, deadline unknown: {e}")
        return {}, False

    async def emit(self, on_event, event):
        """Hand an event to a listener, which may be sync or async.

        A listener that raises is logged and ignored: a frontend bug should
        not take the swap down with it, which is what happens today when a
        Kivy callback throws inside a monitor task.
        """
        if on_event is None:
            return event
        try:
            result = on_event(event)
            if hasattr(result, '__await__'):
                await result
        except Exception as e:
            logger.error(f"Swap event listener failed: {e}", exc_info=True)
        return event

    async def _report(self, on_event, status, **fields):
        return await self.emit(on_event, SwapEvent(status=status, **fields))

    def _probed(self, swap, key, min_interval=None):
        """The last result of this probe, or _NOT_PROBED when it is due again.

        This is what lets cadence be a property of the condition: a caller
        may ask as often as it likes, the network only gets asked on the
        interval. See POLL_INTERVALS.
        """
        entry = swap.probe_cache.get(key)
        if entry is None:
            return self._NOT_PROBED
        probed_at, value = entry
        if min_interval is None:
            min_interval = self.POLL_INTERVALS.get(key, 0)
        if time.monotonic() - probed_at < min_interval:
            return value
        return self._NOT_PROBED

    def _remember(self, swap, key, value):
        swap.probe_cache[key] = (time.monotonic(), value)
        return value

    async def check_kas_funding(self, swap, min_amount=None, known=None, on_event=None,
                                min_interval=None, deadline='kas',
                                waiting_status=SwapStatus.WAITING_FUNDING,
                                funded_status=SwapStatus.FUNDED):
        """One look at the kaspa contract's balance, reported as an event.

        `known` skips the probe and reports that amount instead, for a caller
        that already watched the contract fill up. The two status arguments
        exist because flows name the same observation differently: the maker
        funding our contract is 'waiting_counterparty' during btc2kas.
        """
        if min_amount is None:
            min_amount = swap.kas_amount
        fields, expired = await self._deadline(swap, deadline)
        if expired:
            return await self._report(on_event, SwapStatus.EXPIRED, **fields)
        if known is None:
            funded = self._probed(swap, 'kas_funding', min_interval)
            if funded is self._NOT_PROBED:
                try:
                    funded = self._remember(
                        swap, 'kas_funding', await self.kas_funded_amount(swap)
                    )
                except Exception as e:
                    logger.warning(f"kas_funding probe failed: {e}")
                    funded = 0
        else:
            funded = known
        # In sompi, for the reason kas_funded gives: the amount both sides
        # agreed on is a float that has been through JSON.
        funded_enough = round(funded * 1e8) >= round((min_amount or 0) * 1e8)
        status = funded_status if funded_enough else waiting_status
        return await self._report(on_event, status, funded=funded, **fields)

    async def check_kas_confirmations(self, swap, funded=None, on_event=None, min_interval=None,
                                      deadline='kas',
                                      waiting_status=SwapStatus.WAITING_CONFIRMATIONS,
                                      confirmed_status=SwapStatus.FUNDED):
        """Whether the contract is confirmed deeply enough to spend."""
        fields, expired = await self._deadline(swap, deadline)
        if expired:
            return await self._report(on_event, SwapStatus.EXPIRED, funded=funded, **fields)
        confirmed = self._probed(swap, 'kas_confirmations', min_interval)
        if confirmed is self._NOT_PROBED:
            try:
                confirmed = self._remember(
                    swap, 'kas_confirmations', await self.kas_confirmed(swap)
                )
            except Exception as e:
                logger.warning(f"kas_confirmations probe failed: {e}")
                confirmed = False
        status = confirmed_status if confirmed else waiting_status
        return await self._report(on_event, status, funded=funded, **fields)

    async def check_btc_confirmations(self, swap, funded=None, on_event=None, min_interval=None,
                                      deadline='btc',
                                      waiting_status=SwapStatus.WAITING_CONFIRMATIONS,
                                      confirmed_status=SwapStatus.BTC_FUNDED):
        """Whether the bitcoin contract is confirmed deeply enough to spend."""
        fields, expired = await self._deadline(swap, deadline)
        if expired:
            return await self._report(
                on_event, SwapStatus.EXPIRED, funded=funded, btc_funded=True, **fields
            )
        confirmed = self._probed(swap, 'btc_confirmations', min_interval)
        if confirmed is self._NOT_PROBED:
            try:
                confirmed = self._remember(
                    swap, 'btc_confirmations', await self.btc_confirmed(swap)
                )
            except Exception as e:
                logger.warning(f"btc_confirmations probe failed: {e}")
                confirmed = False
        status = confirmed_status if confirmed else waiting_status
        return await self._report(
            on_event, status, funded=funded, btc_funded=True, **fields
        )

    async def extract_kas_htlc_secret(self, swap):
        """One pass at the kaspa HTLC spend: secret bytes, True (refund), or None.

        Captures known funding txids before refreshing utxos (a spent contract
        would otherwise wipe the snapshot). If still funded, returns None.
        Otherwise asks kaspad_service.check_htlc_spend (explorer flavor when selected).
        """
        svc = self.sm.kaspad_service
        address = swap.contract_address

        # Snapshot before kas_funded_amount replaces swap.utxos with live set.
        funding_txids = [
            u.get('outpoint', {}).get('transactionId') for u in (swap.utxos or [])
        ]
        funding_txids = [t for t in funding_txids if t]
        funding_txid = getattr(swap, 'kas_funding_txid', None)
        if funding_txid and funding_txid not in funding_txids:
            funding_txids.append(funding_txid)

        # Balance, not kas_funded: a spent contract has to clear swap.utxos
        # here, and kas_funded would leave the stale funded snapshot in place.
        # Threshold in sompi, as the btc twin below already is in sats.
        funded = await self.kas_funded_amount(swap)
        if swap.utxos and round(funded * 1e8) >= round((swap.kas_amount or 0) * 1e8):
            return None

        pubkeys = [
            p for p in (getattr(swap, 'sender_pubkey', None), getattr(swap, 'receiver_pubkey', None))
            if p
        ]
        progress = {}
        result = await svc.check_htlc_spend(
            address,
            swap.secret_hash,
            funding_txids=funding_txids or None,
            contract_script=getattr(swap, 'contract_script', None),
            pubkeys=pubkeys or None,
            start_hash=getattr(swap, 'vchain_checkpoint', None),
            onetime=True,
            progress=progress,
        )
        if progress.get('hash'):
            swap.vchain_checkpoint = progress['hash']
        return result

    async def extract_btc_htlc_secret(self, swap):
        """One pass at the bitcoin HTLC spend: secret bytes, True (refund), or None.

        Uses known outpoints when available; falls back to address history when
        the UTXO was funded and spent before we could snapshot it.
        """
        svc = self.sm.bitcoin_service
        address = swap.btc_contract_address
        contract_hex = swap.btc_contract_script.to_hex()

        dust_limit, max_n = self._utxo_limits('btc')
        lock = int(swap.sat_amount or 0)
        unspent = await svc.check_utxos_for_address(
            address,
            min_confirmations=0,
            include_unconfirmed=True,
            min_utxo_size=min(dust_limit, lock) if lock else dust_limit,
            max_utxo_count=max_n,
        )
        funded_sats = sum(o.amount_sats for o in unspent)
        if unspent and funded_sats >= lock:
            swap.btc_utxos = [
                {'txid': o.txid, 'vout': o.vout, 'amount': o.amount_sats / 1e8}
                for o in unspent
            ]
            return None

        outpoints = []
        if swap.btc_utxos:
            outpoints = [(u['txid'], int(u.get('vout', 0))) for u in swap.btc_utxos]
        funding_txid = getattr(swap, 'btc_funding_txid', None)
        if not outpoints and funding_txid:
            history = await svc.check_address_outputs(address, include_spent=True)
            outpoints = [(o.txid, o.vout) for o in history if o.txid == funding_txid]
            if not outpoints:
                outpoints = [(funding_txid, 0)]
        if not outpoints:
            history = await svc.check_address_outputs(address, include_spent=True)
            outpoints = [(o.txid, o.vout) for o in history]

        for outpoint in outpoints:
            result = await svc.check_htlc_spend(
                outpoint, contract_hex, swap.secret_hash, onetime=True,
            )
            if result is not None:
                return result
        return None

    async def check_btc_funding(self, swap, sat_amount=None, known=None, funded=None,
                                on_event=None, min_interval=None, deadline='btc',
                                waiting_status=SwapStatus.WAITING_BTC_FUNDING,
                                funded_status=SwapStatus.BTC_FUNDED):
        """One look at the bitcoin contract, loading its utxos when funded.

        `funded` is the kaspa amount to carry in the payload, which the
        on-chain flows display alongside the bitcoin side.
        """
        fields, expired = await self._deadline(swap, deadline)
        if expired:
            return await self._report(on_event, SwapStatus.EXPIRED, funded=funded, **fields)
        if known is None:
            btc_funded = self._probed(swap, 'btc_funding', min_interval)
            if btc_funded is self._NOT_PROBED:
                try:
                    btc_funded = self._remember(
                        swap, 'btc_funding', await self.btc_funded(swap, sat_amount)
                    )
                except Exception as e:
                    logger.warning(f"btc_funding probe failed: {e}")
                    btc_funded = False
        else:
            btc_funded = known
        status = funded_status if btc_funded else waiting_status
        return await self._report(
            on_event, status, funded=funded, btc_funded=btc_funded or None, **fields
        )

    async def check_contract_spent(self, swap, below_amount=None, on_event=None,
                                   min_interval=None, waiting_status=SwapStatus.MONITORING):
        """Whether the kaspa contract has been emptied.

        Both LN flows infer settlement from this. ToDo: kas2sat should also
        confirm the invoice was paid rather than trusting the balance alone.
        """
        if below_amount is None:
            below_amount = swap.kas_amount
        total = self._probed(swap, 'contract_spent', min_interval)
        if total is self._NOT_PROBED:
            try:
                total = self._remember(
                    swap, 'contract_spent', await self.kas_funded_amount(swap)
                )
            except Exception as e:
                logger.warning(f"contract_spent probe failed: {e}")
                total = below_amount
        status = SwapStatus.COMPLETED if total < below_amount else waiting_status
        return await self._report(on_event, status, funded=total)

    async def check_refund_window(self, swap, on_event=None):
        """Whether the grace period past the locktime has elapsed, so the
        contract can be reclaimed."""
        status = SwapStatus.EXPIRED if swap.refund_window_open() else SwapStatus.MONITORING
        return await self._report(on_event, status, time_remaining=swap.time_remaining())

    def sign_message(self, msg_type, payload, node_key=False):
        msg_hash = self.get_msg_hash(msg_type, payload)
        # logger.debug(f"msg_hash = {msg_hash.hex()}")
        return self.sign(msg_hash, node_key=node_key)

    # @staticmethod
    # async def fund_contract_address(address, amount=0):
    #     logger.info(f"Funding {address} with {amount} KAS ")
    #     kaspawallet_bin = os.getenv('KASPAWALLET', None)
    #     if kaspawallet_bin is None:
    #         raise Exception('kaspawallet not defined')
    #     cmd = f"{kaspawallet_bin} send"
    #     if wallet_daemon := os.getenv('KASPAWALLET_DAEMON', ''):
    #         cmd += f" -d {wallet_daemon}"
    #     cmd += f" -t {address}"
    #     if wallet_password := os.getenv('KASPAWALLET_PASSWORD', ''):
    #         cmd += f" -p {wallet_password}"
    #     cmd += f" -v {amount}"
    #     if wallet_file := os.getenv('KASPAWALLET_KEY_FILE', ''):
    #         cmd += f" -f {wallet_file}"

    #     proc = await asyncio.create_subprocess_shell(
    #         cmd,
    #         stdout=asyncio.subprocess.PIPE,
    #         stderr=asyncio.subprocess.PIPE
    #     )

    #     await asyncio.sleep(0.5)
    #     stdout, stderr = await proc.communicate()
    #     # ToDo: handle error or slow response
    #     stdout = stdout.decode().strip()
    #     stderr = stderr.decode().strip()
    #     logger.info(f"stdout: {stdout}")
    #     logger.debug(f"stderr: {stderr}")
    #     if stderr:
    #         if 'Rejected transaction' in stderr and 'already spent by transaction' in stderr:
    #             # retry after 3 seconds
    #             logger.info('utxo spent: retrying in 3 seconds')
    #             await asyncio.sleep(3)
    #             await Counterparty.fund_contract_address(address, amount)
    #         elif 'Insufficient funds for send' in stderr:
    #             # do some checks here, then retry
    #             logger.info('Not enough funds: retrying in 5 seconds')
    #             await asyncio.sleep(5)
    #             await Counterparty.fund_contract_address(address, amount)


    @staticmethod
    def get_msg_hash(msg_type, payload=None):
        if payload is not None:
            payload_string = json.dumps(payload, separators=(',', ':'))
            message_to_hash = f"{msg_type}:{payload_string}"
        else:
            message_to_hash = f"{msg_type}"
        # logger.debug(f"msg_type = {msg_type}")
        # logger.debug(f"payload_string = {payload_string}")
        msg_hasher = new_message_signing_hash_writer()
        msg_hasher.update(message_to_hash.encode())
        msg_hash = msg_hasher.digest()
        return msg_hash

    def sign(self, msg_hash, node_key=False):
        if node_key:
            priv_key = self.node_privkey
        else:
            priv_key = self.get_wallet().derive_secret_key(f"{DERIVATION_PATH}{self.wallet.address_counter + 1}")
        signature = sign_hash(msg_hash, priv_key)
        return signature

    def verify_signature(self, msg):
        if isinstance(msg, str):
            msg = json.loads(msg)
        signature = bytes.fromhex(msg['signature'])
        msg_hash = self.get_msg_hash(msg['type'], msg['payload'])
        # logger.debug(f"msg_hash = {msg_hash.hex()}")
        pubkey = bytes.fromhex(msg['pubkey'])
        sig_verify = verify_signature(signature, msg_hash, pubkey)
        if not sig_verify:
            logger.error('Error during signature verification')
            return False
        return True
