
import asyncio
import hashlib
import logging
import os
import time
from typing import Iterable, Optional, Sequence, Union

from satkas.core.db.models import Setting
from satkas.core.services.base_service import BaseService
from satkas.core.services.kaspa_explorer_service import KaspaExplorerService
from satkas.core.klib.kgrpc import (
    getBlockDagInfo,
    getFeeEstimate,
    getUtxosByAddresses,
    getMempoolEntry,
    getVirtualChainFromBlock,
    getVirtualChainFromBlockV2,
    submitTransaction,
)

logger = logging.getLogger('kaspad_service')

VCHAIN_CHECKPOINT_SETTING = 'service.kaspad.vchain_checkpoint'


class KaspadService(BaseService):
    service_icon = "server-network"
    icon_style = "kaspa"
    default_host = '127.0.0.1'
    default_port = 16110
    fallback_host = 'kaspad.satkas.com'
    fallback_port = 16110
    default_tip_cache_ttl = 5.0
    default_min_daa_confirmations = 150
    default_vchain_confirm_buffer = 30

    def __init__(self, host=None, port=None):
        super().__init__()
        self.service_name = "Kaspad"
        self.service_status_string = ''
        self.is_enabled = False
        self.is_detected = False

        self.configs = {
            'host': {
                'attr': 'host',
                'default': self.default_host,
                'fallback': self.fallback_host,
                'type': str,
                'env': 'KASPAD_HOST'
            },
            'port': {
                'attr': 'port',
                'default': self.default_port,
                'fallback': self.fallback_port,
                'type': int,
                'env': 'KASPAD_PORT'
            },
            'min_daa_confirmations': {
                'attr': 'min_daa_confirmations',
                'default': self.default_min_daa_confirmations,
                'fallback': None,
                'type': int,
                'env': 'MIN_DAA_CONFIRMATIONS',
            },
        }

        if host is None and port is None:
            self.load_config()
        else:
            self.host = host if host else self.default_host
            self.port = port if port else self.default_port

        if not hasattr(self, 'min_daa_confirmations') or self.min_daa_confirmations is None:
            self.min_daa_confirmations = int(
                os.getenv('MIN_DAA_CONFIRMATIONS', self.default_min_daa_confirmations)
            )
        self.vchain_confirm_buffer = int(
            os.getenv('KAS_VCHAIN_CONFIRM_BUFFER', self.default_vchain_confirm_buffer)
        )

        self.update_status_task = None
        self._last_detect_error = None
        self._dag_info_cache = None
        self._daa_score_cache = None
        self._daa_score_cached_at = 0.0
        self._tip_cache_ttl = float(os.getenv('KAS_TIP_CACHE_TTL', self.default_tip_cache_ttl))
        # txid -> blockDaaScore of its outputs when first observed.
        self._tx_accepting_daa = {}
        # Settled VSPC floor for HTLC scans (memory + DB setting).
        self.vchain_checkpoint = Setting.get_value(VCHAIN_CHECKPOINT_SETTING, None)
        # Built on first use, for history this node has pruned away.
        self._explorer = None
        self._notify_change(['service_name', 'service_status_string', 'is_enabled', 'is_detected'])

    def parse_config_string(self, text):
        if ':' in text:
            self.host, port = text.split(':')
            self.port = int(port)
        elif text == '':
            self.host = self.default_host
            self.port = self.default_port
        else:
            self.host = text
            self.port = self.default_port

    @property
    def config_string(self):
        return f"{self.host}:{self.port}"

    def _rpc_server(self):
        return f"{self.host}:{self.port}"

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

    async def get_dag_info(self, force_refresh: bool = False) -> dict:
        if not force_refresh and self._tip_cache_valid() and self._dag_info_cache is not None:
            return self._dag_info_cache
        dag_info = getBlockDagInfo(rpc_server=self._rpc_server())
        self._store_tip_cache(dag_info)
        return dag_info

    async def get_daa_score(self, force_refresh: bool = False) -> int:
        if not force_refresh and self._tip_cache_valid():
            return self._daa_score_cache
        dag_info = await self.get_dag_info(force_refresh=True)
        return int(dag_info.get('virtualDaaScore', 0))

    async def get_utxos_by_addresses(
        self, addresses, min_utxo_size=None, max_utxo_count=None,
    ) -> list:
        if isinstance(addresses, str):
            addresses = [addresses]
        addresses = [a for a in (addresses or []) if a]
        if not addresses:
            return []
        res = getUtxosByAddresses(addresses, rpc_server=self._rpc_server())
        entries = res.get('entries', []) or []
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
        if isinstance(rpc_transaction, dict) and not rpc_transaction.get('inputs'):
            raise RuntimeError('submit_transaction refused: transaction has no inputs')
        out = submitTransaction(
            rpc_transaction,
            allow_orphan=allow_orphan,
            rpc_server=self._rpc_server(),
        )
        logger.debug(f"Submit transaction out: {out}")
        if not out.get('error'):
            return out['transactionId']
        # Sequence-lock race: retry once after a short delay (legacy AtomicSwap).
        weird_error = 'one of the transaction sequence locks conditions was not met'
        err = out.get('error') or {}
        if weird_error in (err.get('message') or ''):
            await asyncio.sleep(3)
            out = submitTransaction(
                rpc_transaction,
                allow_orphan=allow_orphan,
                rpc_server=self._rpc_server(),
            )
            if not out.get('error'):
                return out['transactionId']
        return False

    async def _tx_in_virtual_chain(self, txid, min_confirmations) -> bool:
        """True if txid is accepted with at least min_confirmations depth.

        Uses getVirtualChainFromBlock from the pruning point with
        minConfirmationCount so the node applies the confirmation buffer.
        See https://docs.kaspa.org/integrate/accepted-transactions
        """
        dag = await self.get_dag_info(force_refresh=True)
        start_hash = dag.get('pruningPointHash')
        if not start_hash:
            return False
        try:
            res = getVirtualChainFromBlock(
                start_hash,
                include_accepted_transaction_ids=True,
                min_confirmation_count=min_confirmations,
                rpc_server=self._rpc_server(),
            )
        except Exception:
            return False
        # Empty {} is a valid at-tip response from MessageToDict.
        err = (res or {}).get('error') or {}
        if err.get('message'):
            return False
        for batch in (res or {}).get('acceptedTransactionIds') or []:
            ids = batch.get('acceptedTransactionIds') or []
            if txid in ids:
                return True
        return False

    async def kas_tx_confirmed(self, txid, addresses=None, min_confirmations=None) -> bool:
        """Whether a kaspa tx is buried by min_daa_confirmations.

        Order of signals:
        1. Still in mempool → not confirmed.
        2. Live UTXOs of `txid` on `addresses` → cache their accepting DAA
           (so a later spend of those outs still measures depth).
        3. Cached accepting DAA from (2).
        4. Fallback: getVirtualChainFromBlock with minConfirmationCount —
           works when outs were spent before we ever saw them.
        """
        if not txid:
            return False
        if min_confirmations is None:
            min_confirmations = self.min_daa_confirmations
        try:
            mempool = getMempoolEntry(txid, rpc_server=self._rpc_server())
            # Present in mempool → not yet accepted deeply enough.
            if mempool and not mempool.get('error'):
                return False
        except Exception:
            pass

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

        if accepting_daa is not None:
            tip = await self.get_daa_score()
            confirmed = accepting_daa + min_confirmations <= tip
            if confirmed:
                self._tx_accepting_daa.pop(txid, None)
            return confirmed

        # Not in mempool, never saw outs — ask the node via virtual chain.
        return await self._tx_in_virtual_chain(txid, min_confirmations)

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

    async def _scan_vchain_for_htlc_secret(
        self,
        secret_hash: bytes,
        *,
        address: Optional[str] = None,
        funding_txids: Optional[Sequence[str]] = None,
        contract_script=None,
        pubkeys: Optional[Sequence[bytes]] = None,
        start_hash: Optional[str] = None,
        progress=None,
    ) -> Optional[Union[bytes, bool]]:
        """Walk V2 FULL batches from start_hash, or the pruning point if none."""
        scan_at = start_hash
        from_pruning = False
        if not scan_at:
            dag = await self.get_dag_info(force_refresh=True)
            scan_at = dag.get('pruningPointHash')
            from_pruning = True
        if not scan_at:
            logger.warning('No virtual-chain start hash available for HTLC scan')
            return None
        logger.info(f"HTLC vchain scan from {'pruning point' if from_pruning else 'checkpoint'} {scan_at[:16]}...")

        batch = 0
        while True:
            try:
                res = await asyncio.to_thread(
                    getVirtualChainFromBlockV2,
                    scan_at,
                    'FULL',
                    0,
                    rpc_server=self._rpc_server(),
                )
            except Exception as e:
                logger.warning(f'getVirtualChainFromBlockV2 failed: {e}')
                res, err_msg = None, str(e)
            else:
                err_msg = ((res or {}).get('error') or {}).get('message')
            if err_msg:
                # Kaspa prunes hard, so a spend older than the pruning point is
                # simply gone from this node: only an index still has it. This
                # cannot fire on a normal "not spent yet" poll, which answers
                # None without an error.
                logger.warning(f'getVirtualChainFromBlockV2 error: {err_msg}')
                if self._explorer is None:
                    self._explorer = KaspaExplorerService()
                logger.info(f'Asking {self._explorer.base_url} for pruned HTLC history')
                try:
                    return await self._explorer.check_htlc_spend(
                        address,
                        secret_hash,
                        funding_txids=funding_txids,
                        contract_script=contract_script,
                        pubkeys=pubkeys,
                        onetime=True,
                    )
                except Exception as e:
                    logger.warning(f'Explorer fallback failed: {e}')
                    return None
            res = res or {}

            for block in res.get('chainBlockAcceptedTransactions') or []:
                for tx in block.get('acceptedTransactions') or []:
                    result = self._extract_secret_from_inputs(
                        tx.get('inputs') or [],
                        secret_hash,
                        funding_txids=funding_txids,
                        contract_script=contract_script,
                        pubkeys=pubkeys,
                    )
                    if result is not None:
                        return result

            added = res.get('addedChainBlockHashes') or []
            if not added:
                return None
            scan_at = added[-1]
            if progress is not None:
                progress['hash'] = scan_at
            if batch == 0 or batch % 20 == 19:
                logger.info(f'HTLC vchain batch {batch + 1}: added={len(added)}')
            batch += 1

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
        Detect Kaspa HTLC spend and extract the secret.
        Compatible with KaspaExplorerService.check_htlc_spend.
        Returns: secret bytes | True (refund) | None.
        """
        if isinstance(funding_txids, str):
            funding_txids = [funding_txids]
        funding_txids = [t for t in (funding_txids or []) if t] or None
        use_loop = timeout and not onetime
        seed_hash = start_hash

        while True:
            if address and await self._funding_still_unspent(address, funding_txids):
                if not use_loop:
                    return None
                await asyncio.sleep(poll_interval)
                continue

            result = await self._scan_vchain_for_htlc_secret(
                secret_hash,
                address=address,
                funding_txids=funding_txids,
                contract_script=contract_script,
                pubkeys=pubkeys,
                start_hash=seed_hash,
                progress=progress,
            )
            if progress and progress.get('hash'):
                seed_hash = progress['hash']
            if result is not None:
                if result is True:
                    logger.info('[KAS/kaspad] Detected refund')
                elif isinstance(result, bytes):
                    logger.info(f'[KAS/kaspad] Extracted secret: {result.hex()}')
                return result

            if not use_loop:
                return None
            await asyncio.sleep(poll_interval)

    async def estimate_send_fee(self, transaction_size):
        """transaction_size is mass in grams. Returns (fee_sompi, feerate_sompi_per_gram)."""
        try:
            res = getFeeEstimate(rpc_server=self._rpc_server())
        except Exception as e:
            logger.debug(f'getFeeEstimate failed: {e}')
            return 0, 0
        estimate = (res or {}).get('estimate') or {}
        feerate = float((estimate.get('priorityBucket') or {}).get('feerate') or 0)
        fee_sompi = int(round(feerate * transaction_size)) if transaction_size else 0
        return fee_sompi, feerate

    async def detect(self):
        self._last_detect_error = None
        try:
            res = getBlockDagInfo(rpc_server=f"{self.host}:{self.port}", timeout=10)
            self.is_detected = True
            self._store_tip_cache(res)
            tips = res.get('tipHashes') or []
            tip = (tips[0] if tips else None) or res.get('sink')
            if tip:
                self.vchain_checkpoint = tip
                Setting.set_value(VCHAIN_CHECKPOINT_SETTING, tip, 'str')
        except Exception as e:
            self.is_detected = False
            self._last_detect_error = str(e)
            details = getattr(e, 'details', None)
            if callable(details):
                try:
                    detail_msg = details()
                    if detail_msg:
                        self._last_detect_error = detail_msg
                except Exception:
                    pass
        self._notify_change(['is_detected'])
        return self.is_detected

    async def validate(self):
        return self.is_detected

    def enable(self):
        self.is_enabled = True
        os.environ['KAS_RPC_SERVER'] = f"{self.host}:{self.port}"
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

                    # Advance floor with V2 NONE + confirm buffer (settled midpoint).
                    start = self.vchain_checkpoint
                    if not start:
                        tips = dag_info.get('tipHashes') or []
                        start = (tips[0] if tips else None) or dag_info.get('sink') or dag_info.get('pruningPointHash')
                        if start:
                            self.vchain_checkpoint = start
                            Setting.set_value(VCHAIN_CHECKPOINT_SETTING, start, 'str')
                    else:
                        try:
                            v2 = getVirtualChainFromBlockV2(
                                start,
                                data_verbosity_level='NONE',
                                min_confirmation_count=self.vchain_confirm_buffer,
                                rpc_server=self._rpc_server(),
                            ) or {}
                            err = v2.get('error') or {}
                            if not err.get('message'):
                                removed = v2.get('removedChainBlockHashes') or []
                                added = v2.get('addedChainBlockHashes') or []
                                if start in removed:
                                    tips = dag_info.get('tipHashes') or []
                                    tip = (tips[0] if tips else None) or dag_info.get('sink')
                                    if tip:
                                        self.vchain_checkpoint = tip
                                        Setting.set_value(VCHAIN_CHECKPOINT_SETTING, tip, 'str')
                                elif added:
                                    self.vchain_checkpoint = added[-1]
                                    Setting.set_value(
                                        VCHAIN_CHECKPOINT_SETTING, added[-1], 'str'
                                    )
                        except Exception as e:
                            logger.debug(f'vchain floor advance failed: {e}')
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


if __name__ == '__main__':
    import asyncio
    asyncio.run(KaspadService().detect())
