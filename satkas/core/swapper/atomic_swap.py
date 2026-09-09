
import os
import time
import logging
import hashlib

from pprint import pformat

from bitcoinutils.transactions import Transaction, TxInput, TxOutput, TxWitnessInput, Locktime
from bitcoinutils.keys import PrivateKey, P2wpkhAddress, P2wshAddress
from bitcoinutils.utils import to_satoshis
from bitcoinutils.setup import setup as btc_network_setup

from satkas.core.klib.kbech32 import decode_address
from satkas.core.klib.kaddress import get_pubkey_hash, p2sh_address_from_script
from satkas.core.klib.scripting import (
    build_contract_script,
    build_spend_script
)
from satkas.core.klib.ktx_mass import (
    estimate_spend_mass, mass,
    P2SH_REDEEM_SIGSCRIPT_LN, P2SH_REDEEM_SIGSCRIPT_DAA,
)
from satkas.core.klib.ktransactions import pay_from_address
from satkas.core.klib.kdatatype import (
    SighashReusedValues,
    SigHashType,
)
from satkas.core.klib.ksign import raw_tx_in_signature
from satkas.core.klib.serialization import gen_rpc_transaction

from satkas.core.blib.scripting import build_btc_contract_script, build_btc_spend_script

logger = logging.getLogger('atomic_swap')
logger.setLevel(logging.INFO)
logging.getLogger('peewee').setLevel(logging.WARNING)
logging.getLogger('asyncio').setLevel(logging.WARNING)
logging.getLogger('aiohttp').setLevel(logging.WARNING)


# ToDo: switch some methods to async
# ToDo: move some methods to utils, keep the code cleaner
# MEMO (post-implement): When self.sm is None, do we require SM, log
# manual steps, or inject a NullServiceManager? 
class AtomicSwap:
    # Wait this long past the timelock before refunding, so a counterparty
    # redeem broadcast near the deadline still gets a chance to confirm.
    REFUND_GRACE_MS = 180_000

    # Above this, timelock is a millisecond timestamp (LN swaps); below, it is
    # a block daa score (on-chain swaps). Mirrors Counterparty.MAX_KAS_LOCKTIME.
    TIMESTAMP_LOCKTIME_FLOOR = 500_000_000_000

    def __init__(self, *args, **kwargs):
        # Optional ServiceManager — all chain I/O goes through self.sm.
        self.sm = kwargs.get('service_manager', None)

        # LN stuff
        self.invoice = kwargs.get('invoice', None)

        # Kaspa stuff
        self.sender_address = kwargs.get('sender_address')
        if self.sender_address:
            self.sender_pubkey = bytes(decode_address(self.sender_address)[1])
            self.sender_pkh = get_pubkey_hash(self.sender_pubkey)
        self.sender_private_key = kwargs.get('sender_private_key')

        self.receiver_address = kwargs.get('receiver_address')
        if self.receiver_address:
            self.receiver_pubkey = bytes(decode_address(self.receiver_address)[1])
            self.receiver_pkh = get_pubkey_hash(self.receiver_pubkey)
        self.receiver_private_key = kwargs.get('receiver_private_key')

        self.output_address = kwargs.get('output_address', None)
        self.output_pubkey = bytes(decode_address(self.output_address)[1]) if self.output_address else None

        self.kas_locktime = kwargs.get('kas_locktime', None)

        # BTC stuff
        self.btc_sender_address = kwargs.get('btc_sender_address', None)
        self.btc_receiver_address = kwargs.get('btc_receiver_address', None)
        self.btc_sender_private_key = None
        self.btc_receiver_private_key = None
        self.btc_sender_pubkey_hash = None
        self.btc_receiver_pubkey_hash = None
        self.btc_output_address = kwargs.get('btc_output_address', None)
        self.btc_locktime = kwargs.get('btc_locktime', None)
        self.btc_contract_script = None
        self.btc_contract_address = ''
        self.btc_contract_descriptor_imported = False
        self.btc_utxos = []
        self.btc_transaction = None
        # Set when we broadcast our own funding; lets spend detection work even
        # if the UTXO is gone before the first successful listunspent snapshot.
        self.btc_funding_txid = kwargs.get('btc_funding_txid', None)
        self.kas_funding_txid = kwargs.get('kas_funding_txid', None)

        # swap stuff
        self.kas_amount = kwargs.get('kas_amount', 0)
        self.sat_amount = kwargs.get('sat_amount', 0)
        self.timelock = 0
        self.secret = None
        self.secret_hash = bytes.fromhex(kwargs.get('secret_hash', ''))
        self.contract_script = b''
        self.contract_address = ''
        self.transaction = None
        self.utxos = []
        # The last spend built by spend_contract / spend_btc_contract, kept so
        # a failed broadcast can be retried with the same bytes.
        self.kas_rpc_tx = None
        self.btc_raw_tx = None
        # In-memory virtual-chain floor for Kaspa HTLC scans (not persisted on
        # the swap DB row). Seeded from kaspad_service at watch start.
        self.vchain_checkpoint = None

        # Last result of each chain probe, owned by the polling layer in
        # Counterparty. Lives here so it dies with the swap and so a maker
        # watching many swaps keeps them apart with no bookkeeping.
        self.probe_cache = {}

        # LN-only swaps omit BTC addresses; skip pubkey derivation for those.
        if self.btc_sender_address and self.btc_receiver_address:
            self.calculate_public_keys()
            self.timelock = self.kas_locktime  # for off-chain swaps this is populated during invoice decoding

    def calculate_public_keys(self):
        # BTC
        # ensure network is correctly set
        btc_network_setup(os.getenv('BTC_NETWORK', 'mainnet'))
        logger.debug(f"{self.btc_sender_address} ({type(self.btc_sender_address)})", flush=True)
        self.btc_sender_address = P2wpkhAddress.from_address(self.btc_sender_address)
        self.btc_receiver_address = P2wpkhAddress.from_address(self.btc_receiver_address)
        self.btc_sender_pubkey_hash = self.btc_sender_address.witness_program
        self.btc_receiver_pubkey_hash = self.btc_receiver_address.witness_program

        # KAS <- Skipped, already calculated in __init__

    def time_remaining(self):
        return int(self.timelock / 1e3 - time.time())

    def is_expired(self):
        return self.timelock / 1e3 < time.time()

    def refund_window_open(self):
        return self.timelock + self.REFUND_GRACE_MS < time.time() * 1000

    def is_timestamp_locktime(self):
        """True when timelock is a deadline in time, False when it is a daa score.

        time_remaining() and is_expired() only mean something in the first case.
        """
        return self.timelock >= self.TIMESTAMP_LOCKTIME_FLOOR

    def validate_preimage(self, preimage):
        """Whether preimage hashes to this swap's secret hash.

        Takes hex or raw bytes, and answers False for anything missing or
        malformed rather than raising. Callers reach here holding whatever a
        wallet or a human gave them, including nothing at all, and a preimage
        that cannot be checked is no different from one that does not match.
        """
        if not preimage:
            return False
        if isinstance(preimage, (bytes, bytearray)):
            preimage_bytes = bytes(preimage)
        elif isinstance(preimage, str):
            preimage = preimage.strip()
            try:
                preimage_bytes = bytes.fromhex(preimage)
            except ValueError:
                return False
        else:
            return False
        if len(preimage_bytes) != 32:
            return False
        return hashlib.sha256(preimage_bytes).digest() == self.secret_hash

    def gen_contract_address(self):
        self.contract_script = build_contract_script(
            self.secret_hash,
            self.receiver_pkh,
            self.timelock,
            self.sender_pkh)

        self.contract_address = p2sh_address_from_script(self.contract_script)
        logger.info(f"Contract P2SH address: {self.contract_address}")

    async def gen_btc_contract_address(self):
        self.btc_contract_script = build_btc_contract_script(
            self.btc_sender_pubkey_hash,
            self.btc_receiver_pubkey_hash,
            self.btc_locktime,
            self.secret_hash
        )
        self.btc_contract_address = P2wshAddress.from_script(self.btc_contract_script).to_string()
        logger.info(f"BTC P2WSH address: {self.btc_contract_address}")

    async def spend_contract(self, secret=None):
        if secret is None:
            # refund path
            pubkey = self.sender_pubkey
            privkey = self.sender_private_key
            dest_addr = self.output_address if self.output_address else self.sender_address
        else:
            pubkey = self.receiver_pubkey
            privkey = self.receiver_private_key
            dest_addr = self.output_address if self.output_address else self.receiver_address

        # Spend refresh: same dust_limit as honor, cap 10 (honor uses 3).
        dust_limit = 100_000_000  # 1 KAS sompi; become env/setting later
        lock = round((self.kas_amount or 0) * 1e8)
        self.utxos = await self.sm.kaspad_service.get_utxos_by_addresses(
            self.contract_address,
            min_utxo_size=min(dust_limit, lock) if lock else dust_limit,
            max_utxo_count=10,
        )
        utxo_sompi = sum(
            int(u.get('utxoEntry', {}).get('amount') or 0) for u in self.utxos
        )
        spend_mass = estimate_spend_mass(
            utxo_sompi or int((self.kas_amount or 0) * 1e8),
            sigscript_size=(
                P2SH_REDEEM_SIGSCRIPT_LN if self.is_timestamp_locktime()
                else P2SH_REDEEM_SIGSCRIPT_DAA
            ),
        )
        fee_sompi, feerate = 0, 0
        if self.sm is not None:
            fee_sompi, feerate = await self.sm.kaspad_service.estimate_send_fee(spend_mass)
        if not fee_sompi:
            fee_sompi = 100 * spend_mass  # Toccata min-relay if RPC fee is missing
        self.transaction = await pay_from_address(
            self.contract_address,
            dest_addr,
            amount=0,  # <- send full amount of the given utxos
            fee=fee_sompi,
            sm=self.sm,
            utxos=self.utxos,
        )
        if secret is None:
            self.transaction.locktime = self.timelock

        # Dummy 65-byte sig: same script size as the real one. Fee from that mass.
        for tx_in in self.transaction.inputs:
            tx_in.signature_script = build_spend_script(
                b'\x00' * 65, pubkey, self.contract_script,
                refund=(not secret), secret=secret
            )
        spend_mass = mass(self.transaction)
        fee_sompi = int(round((feerate or 100) * spend_mass)) or 100 * spend_mass
        in_sum = sum(int(i.utxo_entry.amount) for i in self.transaction.inputs)
        out_sum = sum(int(o.value) for o in self.transaction.outputs)
        self.transaction.outputs[-1].value += (in_sum - out_sum - fee_sompi)

        rv = SighashReusedValues()
        hashtype = SigHashType(1)
        for i, txIn in enumerate(self.transaction.inputs):
            signature = raw_tx_in_signature(self.transaction, i, hashtype, privkey, rv)
            spend_contract_script = build_spend_script(
                signature, pubkey, self.contract_script,
                refund=(not secret), secret=secret
            )
            self.transaction.inputs[i].signature_script = spend_contract_script

        rpc_tx = gen_rpc_transaction(self.transaction)
        logger.debug(f"Finalized transaction, ready to broadcast:")
        logger.debug(pformat(rpc_tx))
        # Kept so a caller can rebroadcast this exact transaction instead of
        # rebuilding it, which is what a retry after a lost response wants.
        self.kas_rpc_tx = rpc_tx
        # Legacy: tx_id = await self.broadcast_transaction(rpc_tx)
        tx_id = await self.sm.kaspad_service.submit_transaction(rpc_tx)
        if tx_id:
            logger.info(f"txid: {tx_id}")
        return tx_id

    async def spend_btc_contract(self, secret=None):
        if secret is None:
            # refund path
            private_key = PrivateKey.from_bytes(self.btc_sender_private_key)
            out_address = self.btc_output_address if self.btc_output_address else self.btc_sender_address
        else:
            private_key = PrivateKey.from_bytes(self.btc_receiver_private_key)
            out_address = self.btc_output_address if self.btc_output_address else self.btc_receiver_address
        # build_btc_contract() rebinds btc_sender_address/btc_receiver_address to
        # P2wpkhAddress objects, so the fallbacks above are objects while callers
        # that set btc_output_address pass a string. from_address() below needs
        # the string form.
        if not isinstance(out_address, str):
            out_address = out_address.to_string()
        pubkey = private_key.get_public_key()

        # Spend refresh: same dust_limit as honor, cap 10 (honor uses 3).
        dust_limit = 1000  # sats; become env/setting later
        lock = int(self.sat_amount or 0)
        outputs = await self.sm.bitcoin_service.check_utxos_for_address(
            self.btc_contract_address,
            min_confirmations=0,
            include_unconfirmed=True,
            min_utxo_size=min(dust_limit, lock) if lock else dust_limit,
            max_utxo_count=10,
        )
        self.btc_utxos = [
            {'txid': o.txid, 'vout': o.vout, 'amount': o.amount_sats / 1e8}
            for o in outputs
        ]

        amounts = []
        amount_sum = 0
        tx_inputs = []
        for utxo in self.btc_utxos:
            tx_inputs.append(TxInput(utxo['txid'], utxo['vout'], sequence=b"\xfe\xff\xff\xff"))
            amount = utxo['amount']
            amounts.append(amount)
            amount_sum += amount

        in_sats = to_satoshis(amount_sum)
        btc_locktime = self.btc_locktime if secret is None else 0
        locktime = Locktime(btc_locktime).for_transaction()
        tx = Transaction(
            tx_inputs,
            [TxOutput(in_sats, P2wpkhAddress.from_address(out_address).to_script_pub_key())],
            has_segwit=True, locktime=locktime,
        )

        # Dummy 71-byte sig: same stack shape as the real one. Fee from that vsize.
        dummy_sig = '00' * 71
        contract_hex = self.btc_contract_script.to_hex()
        for _ in tx.inputs:
            tx.witnesses.append(TxWitnessInput(build_btc_spend_script(
                dummy_sig, pubkey, contract_hex,
                refund=(not secret), secret=secret,
            )))
        vsize = tx.get_vsize()
        fee = 0
        if self.sm is not None:
            fee, _ = await self.sm.bitcoin_service.estimate_send_fee(vsize)
        if not fee:
            fee = vsize  # 1 sat/vB
        tx.outputs[0].amount = in_sats - fee
        tx.witnesses.clear()

        for i, txIn in enumerate(tx.inputs):
            sig = private_key.sign_segwit_input(
                tx, i, self.btc_contract_script, to_satoshis(amounts[i])
            )
            spend_script = build_btc_spend_script(
                sig, pubkey, contract_hex,
                refund=(not secret), secret=secret
            )
            tx.witnesses.append(TxWitnessInput(spend_script))

        raw_tx = tx.serialize()
        logger.debug(f"Final transaction: {raw_tx}")
        # Kept so a caller can rebroadcast this exact transaction instead of
        # rebuilding it, which is what a retry after a lost response wants.
        self.btc_raw_tx = raw_tx
        # Legacy brpc:
        # await sendrawtransaction(tx.serialize())
        sent = await self.sm.bitcoin_service.send_raw_transaction(raw_tx)
        txid = tx.get_txid()
        # get_txid() computes the hash locally, so it answers whether or not
        # the transaction was ever broadcast. Returning it regardless told
        # callers a failed send had succeeded, and the swap then waited for
        # confirmations on a transaction no node had ever seen.
        if not sent:
            logger.error(f"[BTC] broadcast of {txid} was not accepted")
            return False
        logger.info(f"[BTC] txid: {txid}")
        return txid
