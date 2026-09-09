import asyncio
import hashlib
import logging
import math
import os
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import aiohttp

from satkas.core.services.base_service import BaseService
from satkas.core.services.bitcoind_service import TxOutput, SpendWitness

logger = logging.getLogger('mempool_service')


class MempoolApiError(Exception):
    """Raised when a mempool/Esplora HTTP request fails."""

    def __init__(self, message, status=None, body=None):
        self.status = status
        self.body = body
        super().__init__(message)


class MempoolService(BaseService):
    """
    Bitcoin chain monitoring via mempool.space Esplora REST API
    (public instance or self-hosted). Method signatures match BitcoindService.
    Also supports broadcast via POST /tx.
    """
    service_icon = "bitcoin"
    icon_style = "bitcoin"

    default_base_url = 'https://mempool.space/api'
    default_min_confirmations = 1
    default_tip_cache_ttl = 30.0

    def __init__(self, base_url=None):
        super().__init__()
        self.service_name = 'Mempool'
        self.service_status_string = ''
        self.is_enabled = False
        self.is_detected = False

        self.configs = {
            'base_url': {
                'attr': 'base_url',
                'default': self.default_base_url,
                'fallback': None,
                'type': str,
                'env': 'MEMPOOL_BASE_URL',
            },
            'min_confirmations': {
                'attr': 'min_confirmations',
                'default': self.default_min_confirmations,
                'fallback': None,
                'type': int,
                'env': 'MEMPOOL_MIN_CONFIRMATIONS',
            },
        }

        if base_url is None:
            self.load_config()
        else:
            self.base_url = base_url.rstrip('/')
            self.min_confirmations = self.default_min_confirmations

        if not hasattr(self, 'base_url') or self.base_url is None:
            self.base_url = self.default_base_url
        else:
            self.base_url = str(self.base_url).rstrip('/')
        if not hasattr(self, 'min_confirmations') or self.min_confirmations is None:
            self.min_confirmations = self.default_min_confirmations

        self.update_status_task = None
        self._tip_height_cache: Optional[int] = None
        self._tip_height_cached_at = 0.0
        self._tip_cache_ttl = float(os.getenv('BTC_TIP_CACHE_TTL', self.default_tip_cache_ttl))
        self._notify_change(['service_name', 'service_status_string', 'is_enabled', 'is_detected'])

    # --- Config ---

    def parse_config_string(self, text):
        if text:
            self.base_url = text.rstrip('/')

    @property
    def config_string(self):
        return self.base_url or ''

    # --- HTTP ---

    async def _request(self, path: str, method: str = 'GET', **kwargs) -> Any:
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        timeout = aiohttp.ClientTimeout(total=kwargs.pop('timeout', 30))
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(method, url, **kwargs) as response:
                if response.status == 404:
                    return None
                text = await response.text()
                if response.status >= 400:
                    raise MempoolApiError(
                        f"HTTP {response.status} for {url}: {text[:200]}",
                        status=response.status,
                        body=text,
                    )
                content_type = response.headers.get('Content-Type', '')
                if 'application/json' in content_type or text[:1] in ('{', '['):
                    return await response.json(content_type=None)
                # Plain text (e.g. tip height)
                return text.strip()

    async def _get(self, path: str, **kwargs) -> Any:
        return await self._request(path, 'GET', **kwargs)

    async def _post(self, path: str, data=None, **kwargs) -> Any:
        return await self._request(path, 'POST', data=data, **kwargs)

    async def send_raw_transaction(self, raw_hex: str) -> str:
        """
        Broadcast a signed raw transaction via Esplora POST /tx.
        Body is raw hex (text/plain). Returns txid.
        """
        if not raw_hex or not isinstance(raw_hex, str):
            raise ValueError('raw_hex is required')
        hex_body = raw_hex.strip()
        if hex_body.startswith(('0x', '0X')):
            hex_body = hex_body[2:]
        url = f"{self.base_url.rstrip('/')}/tx"
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                url,
                data=hex_body,
                headers={'Content-Type': 'text/plain'},
            ) as response:
                text = await response.text()
                if response.status >= 400:
                    raise MempoolApiError(
                        f"HTTP {response.status} for {url}: {text[:200]}",
                        status=response.status,
                        body=text,
                    )
                txid = (text or '').strip().strip('"')
                if not txid:
                    raise MempoolApiError('Empty txid from mempool broadcast', body=text)
                return txid

    # --- Lifecycle ---

    async def detect(self):
        try:
            height = await self.get_block_height()
            self.is_detected = isinstance(height, int) and height > 0
        except Exception as e:
            logger.debug(f"Mempool detect failed: {e}")
            self.is_detected = False
        self._notify_change(['is_detected'])
        return self.is_detected

    async def validate(self):
        return await self.detect()

    def enable(self):
        self.is_enabled = True
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
                    status_string = f"Server: {self.config_string}\nHeight: {height}"
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

    # --- Helpers ---

    def _tip_cache_valid(self) -> bool:
        if self._tip_height_cache is None:
            return False
        return (time.monotonic() - self._tip_height_cached_at) < self._tip_cache_ttl

    async def _tip_height(self, refresh: bool = False) -> int:
        return await self.get_block_height(force_refresh=refresh)

    def _confirmations_from_status(
        self, status: Optional[Dict], tip_height: int
    ) -> Tuple[int, bool]:
        if not status or not status.get('confirmed'):
            return 0, False
        block_height = status.get('block_height')
        if block_height is None:
            return 0, False
        confs = max(0, tip_height - int(block_height) + 1)
        return confs, True

    async def _get_tx(self, txid: str) -> Optional[Dict]:
        return await self._get(f"tx/{txid}")

    async def _get_outspend(self, txid: str, vout: int) -> Optional[Dict]:
        return await self._get(f"tx/{txid}/outspend/{vout}")

    # --- Tier 1: pure query ---

    async def get_block_height(self, force_refresh: bool = False) -> int:
        if not force_refresh and self._tip_cache_valid():
            return self._tip_height_cache
        res = await self._get('blocks/tip/height')
        height = int(res)
        self._tip_height_cache = height
        self._tip_height_cached_at = time.monotonic()
        return height

    async def check_output(
        self, txid: str, vout: int, *, include_mempool: bool = True
    ) -> TxOutput:
        """Point-in-time outpoint status via GET /tx/:txid/outspend/:vout."""
        tip = await self._tip_height(refresh=True)
        tx = await self._get_tx(txid)
        if tx is None:
            raise MempoolApiError(f"Transaction not found: {txid}", status=404)

        vouts = tx.get('vout') or []
        if vout < 0 or vout >= len(vouts):
            raise MempoolApiError(f"vout {vout} not found in tx {txid}")

        vo = vouts[vout]
        amount_sats = int(vo.get('value', 0))
        address = vo.get('scriptpubkey_address')
        funding_confs, funding_confirmed = self._confirmations_from_status(
            tx.get('status'), tip
        )

        outspend = await self._get_outspend(txid, vout)
        if outspend is None:
            # Treat missing outspend as unspent (Esplora normally always returns an object)
            return TxOutput(
                txid=txid,
                vout=vout,
                address=address,
                amount_sats=amount_sats,
                spent=False,
                confirmations=funding_confs,
                confirmed=funding_confirmed,
            )

        spent = bool(outspend.get('spent'))
        spend_txid = outspend.get('txid')
        spend_vin = outspend.get('vin')
        spend_status = outspend.get('status') or {}

        # Mirror bitcoind gettxout(include_mempool=False): mempool-only spends appear unspent
        if spent and not include_mempool and not spend_status.get('confirmed'):
            spent = False
            spend_txid = None
            spend_vin = None

        return TxOutput(
            txid=txid,
            vout=vout,
            address=address,
            amount_sats=amount_sats,
            spent=spent,
            confirmations=funding_confs,
            confirmed=funding_confirmed,
            spend_txid=spend_txid if spent else None,
            spend_vin=int(spend_vin) if spent and spend_vin is not None else None,
        )

    async def check_utxos_for_address(
        self,
        address: str,
        *,
        min_confirmations: int = 0,
        include_unconfirmed: bool = True,
        min_utxo_size=None,
        max_utxo_count=None,
    ) -> List[TxOutput]:
        tip = await self._tip_height(refresh=True)
        utxos = await self._get(f"address/{address}/utxo")
        if utxos is None:
            return []
        if not isinstance(utxos, list):
            raise MempoolApiError(f"Unexpected UTXO response for {address}")

        outputs = []
        for u in utxos:
            confs, confirmed = self._confirmations_from_status(u.get('status'), tip)
            if not include_unconfirmed and not confirmed:
                continue
            if confs < min_confirmations:
                continue
            outputs.append(
                TxOutput(
                    txid=u.get('txid'),
                    vout=int(u.get('vout', 0)),
                    address=address,
                    amount_sats=int(u.get('value', 0)),
                    spent=False,
                    confirmations=confs,
                    confirmed=confirmed,
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
        """
        Collect outputs from address transaction history + current UTXOs,
        then resolve spent status via check_output.
        """
        tip = await self._tip_height(refresh=True)
        seen = set()
        candidates = []

        # Current UTXOs
        for u in await self.check_utxos_for_address(
            address, min_confirmations=0, include_unconfirmed=include_unconfirmed
        ):
            key = (u.txid, u.vout)
            if key not in seen:
                seen.add(key)
                candidates.append(key)

        # Address tx history (mempool + first page of confirmed)
        txs = await self._get(f"address/{address}/txs")
        if isinstance(txs, list):
            for tx in txs:
                txid = tx.get('txid')
                if not txid:
                    continue
                status = tx.get('status') or {}
                confirmed = bool(status.get('confirmed'))
                if not include_unconfirmed and not confirmed:
                    continue
                for vout_n, vo in enumerate(tx.get('vout') or []):
                    if vo.get('scriptpubkey_address') != address:
                        continue
                    key = (txid, int(vout_n))
                    if key not in seen:
                        seen.add(key)
                        candidates.append(key)

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
        tip = await self._tip_height(refresh=True)
        spend_tx = await self._get_tx(spend_txid)
        if spend_tx is None:
            logger.error(f"Cannot fetch spend tx {spend_txid}")
            return None

        confs, _ = self._confirmations_from_status(spend_tx.get('status'), tip)
        vin_list = spend_tx.get('vin') or []

        if vin_index is None:
            for i, v in enumerate(vin_list):
                if v.get('txid') == funding_txid and v.get('vout') == funding_vout:
                    vin_index = i
                    break
        if vin_index is None or vin_index >= len(vin_list):
            return None

        v = vin_list[vin_index]
        script_sig = v.get('scriptsig') or ''
        witness = v.get('witness') or []

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
        tip = await self._tip_height(refresh=True)
        tx = await self._get_tx(txid)
        if tx is None:
            return None
        confs, _ = self._confirmations_from_status(tx.get('status'), tip)
        return confs

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
                logger.info('[BTC/mempool] Detected refund')
                return True
            if hashlib.sha256(test_secret).digest() == secret_hash:
                logger.info(f"[BTC/mempool] Extracted secret: {test_secret.hex()}")
                return test_secret

            if not use_loop:
                return None
            await asyncio.sleep(poll_interval)

    async def estimate_send_fee(self, transaction_size):
        """transaction_size is vbytes. Returns (fee_sats, feerate_sat_vbyte)."""
        try:
            data = await self._get('/v1/fees/recommended')
        except Exception as e:
            logger.debug(f'fees/recommended failed: {e}')
            return 0, 0
        sat_vbyte = float((data or {}).get('halfHourFee') or 0)
        fee_sats = math.ceil(sat_vbyte * transaction_size) if transaction_size else 0
        return int(fee_sats), sat_vbyte
