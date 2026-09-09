import asyncio
import logging
from typing import Optional

from coincurve import PublicKeyXOnly

from satkas.core.services.base_service import BaseService
from satkas.core.klib.kaddress import p2pk_address
from satkas.core.klib.kgrpc import getUtxosByAddresses, submitTransaction
from satkas.core.klib.ktransactions import pay_from_address, sign_p2pk_with_key
from satkas.core.klib.serialization import gen_rpc_transaction

logger = logging.getLogger('internal_kaspawallet')


class InternalKaspaWalletService(BaseService):
    """
    Kaspa wallet backed by a single private key from env (testnet).
    Signs and broadcasts plain P2PK sends via klib.
    """
    service_icon = "wallet"
    icon_style = "kaspa"

    default_fee_sompi = 1_000_000

    def __init__(self):
        super().__init__()
        self.service_name = "Internal Kaspa Wallet"
        self.service_status_string = ''
        self.is_enabled = False
        self.is_detected = False
        self.update_status_task = None

        self.private_key_hex = None
        self.fee_sompi = self.default_fee_sompi
        self._privkey = None
        self._address = None

        self.configs = {
            'private_key_hex': {
                'attr': 'private_key_hex',
                'default': None,
                'fallback': None,
                'type': str,
                'env': 'INTERNAL_KASPA_PRIVKEY',
            },
            'fee_sompi': {
                'attr': 'fee_sompi',
                'default': self.default_fee_sompi,
                'fallback': self.default_fee_sompi,
                'type': int,
                'env': 'INTERNAL_KASPA_FEE',
            },
        }
        self.load_config()
        self._refresh_key_material()
        self._notify_change(['service_name', 'service_status_string', 'is_enabled', 'is_detected'])

    def parse_config_string(self, text):
        pass

    @property
    def config_string(self):
        return ''

    def _refresh_key_material(self):
        self._privkey = None
        self._address = None
        hex_key = (self.private_key_hex or '').strip()
        if not hex_key:
            return
        try:
            privkey = bytes.fromhex(hex_key)
            if len(privkey) != 32:
                logger.warning('INTERNAL_KASPA_PRIVKEY must be 32 bytes (64 hex chars)')
                return
            x_only_pubkey = PublicKeyXOnly.from_secret(privkey).format()
            self._privkey = privkey
            self._address = p2pk_address(x_only_pubkey)
        except Exception as e:
            logger.warning(f"Failed to parse INTERNAL_KASPA_PRIVKEY: {e}")

    def reload_config(self):
        super().reload_config()
        self._refresh_key_material()

    async def detect(self):
        self._refresh_key_material()
        self.is_detected = self._privkey is not None and self._address is not None
        self._notify_change(['is_detected'])
        return self.is_detected

    async def validate(self):
        return self.is_detected

    def enable(self, sm=None):
        self.is_enabled = True
        self.update_status_task = asyncio.create_task(
            self.update_status(run_once=False, sm=sm)
        )
        self._notify_change(['is_enabled'])

    def disable(self):
        self.is_enabled = False
        if self.update_status_task:
            self.update_status_task.cancel()
            self.update_status_task = None
        self._notify_change(['is_enabled'])

    async def update_status(self, run_once=True, sm=None):
        while True:
            if self.is_detected and self.is_enabled:
                try:
                    addr = self._address or ''
                    short_addr = f"{addr[:18]}...{addr[-8:]}" if len(addr) > 30 else addr
                    balance = await self.get_balance(sm=sm)
                    status_string = f"Address: {short_addr}\nBalance: {balance} KAS"
                except Exception:
                    status_string = 'Error reading internal wallet status'
                    self.is_detected = False
                    self._notify_change(['is_detected'])
                if status_string != self.service_status_string:
                    self.service_status_string = status_string
                    self._notify_change(['service_status_string'])
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    return
            else:
                await self.detect()
                await asyncio.sleep(1)
            if run_once:
                break

    def _require_key(self):
        if not self._privkey or not self._address:
            self._refresh_key_material()
        if not self._privkey or not self._address:
            raise RuntimeError(
                'Internal Kaspa wallet private key not configured '
                '(set INTERNAL_KASPA_PRIVKEY)'
            )

    async def get_address(self) -> Optional[str]:
        self._require_key()
        return self._address

    async def get_new_address(self) -> Optional[str]:
        # Single-key wallet: no HD rotation
        return await self.get_address()

    def _sum_utxo_balance_kas(self):
        self._require_key()
        entries = getUtxosByAddresses(self._address).get('entries', []) or []
        total_sompi = sum(int(e['utxoEntry']['amount']) for e in entries)
        return total_sompi / 1e8

    async def get_balance(self, sm=None):
        try:
            self._require_key()
            if sm is not None:
                entries = await sm.kaspad_service.get_utxos_by_addresses(
                    self._address
                )
            else:
                # entries = getUtxosByAddresses(self._address).get('entries', []) or []
                entries = await asyncio.to_thread(
                    lambda: getUtxosByAddresses(self._address).get('entries', []) or []
                )
            total_sompi = sum(int(e['utxoEntry']['amount']) for e in entries)
            return total_sompi / 1e8
        except Exception as e:
            logger.error(f"get_balance failed: {e}", exc_info=True)
            return 0

    # def _pay_sync(self, destination, amount):
    #     self._require_key()
    #     fee = int(getattr(self, 'fee_sompi', None) or self.default_fee_sompi)
    #     tx = asyncio.run(pay_from_address(self._address, destination, amount, fee=fee))
    #     if not tx.inputs:
    #         raise RuntimeError(f"No UTXOs to spend from {self._address}")
    #     sign_p2pk_with_key(tx, self._privkey)
    #     rpc_tx = gen_rpc_transaction(tx)
    #     res = submitTransaction(rpc_tx)
    #     tx_id = res.get('transactionId')
    #     if tx_id is None:
    #         raise RuntimeError(f"submitTransaction failed: {res}")
    #     return tx_id

    async def pay(self, destination, amount, sm=None):
        self._require_key()
        logger.info(f"Paying {amount} KAS to {destination} from {self._address}")
        fee = int(getattr(self, 'fee_sompi', None) or self.default_fee_sompi)
        tx = await pay_from_address(
            self._address, destination, amount, fee=fee, sm=sm,
        )
        if not tx.inputs:
            raise RuntimeError(f"No UTXOs to spend from {self._address}")
        sign_p2pk_with_key(tx, self._privkey)
        rpc_tx = gen_rpc_transaction(tx)
        if sm is not None:
            tx_id = await sm.kaspad_service.submit_transaction(rpc_tx)
            if not tx_id:
                raise RuntimeError('submit_transaction failed')
        else:
            res = submitTransaction(rpc_tx)
            tx_id = res.get('transactionId')
            if tx_id is None:
                raise RuntimeError(f"submitTransaction failed: {res}")
        logger.info(f"Broadcast txid: {tx_id}")
        return tx_id

    async def send_transaction(self, address, amount, fee=None, sm=None):
        if fee is not None:
            self.fee_sompi = int(fee)
        return await self.pay(address, amount, sm=sm)
