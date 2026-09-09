import asyncio
import json
import logging
import math
import os
import shutil

from bolt11.decode import decode as bolt11_decode
from satkas.core.services.base_service import BaseService, PaymentStatus

logger = logging.getLogger('lncli')


class LncliService(BaseService):
    """
    Lightning wallet service backed by the local lncli binary (LND).
    """
    service_icon = "lightning-bolt"
    icon_style = "lightning"

    default_lncli = 'lncli'
    default_rpc_server = '127.0.0.1:10009'
    default_invoice_expiry = 900

    def __init__(self):
        super().__init__()
        self.service_name = 'LNCLI (LND)'
        self.service_status_string = ''
        self.is_enabled = False
        self.is_detected = False
        self.update_status_task = None

        self.configs = {
            'lncli_bin': {
                'attr': 'lncli_bin',
                'default': self.default_lncli,
                'fallback': self.default_lncli,
                'type': str,
                'env': 'LNCLI',
            },
            'rpc_server': {
                'attr': 'rpc_server',
                'default': self.default_rpc_server,
                'fallback': self.default_rpc_server,
                'type': str,
                'env': 'LN_RPC_SERVER',
            },
            'invoice_expiry': {
                'attr': 'invoice_expiry',
                'default': self.default_invoice_expiry,
                'fallback': self.default_invoice_expiry,
                'type': int,
                'env': 'LNCLI_INVOICE_EXPIRY',
            },
        }
        self.load_config()
        self._notify_change(['service_name', 'service_status_string', 'is_enabled', 'is_detected'])

    def parse_config_string(self, text):
        text = (text or '').strip()
        if not text:
            self.rpc_server = self.default_rpc_server
            return
        self.rpc_server = text

    @property
    def config_string(self):
        return getattr(self, 'rpc_server', self.default_rpc_server) or ''

    def _build_cmd(self, *args):
        lncli = getattr(self, 'lncli_bin', None) or self.default_lncli
        cmd = f"{lncli}"
        rpc_server = getattr(self, 'rpc_server', '') or ''
        if rpc_server:
            cmd += f" --rpcserver {rpc_server}"
        if args:
            cmd += ' ' + ' '.join(str(a) for a in args)
        return cmd

    @staticmethod
    def _bolt11(invoice):
        text = str(invoice or '').strip()
        if text.lower().startswith('lightning:'):
            text = text[10:]
        return text if text.isalnum() else ''

    async def _run_lncli(self, *args, timeout=None):
        """Run lncli and return (stdout, stderr, returncode).

        timeout is for the streaming subcommands: `trackpayment` follows a
        payment until it reaches a terminal state, so without a deadline a
        payment still in flight would hang the caller forever. On expiry the
        process is killed and whatever it printed so far is returned, which is
        exactly what we want - the first update already carries the status.
        """
        cmd = self._build_cmd(*args)
        logger.debug(f"Executing {cmd}")
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if timeout is None:
            stdout, stderr = await proc.communicate()
            return stdout, stderr, proc.returncode
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            logger.debug(f"Timed out after {timeout}s, killing: {cmd}")
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            # communicate() again drains the pipes of the killed process, so
            # a partial stream is still readable instead of being discarded.
            try:
                stdout, stderr = await proc.communicate()
            except Exception:
                stdout, stderr = b'', b''
        return stdout, stderr, proc.returncode

    async def detect(self):
        lncli = getattr(self, 'lncli_bin', None) or self.default_lncli
        if not shutil.which(lncli) and not os.path.isfile(lncli):
            self.is_detected = False
            self._notify_change(['is_detected'])
            return self.is_detected
        try:
            stdout, stderr, code = await self._run_lncli('getinfo')
            if code == 0 and stdout:
                json.loads(stdout.decode())
                self.is_detected = True
            else:
                self.is_detected = False
                logger.debug(f"lncli detect stderr: {stderr.decode()}")
        except Exception as e:
            self.is_detected = False
            logger.debug(f"lncli detect failed: {e}")
        self._notify_change(['is_detected'])
        return self.is_detected

    async def validate(self):
        return self.is_detected

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
                    info = await self.get_node_info()
                    alias = (info or {}).get('alias', '')
                    pubkey = (info or {}).get('identity_pubkey', '')
                    short_pk = f"{pubkey[:8]}...{pubkey[-8:]}" if pubkey and len(pubkey) > 16 else pubkey
                    balance = await self.get_balance()
                    if info:
                        status_string = f"Alias: {alias}\nPubkey: {short_pk}\nBalance: {balance} sats"
                    else:
                        status_string = 'LNCLI ready'
                except Exception:
                    status_string = 'Error getting LN node info'
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

    async def create_invoice(self, amount=0, memo=''):
        memo = memo or 'SatKas'
        expiry = int(getattr(self, 'invoice_expiry', None) or self.default_invoice_expiry)
        stdout, stderr, _code = await self._run_lncli(
            'addinvoice',
            f'--amt {int(amount)}',
            f'--expiry {expiry}',
            f'--memo {memo}',
        )
        try:
            out_data = json.loads(stdout.decode())
        except json.JSONDecodeError:
            raise Exception(stderr.decode() if stderr else 'lncli addinvoice failed')
        return out_data.get('payment_request')

    async def pay_invoice(self, invoice):
        invoice = self._bolt11(invoice)
        if not invoice:
            return False
        stdout, stderr, _code = await self._run_lncli(
            'payinvoice',
            invoice,
            '--json',
            '--force',
        )
        logger.debug(f"stdout: {stdout.decode()}")
        logger.debug(f"stderr: {stderr.decode()}")
        try:
            data = json.loads(stdout.decode())
            # Normalize to include both key names used by callers
            preimage = data.get('payment_preimage') or data.get('preimage')
            if preimage and 'payment_preimage' not in data:
                data['payment_preimage'] = preimage
            if preimage and 'preimage' not in data:
                data['preimage'] = preimage
            return data
        except Exception as e:
            err = stderr.decode() if stderr else ''
            if 'AlreadyExists desc = payment is in transition' in err:
                pass
            elif 'AlreadyExists desc = invoice is already paid' in err:
                pass
            else:
                logger.error(err)
                logger.error(e, exc_info=True)
            return False

    async def check_invoice(self, payment_hash):
        try:
            payment_hash = bytes.fromhex(str(payment_hash).strip()).hex()
        except (ValueError, TypeError, AttributeError):
            return None
        if len(payment_hash) != 64:
            return None
        stdout, stderr, _code = await self._run_lncli('lookupinvoice', payment_hash)
        try:
            return json.loads(stdout.decode())
        except json.JSONDecodeError:
            logger.error(f"lookupinvoice failed: {stderr.decode()}")
            return None

    # --- Outgoing payments ---
    #
    # Deliberately separate from check_invoice above, which asks lookupinvoice
    # and so only knows about invoices this node issued. A node running both
    # sides of a swap - the regtest setup - holds the same hash as both an
    # invoice and a payment, so folding one into the other would answer with
    # whichever the fallback order happened to favour.

    @staticmethod
    def _json_stream(text):
        """Every complete JSON object in a concatenated stream, in order.

        `trackpayment --json` prints one object per update rather than one
        document, so json.loads() on the whole output fails.
        """
        decoder = json.JSONDecoder()
        objects = []
        idx, end = 0, len(text)
        while idx < end:
            while idx < end and text[idx].isspace():
                idx += 1
            if idx >= end:
                break
            try:
                obj, idx = decoder.raw_decode(text, idx)
            except ValueError:
                break
            objects.append(obj)
        return objects

    @staticmethod
    def _payment_from_lnd(payment):
        """An lnrpc.Payment, as status plus preimage."""
        raw = payment.get('status')
        # lncli renders the enum by name, but take the wire numbers too.
        by_name = {
            'SUCCEEDED': PaymentStatus.SETTLED,
            'IN_FLIGHT': PaymentStatus.IN_FLIGHT,
            'INITIATED': PaymentStatus.IN_FLIGHT,
            'FAILED': PaymentStatus.FAILED,
            'UNKNOWN': PaymentStatus.UNKNOWN,
        }
        by_number = {
            0: PaymentStatus.UNKNOWN,
            1: PaymentStatus.IN_FLIGHT,
            2: PaymentStatus.SETTLED,
            3: PaymentStatus.FAILED,
            4: PaymentStatus.IN_FLIGHT,
        }
        if isinstance(raw, str):
            status = by_name.get(raw.upper(), PaymentStatus.UNKNOWN)
        else:
            status = by_number.get(raw, PaymentStatus.UNKNOWN)
        preimage = payment.get('payment_preimage') or payment.get('preimage')
        # LND pads an unsettled payment's preimage with zeroes.
        if preimage and not preimage.strip('0'):
            preimage = None
        return {'status': status, 'preimage': preimage or None}

    async def check_payment(self, payment_hash, timeout=10):
        """Status of an outgoing payment, as a PaymentStatus and a preimage.

        trackpayment is LND's answer to "look this payment up by hash"; it
        streams until the payment reaches a terminal state, so it is bounded
        by timeout. Raise the timeout to wait for an in-flight payment to
        resolve, lower it to sample the current status.
        """
        try:
            payment_hash = bytes.fromhex(str(payment_hash).strip()).hex()
        except (ValueError, TypeError, AttributeError):
            return {'status': PaymentStatus.UNKNOWN, 'preimage': None}
        if len(payment_hash) != 64:
            return {'status': PaymentStatus.UNKNOWN, 'preimage': None}
        stdout, stderr, _code = await self._run_lncli(
            'trackpayment', '--json', payment_hash, timeout=timeout,
        )
        updates = self._json_stream(stdout.decode(errors='replace'))
        if updates:
            return self._payment_from_lnd(updates[-1])

        err = (stderr.decode(errors='replace') if stderr else '').lower()
        # The node answered and has never heard of this hash. That is the one
        # reply that lets a caller write the payment off: nothing was sent.
        if 'not initiated' in err or 'not found' in err or 'notfound' in err:
            return {'status': PaymentStatus.FAILED, 'preimage': None}
        logger.debug(f"trackpayment gave nothing usable ({err.strip()}), trying listpayments")
        return await self._payment_from_listpayments(payment_hash)

    async def _payment_from_listpayments(self, payment_hash):
        """Fallback for lncli builds predating trackpayment's --json flag."""
        stdout, stderr, _code = await self._run_lncli(
            'listpayments', '--include_incomplete',
        )
        try:
            data = json.loads(stdout.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.error(f"listpayments failed: {stderr.decode(errors='replace')}")
            return {'status': PaymentStatus.UNKNOWN, 'preimage': None}
        for payment in data.get('payments') or []:
            if payment.get('payment_hash') == payment_hash:
                return self._payment_from_lnd(payment)
        # A node that listed its payments and did not include this one has
        # never sent it.
        return {'status': PaymentStatus.FAILED, 'preimage': None}

    async def get_node_info(self):
        stdout, stderr, _code = await self._run_lncli('getinfo')
        try:
            return json.loads(stdout.decode())
        except json.JSONDecodeError:
            logger.error(f"getinfo failed: {stderr.decode()}")
            return None

    async def get_balance(self):
        """Local channel balance in sats from `lncli channelbalance`."""
        stdout, stderr, _code = await self._run_lncli('channelbalance')
        try:
            data = json.loads(stdout.decode())
            return int(data.get('balance') or 0)
        except (json.JSONDecodeError, TypeError, ValueError):
            logger.error(f"channelbalance failed: {stderr.decode()}")
            return 0


    async def decode_invoice(self, invoice):
        invoice = self._bolt11(invoice)
        if not invoice:
            return None
        try:
            stdout, stderr, code = await self._run_lncli('decodepayreq', invoice)
            if code == 0 and stdout:
                data = json.loads(stdout.decode())
                num_msat = data.get('num_msat')
                if num_msat in (None, ''):
                    amount_msat = int(data.get('num_satoshis') or 0) * 1000
                else:
                    amount_msat = int(num_msat)
                return {
                    'amount_msat': amount_msat,
                    'date': int(data['timestamp']),
                    'expiry': int(data['expiry']),
                    'payment_hash': data['payment_hash'],
                    'description': data.get('description') or '',
                    'payee': data.get('destination'),
                }
            logger.debug(
                f"decodepayreq failed: {(stderr or b'').decode()}, falling back to bolt11"
            )
        except Exception as e:
            logger.debug(f"decodepayreq failed: {e}, falling back to bolt11")
        decoded = bolt11_decode(invoice)
        return {
            'amount_msat': decoded.amount_msat,
            'date': decoded.date,
            'expiry': decoded.expiry,
            'payment_hash': decoded.payment_hash,
            'description': decoded.description,
            'payee': decoded.payee,
        }

    async def validate_invoice(self, invoice, parsed_data=None):
        if parsed_data is None:
            parsed_data = await self.decode_invoice(invoice)
        return True

    async def estimate_route_fee(self, invoice=None, sat_amount=None, destination=None):
        """Returns routing fee in sats. Decode invoice on the caller; pass destination."""
        amt = int(sat_amount) if sat_amount else 0
        try:
            destination = bytes.fromhex(str(destination or '')).hex()
            if len(destination) != 66:
                destination = ''
        except (ValueError, TypeError):
            destination = ''
        if destination and amt > 0:
            stdout, stderr, code = await self._run_lncli(
                'estimateroutefee', '--dest', destination, '--amt', str(amt),
            )
            if code == 0 and stdout:
                try:
                    data = json.loads(stdout.decode())
                except json.JSONDecodeError:
                    data = None
                reason = (data or {}).get('failure_reason') or 'FAILURE_REASON_NONE'
                if data and reason in ('FAILURE_REASON_NONE', '', 0, '0'):
                    msat = int(data.get('routing_fee_msat') or 0)
                    return math.ceil(msat / 1000)
            logger.debug(f"estimateroutefee failed: {stderr.decode() if stderr else code}")
        if amt <= 0:
            return 0
        return max(2, math.ceil(0.01 * amt))
