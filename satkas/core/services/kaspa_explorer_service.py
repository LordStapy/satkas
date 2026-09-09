import asyncio
import hashlib
import logging
import os
import time
from typing import Any, Iterable, Optional, Sequence, Union

import aiohttp

from satkas.core.db.models import Setting
from satkas.core.services.base_service import BaseService

logger = logging.getLogger('kaspa_explorer_service')


class KaspaExplorerApiError(Exception):
    """Raised when a Kaspa explorer HTTP request fails."""

    def __init__(self, message, status=None, body=None):
        self.status = status
        self.body = body
        super().__init__(message)


class KaspaExplorerService(BaseService):
    """
    Kaspa chain monitoring / broadcast via public explorer REST API
    (api.kaspa.org or self-hosted). Method signatures match KaspadService.
    """
    service_icon = "web"
    icon_style = "kaspa"

    default_base_url = 'https://api.kaspa.org'
    default_tip_cache_ttl = 5.0
    default_min_daa_confirmations = 150

    def __init__(self, base_url=None):
        super().__init__()
        self.service_name = 'Kaspa Explorer'
        self.service_status_string = ''
        self.is_enabled = False
        self.is_detected = False

        self.configs = {
            'base_url': {
                'attr': 'base_url',
                'default': self.default_base_url,
                'fallback': None,
                'type': str,
                'env': 'KAS_EXPLORER_BASE_URL',
            },
            'min_daa_confirmations': {
                'attr': 'min_daa_confirmations',
                'default': self.default_min_daa_confirmations,
                'fallback': None,
                'type': int,
                'env': 'MIN_DAA_CONFIRMATIONS',
            },
        }

        if base_url is None:
            self.load_config()
            # Legacy host-only env (e.g. api-tn10.kaspa.org) from jumbo/atomic helpers.
            # Apply only when the new full-URL env and DB setting are both unset.
            if not os.getenv('KAS_EXPLORER_BASE_URL'):
                if Setting.get_value('service.kaspa_explorer.base_url') is None:
                    legacy = os.getenv('KAS_EXPLORER')
                    if legacy:
                        self.base_url = legacy
        else:
            self.base_url = base_url
            self.min_daa_confirmations = self.default_min_daa_confirmations

        self.base_url = self._normalize_base_url(
            getattr(self, 'base_url', None) or self.default_base_url
        )
        if not hasattr(self, 'min_daa_confirmations') or self.min_daa_confirmations is None:
            self.min_daa_confirmations = int(
                os.getenv('MIN_DAA_CONFIRMATIONS', self.default_min_daa_confirmations)
            )

        self.update_status_task = None
        self._last_detect_error = None
        self._dag_info_cache = None
        self._daa_score_cache = None
        self._daa_score_cached_at = 0.0
        self._tip_cache_ttl = float(os.getenv('KAS_TIP_CACHE_TTL', self.default_tip_cache_ttl))
        # txid -> accepting DAA when first observed (same role as KaspadService).
        self._tx_accepting_daa = {}
        self._notify_change(['service_name', 'service_status_string', 'is_enabled', 'is_detected'])

    @staticmethod
    def _normalize_base_url(url: str) -> str:
        text = str(url or '').strip().rstrip('/')
        if not text:
            text = KaspaExplorerService.default_base_url
        if not text.startswith(('http://', 'https://')):
            text = f'https://{text}'
        return text.rstrip('/')

    def parse_config_string(self, text):
        if text:
            self.base_url = self._normalize_base_url(text)

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
                    raise KaspaExplorerApiError(
                        f"HTTP {response.status} for {url}: {text[:200]}",
                        status=response.status,
                        body=text,
                    )
                content_type = response.headers.get('Content-Type', '')
                if 'application/json' in content_type or (text and text[:1] in ('{', '[')):
                    return await response.json(content_type=None)
                return text.strip() if text is not None else None

    async def _get(self, path: str, **kwargs) -> Any:
        return await self._request(path, 'GET', **kwargs)

    async def _post(self, path: str, json=None, **kwargs) -> Any:
        return await self._request(path, 'POST', json=json, **kwargs)

    # --- Tip cache ---

    def _tip_cache_valid(self) -> bool:
        if self._daa_score_cache is None:
            return False
        return (time.monotonic() - self._daa_score_cached_at) < self._tip_cache_ttl

    def _store_tip_cache(self, dag_info: dict) -> int:
        daa_score = int(dag_info.get('virtualDaaScore', 0))
        self._dag_info_cache = dag_info
        self._daa_score_cache = daa_score
        self._daa_score_cached_at = time.monotonic()
        return daa_score

    # --- Chain API (KaspadService-compatible) ---

    async def get_dag_info(self, force_refresh: bool = False) -> dict:
        if not force_refresh and self._tip_cache_valid() and self._dag_info_cache is not None:
            return self._dag_info_cache
        dag_info = await self._get('info/blockdag')
        if not isinstance(dag_info, dict):
            raise KaspaExplorerApiError('Unexpected blockdag response')
        self._store_tip_cache(dag_info)
        return dag_info

    async def get_daa_score(self, force_refresh: bool = False) -> int:
        if not force_refresh and self._tip_cache_valid():
            return self._daa_score_cache
        dag_info = await self.get_dag_info(force_refresh=True)
        return int(dag_info.get('virtualDaaScore', 0))

    @staticmethod
    def _normalize_utxo_entry(entry: dict) -> dict:
        """Ensure amount / blockDaaScore are strings like gRPC entries."""
        if not isinstance(entry, dict):
            return entry
        out = dict(entry)
        utxo = dict(out.get('utxoEntry') or {})
        if 'amount' in utxo and utxo['amount'] is not None:
            utxo['amount'] = str(utxo['amount'])
        if 'blockDaaScore' in utxo and utxo['blockDaaScore'] is not None:
            utxo['blockDaaScore'] = str(utxo['blockDaaScore'])
        spk = utxo.get('scriptPublicKey')
        if isinstance(spk, dict) and 'version' not in spk:
            spk = dict(spk)
            spk['version'] = 0
            utxo['scriptPublicKey'] = spk
        out['utxoEntry'] = utxo
        return out

    async def get_utxos_by_addresses(
        self, addresses, min_utxo_size=None, max_utxo_count=None,
    ) -> list:
        if isinstance(addresses, str):
            addresses = [addresses]
        addresses = [a for a in (addresses or []) if a]
        if not addresses:
            return []
        res = await self._post('addresses/utxos', json={'addresses': addresses})
        if not res:
            return []
        if not isinstance(res, list):
            raise KaspaExplorerApiError('Unexpected utxos response')
        entries = [self._normalize_utxo_entry(u) for u in res]
        if min_utxo_size is not None:
            entries = [
                u for u in entries
                if int((u.get('utxoEntry') or {}).get('amount') or 0) >= min_utxo_size
            ]
        if max_utxo_count is not None:
            entries.sort(
                key=lambda u: int((u.get('utxoEntry') or {}).get('amount') or 0),
                reverse=True,
            )
            entries = entries[:max_utxo_count]
        return entries

    async def submit_transaction(self, rpc_transaction, allow_orphan=True):
        """Broadcast a signed Kaspa transaction. Returns txid or False."""
        # gen_rpc_transaction is the inner SubmitTxModel, plus gas/payload
        # which this API's pydantic model does not define (dropped or 5xx
        # depending on extra=). POST a single {transaction, allowOrphan} object.
        if isinstance(rpc_transaction, list):
            rpc_transaction = rpc_transaction[0] if rpc_transaction else {}
        if isinstance(rpc_transaction, dict):
            rpc_transaction = {
                k: rpc_transaction[k]
                for k in ('version', 'inputs', 'outputs', 'lockTime', 'subnetworkId')
                if k in rpc_transaction
            }
            if not rpc_transaction.get('inputs'):
                raise RuntimeError('submit_transaction refused: transaction has no inputs')
        payload = {
            'transaction': rpc_transaction,
            'allowOrphan': bool(allow_orphan),
        }
        try:
            out = await self._post('transactions', json=payload)
        except KaspaExplorerApiError as e:
            weird_error = 'one of the transaction sequence locks conditions was not met'
            if e.body and weird_error in e.body:
                await asyncio.sleep(3)
                try:
                    out = await self._post('transactions', json=payload)
                except KaspaExplorerApiError:
                    return False
            else:
                logger.warning(f"Explorer submit_transaction failed: {e}")
                if e.body:
                    logger.warning(f"Explorer submit body: {e.body[:1000]}")
                return False
        if not isinstance(out, dict):
            return False
        if out.get('error'):
            weird_error = 'one of the transaction sequence locks conditions was not met'
            if weird_error in str(out.get('error')):
                await asyncio.sleep(3)
                try:
                    out = await self._post('transactions', json=payload)
                except KaspaExplorerApiError:
                    return False
                if isinstance(out, dict) and not out.get('error') and out.get('transactionId'):
                    return out['transactionId']
            return False
        return out.get('transactionId') or False

    async def _accepting_daa_from_tx(self, txid: str) -> Optional[int]:
        """Resolve accepting DAA via GET /transactions/{id} + block header."""
        try:
            tx = await self._get(f'transactions/{txid}', params={'inputs': 'false', 'outputs': 'false'})
        except KaspaExplorerApiError:
            return None
        if not isinstance(tx, dict):
            return None
        if tx.get('is_accepted') is False:
            return None
        block_hash = tx.get('accepting_block_hash')
        if not block_hash:
            return None
        try:
            block = await self._get(
                f'blocks/{block_hash}',
                params={'includeTransactions': 'false'},
            )
        except KaspaExplorerApiError:
            return None
        if not isinstance(block, dict):
            return None
        header = block.get('header') or {}
        daa = header.get('daaScore')
        if daa is None:
            return None
        return int(daa)

    async def kas_tx_confirmed(self, txid, addresses=None, min_confirmations=None) -> bool:
        """Whether a kaspa tx is buried by min_daa_confirmations.

        Order of signals (explorer-adapted):
        1. Live UTXOs of `txid` on `addresses` → cache accepting DAA.
        2. Cached accepting DAA from (1).
        3. Fallback: GET transaction accepting block → header daaScore.
        """
        if not txid:
            return False
        if min_confirmations is None:
            min_confirmations = self.min_daa_confirmations

        accepting_daa = self._tx_accepting_daa.get(txid)
        if isinstance(addresses, str):
            addresses = [addresses]
        addresses = [a for a in (addresses or []) if a]
        if addresses:
            utxos = await self.get_utxos_by_addresses(addresses)
            matching = [
                u for u in utxos
                if u.get('outpoint', {}).get('transactionId') == txid
            ]
            if matching:
                accepting_daa = max(
                    int(u['utxoEntry']['blockDaaScore']) for u in matching
                )
                self._tx_accepting_daa[txid] = accepting_daa

        if accepting_daa is None:
            accepting_daa = await self._accepting_daa_from_tx(txid)
            if accepting_daa is not None:
                self._tx_accepting_daa[txid] = accepting_daa

        if accepting_daa is None:
            return False

        tip = await self.get_daa_score()
        confirmed = accepting_daa + min_confirmations <= tip
        if confirmed:
            self._tx_accepting_daa.pop(txid, None)
        return confirmed

    @staticmethod
    def _normalize_contract_script(contract_script) -> Optional[bytes]:
        if contract_script is None:
            return None
        if isinstance(contract_script, bytes):
            return contract_script
        text = str(contract_script).strip()
        if text.startswith(('0x', '0X')):
            text = text[2:]
        return bytes.fromhex(text)

    @classmethod
    def _extract_secret_from_signature_script(
        cls,
        signature_script_hex: str,
        secret_hash: bytes,
        *,
        contract_script=None,
        pubkeys: Optional[Sequence[bytes]] = None,
    ) -> Optional[Union[bytes, bool]]:
        """
        Parse a Kaspa P2SH HTLC redeem signatureScript.

        Layout matches `build_spend_script` / AtomicSwap.monitor_kas_utxo:
          [len|sig(65)] [len|pubkey(32)] [OP_0 | len|secret(32)] [OP_1?] [contract...]

        Returns secret bytes, True (refund), or None.
        """
        if not signature_script_hex or not secret_hash:
            return None
        try:
            script = bytes.fromhex(signature_script_hex)
        except (ValueError, TypeError):
            return None

        contract = cls._normalize_contract_script(contract_script)
        min_len = 100 + (len(contract) if contract else 0)
        if len(script) < max(min_len, 100):
            return None

        pubkey = script[67:99]
        if pubkeys:
            wanted = [p for p in pubkeys if p]
            if wanted and pubkey not in wanted:
                return None

        push_or_op0 = script[99:100]
        if push_or_op0 == b'\x00':
            return True

        test_secret = script[100:132]
        if hashlib.sha256(test_secret).digest() == secret_hash:
            return test_secret
        return None

    @classmethod
    def _extract_secret_from_inputs(
        cls,
        inputs: Iterable[dict],
        secret_hash: bytes,
        *,
        funding_txids: Optional[Sequence[str]] = None,
        contract_script=None,
        pubkeys: Optional[Sequence[bytes]] = None,
    ) -> Optional[Union[bytes, bool]]:
        """Scan tx inputs for an HTLC spend (kaspad camelCase or explorer snake_case)."""
        funding = {t for t in (funding_txids or []) if t} or None
        for inp in inputs or []:
            if not isinstance(inp, dict):
                continue
            prev = inp.get('previousOutpoint') or {}
            prev_txid = (
                inp.get('previous_outpoint_hash')
                or prev.get('transactionId')
                or prev.get('transaction_id')
            )
            if funding is not None and prev_txid not in funding:
                continue
            sig = inp.get('signature_script') or inp.get('signatureScript') or ''
            result = cls._extract_secret_from_signature_script(
                sig,
                secret_hash,
                contract_script=contract_script,
                pubkeys=pubkeys,
            )
            if result is not None:
                return result
        return None

    async def _funding_still_unspent(self, address: str, funding_txids: Optional[Sequence[str]]) -> bool:
        utxos = await self.get_utxos_by_addresses(address)
        if not funding_txids:
            return bool(utxos)
        present = {
            u.get('outpoint', {}).get('transactionId')
            for u in utxos
            if u.get('outpoint', {}).get('transactionId')
        }
        return all(txid in present for txid in funding_txids if txid)

    async def _scan_address_txs_for_htlc_secret(
        self,
        address: str,
        secret_hash: bytes,
        *,
        funding_txids: Optional[Sequence[str]] = None,
        contract_script=None,
        pubkeys: Optional[Sequence[bytes]] = None,
        limit: int = 50,
    ) -> Optional[Union[bytes, bool]]:
        """Pull recent full transactions for address and parse redeem scripts."""
        if not address:
            return None
        path = f'addresses/{address}/full-transactions'
        try:
            response = await self._get(path, params={'limit': limit, 'fields': 'inputs'})
        except KaspaExplorerApiError as e:
            logger.debug(f'Explorer full-transactions failed: {e}')
            return None
        if not isinstance(response, list):
            return None
        for tx in response:
            inputs = (tx or {}).get('inputs') or []
            result = self._extract_secret_from_inputs(
                inputs,
                secret_hash,
                funding_txids=funding_txids,
                contract_script=contract_script,
                pubkeys=pubkeys,
            )
            if result is not None:
                return result
        return None

    async def check_htlc_spend(
        self,
        address: str,
        secret_hash: bytes,
        *,
        funding_txids: Optional[Sequence[str]] = None,
        contract_script=None,
        pubkeys: Optional[Sequence[bytes]] = None,
        start_hash: Optional[str] = None,
        timeout: bool = False,
        onetime: bool = False,
        poll_interval: float = 10,
        progress=None,
    ) -> Optional[Union[bytes, bool]]:
        """
        Detect Kaspa HTLC spend of contract `address` and extract the secret.

        Compatible with KaspadService.check_htlc_spend.
        `start_hash` / `progress` are accepted for signature parity (unused on explorer).

        Returns: secret bytes | True (refund) | None (pending / not found).
        """
        del start_hash, progress  # signature parity with KaspadService; explorer is indexed
        if isinstance(funding_txids, str):
            funding_txids = [funding_txids]
        funding_txids = [t for t in (funding_txids or []) if t] or None
        use_loop = timeout and not onetime

        while True:
            if address and await self._funding_still_unspent(address, funding_txids):
                if not use_loop:
                    return None
                await asyncio.sleep(poll_interval)
                continue

            result = await self._scan_address_txs_for_htlc_secret(
                address,
                secret_hash,
                funding_txids=funding_txids,
                contract_script=contract_script,
                pubkeys=pubkeys,
            )
            if result is not None:
                if result is True:
                    logger.info('[KAS/explorer] Detected refund')
                elif isinstance(result, bytes):
                    logger.info(f'[KAS/explorer] Extracted secret: {result.hex()}')
                return result

            if not use_loop:
                return None
            await asyncio.sleep(poll_interval)

    async def estimate_send_fee(self, transaction_size):
        """transaction_size is mass in grams. Returns (fee_sompi, feerate_sompi_per_gram)."""
        try:
            data = await self._get('info/fee-estimate')
        except Exception as e:
            logger.debug(f'fee-estimate failed: {e}')
            return 0, 0
        feerate = float(((data or {}).get('priorityBucket') or {}).get('feerate') or 0)
        fee_sompi = int(round(feerate * transaction_size)) if transaction_size else 0
        return fee_sompi, feerate

    # --- Lifecycle ---

    async def detect(self):
        self._last_detect_error = None
        try:
            dag = await self.get_dag_info(force_refresh=True)
            self.is_detected = bool(dag and dag.get('virtualDaaScore') is not None)
        except Exception as e:
            self.is_detected = False
            self._last_detect_error = str(e)
            logger.debug(f"Kaspa explorer detect failed: {e}")
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
                    dag_info = await self.get_dag_info(force_refresh=True)
                    network_name = dag_info.get('networkName', '')
                    daa_score = dag_info.get('virtualDaaScore', '')
                    status_string = f"Network: {network_name}\nDAA Score: {daa_score}"
                except Exception:
                    status_string = 'Error getting DAG info'
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
