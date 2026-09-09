import asyncio
import hashlib
import logging
import os
import time
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple, Union

from satkas.core.services.base_service import BaseService
from satkas.core.blib.brpc import (
    BrpcError,
    raise_for_error,
    getblockcount,
    getblockchaininfo,
    estimatesmartfee,
    gettxout,
    gettxspendingprevout,
    gettransaction,
    listunspent,
    listtransactions,
    listwallets,
    createwallet,
    loadwallet,
    getdescriptorinfo,
    importdescriptors,
    sendrawtransaction,
)

logger = logging.getLogger('bitcoind_service')


@dataclass
class TxOutput:
    txid: str
    vout: int
    address: Optional[str]
    amount_sats: int
    spent: bool
    confirmations: int
    confirmed: bool
    spend_txid: Optional[str] = None
    spend_vin: Optional[int] = None


@dataclass
class SpendWitness:
    spend_txid: str
    vin_index: int
    funding_txid: str
    funding_vout: int
    script_sig: str
    witness: List[str]
    confirmations: int


class BitcoindService(BaseService):
    """
    Watch-only Bitcoin chain monitoring via bitcoind RPC.
    Monitoring and broadcast — no private keys.
    """
    service_icon = "bitcoin"
    icon_style = "bitcoin"

    default_host = '127.0.0.1'
    default_port = 8332
    default_wallet_name = 'satkas_watchonly'
    default_min_confirmations = 1
    default_tip_cache_ttl = 30.0

    def __init__(self, host=None, port=None):
        super().__init__()
        self.service_name = 'Bitcoind'
        self.service_status_string = ''
        self.is_enabled = False
        self.is_detected = False

        self.configs = {
            'host': {
                'attr': 'host',
                'default': self.default_host,
                'fallback': None,
                'type': str,
                'env': 'BITCOIND_HOST',
            },
            'port': {
                'attr': 'port',
                'default': self.default_port,
                'fallback': None,
                'type': int,
                'env': 'BITCOIND_PORT',
            },
            'rpc_user': {
                'attr': 'rpc_user',
                'default': '',
                'fallback': None,
                'type': str,
                'env': 'BTC_RPC_USER',
            },
            'rpc_pass': {
                'attr': 'rpc_pass',
                'default': '',
                'fallback': None,
                'type': str,
                'env': 'BTC_RPC_PASS',
            },
            'wallet_name': {
                'attr': 'wallet_name',
                'default': self.default_wallet_name,
                'fallback': None,
                'type': str,
                'env': 'BITCOIND_WALLET_NAME',
            },
            'min_confirmations': {
                'attr': 'min_confirmations',
                'default': self.default_min_confirmations,
                'fallback': None,
                'type': int,
                'env': 'BITCOIND_MIN_CONFIRMATIONS',
            },
        }

        if host is None and port is None:
            self.load_config()
        else:
            self.host = host if host else self.default_host
            self.port = port if port else self.default_port
            self.rpc_user = os.getenv('BTC_RPC_USER', '')
            self.rpc_pass = os.getenv('BTC_RPC_PASS', '')
            self.wallet_name = os.getenv('BITCOIND_WALLET_NAME', self.default_wallet_name)
            self.min_confirmations = int(
                os.getenv('BITCOIND_MIN_CONFIRMATIONS', self.default_min_confirmations)
            )

        # Ensure attributes exist even if load_config left gaps
        if not hasattr(self, 'host') or self.host is None:
            self.host = self.default_host
        if not hasattr(self, 'port') or self.port is None:
            self.port = self.default_port
        if not hasattr(self, 'rpc_user') or self.rpc_user is None:
            self.rpc_user = ''
        if not hasattr(self, 'rpc_pass') or self.rpc_pass is None:
            self.rpc_pass = ''
        if not hasattr(self, 'wallet_name') or self.wallet_name is None:
            self.wallet_name = self.default_wallet_name
        if not hasattr(self, 'min_confirmations') or self.min_confirmations is None:
            self.min_confirmations = self.default_min_confirmations

        self._wallet_ready = False
        self._imported_addresses = set()
        self.update_status_task = None
        self._tip_height_cache: Optional[int] = None
        self._tip_height_cached_at = 0.0
        self._tip_cache_ttl = float(os.getenv('BTC_TIP_CACHE_TTL', self.default_tip_cache_ttl))
        self._notify_change(['service_name', 'service_status_string', 'is_enabled', 'is_detected'])

    # --- Config ---

    def parse_config_string(self, text):
        if not text:
            self.host = self.default_host
            self.port = self.default_port
            return
        parts = text.split(':')
        if len(parts) >= 2:
            self.host = parts[0]
            self.port = int(parts[1])
            if len(parts) >= 3 and parts[2]:
                self.wallet_name = parts[2]
        else:
            self.host = text
            self.port = self.default_port

    @property
    def config_string(self):
        return f"{self.host}:{self.port}"

    def _rpc_kwargs(self, with_wallet=False):
        kwargs = {
            'server': f"{self.host}:{self.port}",
            'user': self.rpc_user,
            'password': self.rpc_pass,
        }
        if with_wallet:
            kwargs['rpcwallet'] = self.wallet_name
        return kwargs

    # --- Lifecycle ---

    async def detect(self):
        try:
            res = await getblockcount(**self._rpc_kwargs())
            raise_for_error(res)
            # Prefer getblockchaininfo for sync status; fall back if unavailable
            try:
                info_res = await getblockchaininfo(**self._rpc_kwargs())
                raise_for_error(info_res)
                info = info_res.get('result') or {}
                blocks = info.get('blocks', 0)
                headers = info.get('headers', blocks)
                if headers and blocks is not None and abs(headers - blocks) > 1:
                    logger.warning(
                        f"bitcoind not fully synced: blocks={blocks} headers={headers}"
                    )
            except BrpcError:
                pass
            # Wallet support check (node may be compiled with -disablewallet)
            try:
                await listwallets(**self._rpc_kwargs())
            except BrpcError as e:
                logger.error(f"bitcoind wallet RPC unavailable: {e}")
                self.is_detected = False
                self.service_status_string = 'Wallet support missing'
                self._notify_change(['is_detected', 'service_status_string'])
                return False
            self.is_detected = True
        except Exception as e:
            logger.debug(f"Bitcoind detect failed: {e}")
            self.is_detected = False
        self._notify_change(['is_detected'])
        return self.is_detected

    async def validate(self):
        if not self.is_detected:
            return False
        try:
            await self._ensure_watch_wallet()
            return self._wallet_ready
        except Exception as e:
            logger.error(f"Bitcoind validate failed: {e}")
            return False

    def enable(self):
        self.is_enabled = True
        os.environ['BITCOIND_HOST'] = str(self.host)
        os.environ['BITCOIND_PORT'] = str(self.port)
        os.environ['BTC_RPC_USER'] = self.rpc_user or ''
        os.environ['BTC_RPC_PASS'] = self.rpc_pass or ''
        # Watch-only wallet name — do not set BTC_WALLET_NAME (owned by spending wallet service)
        os.environ['BITCOIND_WALLET_NAME'] = self.wallet_name
        self.update_status_task = asyncio.create_task(self.update_status(run_once=False))
        self._notify_change(['is_enabled'])

    def disable(self):
        self.is_enabled = False
        if self.update_status_task:
            self.update_status_task.cancel()
            self.update_status_task = None
        self._notify_change(['is_enabled'])

    async def update_status(self, run_once=True):
        while True:
            if self.is_detected and self.is_enabled:
                try:
                    height = await self.get_block_height(force_refresh=True)
                    status_string = f"Server: {self.config_string}\nHeight: {height}\nWallet: {self.wallet_name}"
                except Exception:
                    status_string = 'Error getting chain info'
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

    # --- Wallet internals ---

    async def _ensure_watch_wallet(self):
        if self._wallet_ready:
            return
        wallets_res = await listwallets(**self._rpc_kwargs())
        raise_for_error(wallets_res)
        loaded = wallets_res.get('result') or []
        if self.wallet_name in loaded:
            self._wallet_ready = True
            return
        # Try load, then create
        load_res = await loadwallet(self.wallet_name, **self._rpc_kwargs())
        if load_res.get('error') is None:
            self._wallet_ready = True
            return
        create_res = await createwallet(
            self.wallet_name,
            disable_private_keys=True,
            descriptors=True,
            **self._rpc_kwargs(),
        )
        # Wallet may already exist on disk but not loaded
        err = create_res.get('error')
        if err is not None:
            # Retry load after create failure (e.g. already exists)
            load_res = await loadwallet(self.wallet_name, **self._rpc_kwargs())
            raise_for_error(load_res)
        self._wallet_ready = True
        logger.info(f"Watch-only wallet ready: {self.wallet_name}")

    async def _ensure_address_imported(self, address: str):
        await self._ensure_watch_wallet()
        if address in self._imported_addresses:
            return
        raw_descriptor = f"addr({address})"
        info_res = await getdescriptorinfo(raw_descriptor, **self._rpc_kwargs())
        raise_for_error(info_res)
        descriptor = info_res['result']['descriptor']
        while True:
            res = await importdescriptors(
                descriptor,
                **self._rpc_kwargs(with_wallet=True),
            )
            error = res.get('error')
            if error is None:
                # importdescriptors returns result as list of per-descriptor statuses
                result = res.get('result') or []
                if isinstance(result, list) and result:
                    if all(r.get('success') for r in result):
                        self._imported_addresses.add(address)
                        logger.info(f"Imported watch address {address}")
                        return
                    logger.error(f"importdescriptors failed: {result}")
                else:
                    self._imported_addresses.add(address)
                    return
            else:
                logger.error(f"importdescriptors error: {error}")
            await asyncio.sleep(3)

    # --- Tier 1: pure query ---

    def _tip_cache_valid(self) -> bool:
        if self._tip_height_cache is None:
            return False
        return (time.monotonic() - self._tip_height_cached_at) < self._tip_cache_ttl

    async def get_block_height(self, force_refresh: bool = False) -> int:
        if not force_refresh and self._tip_cache_valid():
            return self._tip_height_cache
        res = await getblockcount(**self._rpc_kwargs())
        raise_for_error(res)
        height = int(res['result'])
        self._tip_height_cache = height
        self._tip_height_cached_at = time.monotonic()
        return height

    async def check_output(self, txid: str, vout: int, *, include_mempool: bool = True) -> TxOutput:
        """Point-in-time outpoint status. Works for spent and unspent outputs."""
        await self._ensure_watch_wallet()
        txout_res = await gettxout(txid, vout, include_mempool, **self._rpc_kwargs())
        raise_for_error(txout_res)
        txout = txout_res.get('result')
        if txout is not None:
            value_btc = txout.get('value', 0)
            confs = int(txout.get('confirmations', 0))
            spk = txout.get('scriptPubKey') or {}
            return TxOutput(
                txid=txid,
                vout=vout,
                address=spk.get('address'),
                amount_sats=int(round(value_btc * 1e8)),
                spent=False,
                confirmations=confs,
                confirmed=confs > 0,
            )

        # Output spent or never existed — use wallet gettransaction (no -txindex).
        amount_sats = 0
        address = None
        funding_confs = 0
        try:
            tx_res = await gettransaction(
                txid, True, True, **self._rpc_kwargs(with_wallet=True)
            )
            raise_for_error(tx_res)
            result = tx_res.get('result') or {}
            funding_confs = int(result.get('confirmations', 0) or 0)
            decoded = result.get('decoded') or {}
            vouts = decoded.get('vout') or []
            if vout < len(vouts):
                vo = vouts[vout]
                amount_sats = int(round(vo.get('value', 0) * 1e8))
                address = (vo.get('scriptPubKey') or {}).get('address')
            if address is None:
                for d in result.get('details') or []:
                    if d.get('vout') == vout:
                        address = d.get('address')
                        if amount_sats == 0 and d.get('amount') is not None:
                            amount_sats = int(round(abs(d['amount']) * 1e8))
                        break
        except BrpcError as e:
            logger.debug(f"gettransaction failed for {txid}: {e}")

        spend_txid, spend_vin = await self._find_spender(txid, vout, address)
        return TxOutput(
            txid=txid,
            vout=vout,
            address=address,
            amount_sats=amount_sats,
            spent=True,
            confirmations=funding_confs,
            confirmed=funding_confs > 0,
            spend_txid=spend_txid,
            spend_vin=spend_vin,
        )

    async def _find_spender(
        self, txid: str, vout: int, address: Optional[str]
    ) -> Tuple[Optional[str], Optional[int]]:
        # 1. gettxspendingprevout (mempool + optional txspenderindex)
        try:
            res = await gettxspendingprevout(
                [{'txid': txid, 'vout': vout}],
                **self._rpc_kwargs(),
            )
            raise_for_error(res)
            entries = res.get('result') or []
            if entries:
                spending = entries[0].get('spendingtxid')
                if spending:
                    vin_index = await self._vin_index_in_tx(spending, txid, vout)
                    return spending, vin_index
        except BrpcError as e:
            logger.debug(f"gettxspendingprevout unavailable: {e}")

        # 2. Watch-wallet listtransactions fallback
        if address:
            try:
                await self._ensure_address_imported(address)
                found = await self._find_spend_via_wallet(txid, vout)
                if found:
                    return found
            except Exception as e:
                logger.debug(f"wallet spend lookup failed: {e}")

        return None, None

    async def _vin_index_in_tx(
        self, spend_txid: str, funding_txid: str, funding_vout: int
    ) -> Optional[int]:
        # Wallet gettransaction — no -txindex required.
        try:
            res = await gettransaction(
                spend_txid,
                True,
                True,
                **self._rpc_kwargs(with_wallet=True),
            )
            raise_for_error(res)
            decoded = (res.get('result') or {}).get('decoded') or {}
            vin = decoded.get('vin') or []
            for i, v in enumerate(vin):
                if v.get('txid') == funding_txid and v.get('vout') == funding_vout:
                    return i
        except BrpcError:
            pass
        return None

    async def _find_spend_via_wallet(
        self, funding_txid: str, funding_vout: int
    ) -> Optional[Tuple[str, int]]:
        res = await listtransactions(
            '*', 100, 0, True, **self._rpc_kwargs(with_wallet=True)
        )
        raise_for_error(res)
        txs = res.get('result') or []
        spent_txs = [t for t in txs if t.get('category') == 'send']
        for spent_tx in reversed(spent_txs):
            spend_txid = spent_tx.get('txid', '')
            if not spend_txid:
                continue
            tx_res = await gettransaction(
                spend_txid, True, True, **self._rpc_kwargs(with_wallet=True)
            )
            if tx_res.get('error'):
                continue
            vin = (tx_res.get('result') or {}).get('decoded', {}).get('vin') or []
            for i, v in enumerate(vin):
                if v.get('txid') == funding_txid and v.get('vout') == funding_vout:
                    return spend_txid, i
        return None

    async def check_utxos_for_address(
        self,
        address: str,
        *,
        min_confirmations: int = 0,
        include_unconfirmed: bool = True,
        min_utxo_size=None,
        max_utxo_count=None,
    ) -> List[TxOutput]:
        await self._ensure_address_imported(address)
        minconf = 0 if include_unconfirmed else max(min_confirmations, 1)
        res = await listunspent(
            [address],
            minconf=minconf,
            **self._rpc_kwargs(with_wallet=True),
        )
        raise_for_error(res)
        outputs = []
        for r in res.get('result') or []:
            confs = int(r.get('confirmations', 0))
            if confs < min_confirmations:
                continue
            amount_btc = r.get('amount', 0)
            outputs.append(
                TxOutput(
                    txid=r.get('txid'),
                    vout=int(r.get('vout', 0)),
                    address=r.get('address', address),
                    amount_sats=int(round(amount_btc * 1e8)),
                    spent=False,
                    confirmations=confs,
                    confirmed=confs > 0,
                )
            )
        if min_utxo_size is not None:
            outputs = [o for o in outputs if o.amount_sats >= min_utxo_size]
        if max_utxo_count is not None:
            outputs.sort(key=lambda o: o.amount_sats, reverse=True)
            outputs = outputs[:max_utxo_count]
        return outputs

    async def check_address_outputs(
        self,
        address: str,
        *,
        include_spent: bool = True,
        include_unconfirmed: bool = True,
    ) -> List[TxOutput]:
        await self._ensure_address_imported(address)
        # Collect candidate outpoints from wallet history + current utxos
        seen = set()
        candidates = []

        utxos = await self.check_utxos_for_address(
            address,
            min_confirmations=0,
            include_unconfirmed=include_unconfirmed,
        )
        for u in utxos:
            key = (u.txid, u.vout)
            if key not in seen:
                seen.add(key)
                candidates.append(key)

        if include_spent:
            res = await listtransactions(
                '*', 200, 0, True, **self._rpc_kwargs(with_wallet=True)
            )
            raise_for_error(res)
            for t in res.get('result') or []:
                if t.get('address') != address:
                    continue
                txid = t.get('txid')
                if not txid:
                    continue
                # Resolve vouts via gettransaction
                try:
                    tx_res = await gettransaction(
                        txid, True, True, **self._rpc_kwargs(with_wallet=True)
                    )
                    if tx_res.get('error'):
                        continue
                    decoded = (tx_res.get('result') or {}).get('decoded') or {}
                    for vo in decoded.get('vout') or []:
                        spk = vo.get('scriptPubKey') or {}
                        if spk.get('address') == address:
                            key = (txid, int(vo.get('n', 0)))
                            if key not in seen:
                                seen.add(key)
                                candidates.append(key)
                except Exception:
                    continue

        results = []
        for txid, vout in candidates:
            out = await self.check_output(txid, vout)
            if not include_spent and out.spent:
                continue
            if not include_unconfirmed and not out.confirmed and not out.spent:
                continue
            results.append(out)
        return results

    async def get_spend_witness(
        self, funding_txid: str, funding_vout: int
    ) -> Optional[SpendWitness]:
        """Extract scriptSig + witness for the vin spending the given outpoint."""
        out = await self.check_output(funding_txid, funding_vout)
        if not out.spent or not out.spend_txid:
            return None
        spend_txid = out.spend_txid
        vin_index = out.spend_vin

        decoded = None
        confs = 0
        try:
            tx_res = await gettransaction(
                spend_txid, True, True, **self._rpc_kwargs(with_wallet=True)
            )
            raise_for_error(tx_res)
            result = tx_res.get('result') or {}
            decoded = result.get('decoded') or {}
            confs = int(result.get('confirmations', 0) or 0)
        except BrpcError as e:
            logger.error(f"Cannot fetch spend tx {spend_txid}: {e}")
            return None

        vin_list = decoded.get('vin') or []
        if vin_index is None:
            for i, v in enumerate(vin_list):
                if v.get('txid') == funding_txid and v.get('vout') == funding_vout:
                    vin_index = i
                    break
        if vin_index is None or vin_index >= len(vin_list):
            return None

        v = vin_list[vin_index]
        script_sig = ''
        ss = v.get('scriptSig') or {}
        if isinstance(ss, dict):
            script_sig = ss.get('hex', '') or ''
        elif isinstance(ss, str):
            script_sig = ss
        witness = v.get('txinwitness') or []

        return SpendWitness(
            spend_txid=spend_txid,
            vin_index=vin_index,
            funding_txid=funding_txid,
            funding_vout=funding_vout,
            script_sig=script_sig,
            witness=list(witness),
            confirmations=confs,
        )

    # --- Tier 2: condition checks (timeout=False default) ---

    # async def check_funding(
    #     self,
    #     address: str,
    #     min_amount_sats: int,
    #     *,
    #     include_unconfirmed: bool = True,
    #     timeout: bool = False,
    #     poll_interval: float = 10,
    # ) -> Optional[List[TxOutput]]:
    #     while True:
    #         outputs = await self.check_utxos_for_address(
    #             address,
    #             min_confirmations=0,
    #             include_unconfirmed=include_unconfirmed,
    #         )
    #         total = sum(o.amount_sats for o in outputs)
    #         if outputs and total >= min_amount_sats:
    #             return outputs
    #         if not timeout:
    #             return None
    #         await asyncio.sleep(poll_interval)

    async def check_output_confirmed(
        self,
        txid: str,
        vout: int,
        min_confirmations: Optional[int] = None,
        *,
        timeout: bool = False,
        poll_interval: float = 10,
    ) -> Optional[TxOutput]:
        if min_confirmations is None:
            min_confirmations = self.min_confirmations
        while True:
            out = await self.check_output(txid, vout)
            if not out.spent and out.confirmations >= min_confirmations:
                return out
            if not timeout:
                return None
            await asyncio.sleep(poll_interval)

    async def check_tx_confirmations(
        self,
        txid: str,
        min_confirmations: Optional[int] = None,
        *,
        timeout: bool = False,
        poll_interval: float = 10,
    ) -> Optional[int]:
        if min_confirmations is None:
            min_confirmations = self.min_confirmations
        while True:
            confs = await self._get_tx_confirmations(txid)
            if confs is not None and confs >= min_confirmations:
                return confs
            if not timeout:
                return confs
            await asyncio.sleep(poll_interval)

    async def _get_tx_confirmations(self, txid: str) -> Optional[int]:
        # Wallet gettransaction only — getrawtransaction needs -txindex.
        try:
            res = await gettransaction(
                txid, True, True, **self._rpc_kwargs(with_wallet=True)
            )
            raise_for_error(res)
            return int((res.get('result') or {}).get('confirmations', 0) or 0)
        except BrpcError:
            return None

    async def check_output_spent(
        self,
        txid: str,
        vout: int,
        *,
        timeout: bool = False,
        poll_interval: float = 5,
    ) -> Optional[TxOutput]:
        while True:
            out = await self.check_output(txid, vout)
            if out.spent:
                return out
            if not timeout:
                return None
            await asyncio.sleep(poll_interval)

    async def check_htlc_spend(
        self,
        outpoint: Tuple[str, int],
        contract_script_hex: str,
        secret_hash: bytes,
        *,
        timeout: bool = False,
        onetime: bool = False,
        poll_interval: float = 5,
    ) -> Optional[Union[bytes, bool]]:
        """
        Detect HTLC spend of outpoint and extract secret from witness stack.
        Returns: secret bytes | True (refund) | None (pending).
        onetime=True forces a single pass (maker monitor pattern).
        """
        funding_txid, funding_vout = outpoint
        use_loop = timeout and not onetime
        while True:
            spent = await self.check_output_spent(
                funding_txid, funding_vout, timeout=False
            )
            if not spent:
                if not use_loop:
                    return None
                await asyncio.sleep(poll_interval)
                continue

            witness_info = await self.get_spend_witness(funding_txid, funding_vout)
            if not witness_info:
                if not use_loop:
                    return None
                await asyncio.sleep(poll_interval)
                continue

            witness = witness_info.witness
            if len(witness) < 4:
                if not use_loop:
                    return None
                await asyncio.sleep(poll_interval)
                continue
            if witness[-1] != contract_script_hex:
                if not use_loop:
                    return None
                await asyncio.sleep(poll_interval)
                continue

            test_secret = bytes.fromhex(witness[2]) if witness[2] else b''
            if test_secret == b'':
                logger.info('[BTC] Detected refund')
                return True
            if hashlib.sha256(test_secret).digest() == secret_hash:
                logger.info(f"[BTC] Extracted secret: {test_secret.hex()}")
                return test_secret

            if not use_loop:
                return None
            await asyncio.sleep(poll_interval)

    async def estimate_send_fee(self, transaction_size):
        """transaction_size is vbytes. Returns (fee_sats, feerate_sat_vbyte)."""
        try:
            res = await estimatesmartfee(2, 'CONSERVATIVE', **self._rpc_kwargs())
            raise_for_error(res)
        except Exception as e:
            logger.debug(f'estimatesmartfee failed: {e}')
            return 0, 0
        result = res.get('result') or {}
        if result.get('errors') or 'feerate' not in result:
            return 0, 0
        sat_vbyte = float(result['feerate']) * 1e5
        fee_sats = math.ceil(sat_vbyte * transaction_size) if transaction_size else 0
        return int(fee_sats), sat_vbyte

    async def send_raw_transaction(self, raw_hex: str) -> str:
        """Broadcast a signed raw transaction via bitcoind. Returns txid."""
        if not raw_hex or not isinstance(raw_hex, str):
            raise ValueError('raw_hex is required')
        hex_body = raw_hex.strip()
        if hex_body.startswith(('0x', '0X')):
            hex_body = hex_body[2:]
        res = await sendrawtransaction(hex_body, **self._rpc_kwargs())
        raise_for_error(res)
        return res['result']
