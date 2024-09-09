
import asyncio
import os
import time
import json
import subprocess
import logging

from pprint import pformat
# from dotenv import load_dotenv

from klib.kbech32 import decode_address
from klib.kaddress import get_pubkey_hash, p2sh_address_from_script
from klib.scripting import (build_contract_script, build_spend_script,
                            build_contract_script_short, build_spend_script_short)
from klib.kdatatype import (Transaction, Input, Output,
                            OutPoint, UtxoEntry, ScriptPublicKey,
                            SighashReusedValues, SigHashType)
from klib.ksign import raw_tx_in_signature
from klib.serialization import gen_rpc_transaction

# load_dotenv()

logger = logging.getLogger('atomic_swap')
# logging.basicConfig(level=logging.DEBUG)
logging.getLogger('peewee').setLevel(logging.WARNING)
logging.getLogger('asyncio').setLevel(logging.WARNING)
logging.getLogger('aiohttp').setLevel(logging.WARNING)


# ToDo: switch some methods to async
# ToDo: move some methods to utils, keep the code cleaner
class AtomicSwap:
    def __init__(self, *args, **kwargs):
        # server stuff
        self.kas_rpc_server = kwargs.get('kas_rpc_server')
        self.ln_rpc_server = kwargs.get('ln_rpc_server')

        # LN stuff
        self.invoice = kwargs.get('invoice')

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

        self.output_address = kwargs.get('output_address')
        self.output_pubkey = bytes(decode_address(self.output_address)[1]) if self.output_address else None

        # swap stuff
        self.kas_amount = 0
        self.sat_amount = 0
        self.timelock = 0
        self.secret_hash = b''
        self.contract_script = b''
        self.contract_address = ''
        self.transaction = None
        self.utxos = []

    @staticmethod
    def run_cmd(cmd, shell=False):
        process = subprocess.Popen(cmd, shell=shell, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = process.communicate()
        return out, err

    def decode_ln_invoice(self, invoice=None):
        if invoice is None:
            invoice = self.invoice
        lncli = os.getenv('LNCLI', None)
        if not invoice.isalnum():
            return False
        if not lncli:
            return self.internal_ln_decode()
        cmd = f"{lncli}"
        if ln_rpc_server := os.getenv('LN_RPC_SERVER', ''):
            cmd += f" --rpcserver {ln_rpc_server}"
        cmd += f" decodepayreq {invoice}"
        cmd = cmd.split()
        out, err = self.run_cmd(cmd, shell=False)
        if out:
            try:
                out = json.loads(out.decode())
            except Exception as e:
                logger.error(e)
                return False
        else:
            logger.error(f"Error decoding invoice: {err.decode()}")
            return False
        self.sat_amount = int(out['num_satoshis'])
        self.timelock = (int(out['timestamp']) + (int(out['expiry']) * 1)) * 1000
        self.secret_hash = bytes.fromhex(out['payment_hash'])
        return out

    def internal_ln_decode(self):
        logger.debug('Using internal ln decoder')
        # if 'lightning-payencode-master' not in sys.path:
        #     sys.path.append('lightning-payencode-master')
        # from lnaddr import lndecode
        from bolt11.decode import decode as lndecode
        decoded = lndecode(self.invoice)
        date = decoded.date
        expiry = decoded.expiry
        secret_hash = decoded.payment_hash
        self.sat_amount = int(decoded.amount_msat / 1000)
        self.timelock = (int(date) + (int(expiry))) * 1000
        self.secret_hash = bytes.fromhex(secret_hash)

    def get_utxos_by_address(self, address):
        kaspactl = os.getenv('KASPACTL', 'kaspactl')
        cmd = f"{kaspactl} -a -s {self.kas_rpc_server} GetUtxosByAddresses '{address}'"
        out, err = self.run_cmd(cmd, shell=True)
        # logger.debug(out.decode(), err.decode())
        if err:
            logger.error(err.decode())
        if out:
            out = json.loads(out.decode())
            res = out['getUtxosByAddressesResponse']['entries']
        else:
            res = []
        return res

    def broadcast_transaction(self, rpc_transaction):
        kaspactl = os.getenv('KASPACTL', 'kaspactl')
        cmd = f"{kaspactl} -a -s {self.kas_rpc_server} SubmitTransaction '{rpc_transaction}' false"
        out, err = self.run_cmd(cmd, shell=True)
        try:
            out = json.loads(out.decode())
            if not out['submitTransactionResponse']['error']:
                res = out['submitTransactionResponse']['transactionId']
            else:
                # ToDo: handle as many errors as possible here
                logger.error('HEY!!!')
                logger.error(out['submitTransactionResponse']['error'])
                weird_error = 'one of the transaction sequence locks conditions was not met'
                if weird_error in out['submitTransactionResponse']['error']['message']:
                    logger.warning("Retrying broadcast in 3 seconds")
                    time.sleep(3)
                    return self.broadcast_transaction(rpc_transaction)
                res = False
        except json.JSONDecodeError:
            logger.error(err.decode())
            res = False
        return res

    def gen_contract_address(self):
        self.contract_script = build_contract_script(
            self.secret_hash,
            self.receiver_pkh,
            self.timelock,
            self.sender_pkh)

        self.contract_address = p2sh_address_from_script(self.contract_script)
        logger.info(f"Contract P2SH address: {self.contract_address}")

    def gen_short_contract_address(self):
        self.contract_script = build_contract_script_short(
            self.secret_hash,
            self.receiver_pubkey,
            self.timelock,
            self.sender_pubkey
        )
        self.contract_address = p2sh_address_from_script(self.contract_script)
        logger.info(f"Short contract P2SH address: {self.contract_address}")

    def check_utxo(self, address=None, min_amount=0, timeout=True):
        # Check if address has any utxo
        if address is None:
            address = self.contract_address
        self.utxos = self.get_utxos_by_address(address)
        if not self.utxos:
            amount_notified = 0
            logger.info(f"Address {address} has no utxo, awaiting funding...")
            while True:
                time.sleep(1)
                self.utxos = self.get_utxos_by_address(address)
                if self.utxos:
                    total = sum([int(utxo['utxoEntry']['amount']) for utxo in self.utxos]) / 1e8
                    if total >= min_amount:
                        break
                    elif total > amount_notified:
                        logger.debug(f"utxo detected, but amount is too low ({total} / {min_amount} KAS)\r")
                        amount_notified = total
                if timeout and self.timelock < time.time() * 1e3:
                    logger.info('\nInvoice expired, leaving')
                    return False
        total = sum([int(utxo['utxoEntry']['amount']) for utxo in self.utxos]) / 1e8
        # logger.debug(f"Found {len(self.utxos)} UTXOs totaling {total} KAS")
        return total

    async def async_check_utxo(self, address=None, min_amount=0, timeout=True):
        # Check if address has any utxo
        if address is None:
            address = self.contract_address
        self.utxos = self.get_utxos_by_address(address)
        if not self.utxos and timeout:
            amount_notified = 0
            logger.debug(f"Address {address} has no utxo, awaiting funding...")
            while True:
                await asyncio.sleep(1)
                self.utxos = self.get_utxos_by_address(address)
                if self.utxos:
                    total = sum([int(utxo['utxoEntry']['amount']) for utxo in self.utxos]) / 1e8
                    if total >= min_amount:
                        break
                    elif total > amount_notified:
                        logger.debug(f"utxo detected, but amount is too low ({total} / {min_amount} KAS)")
                        amount_notified = total
                if timeout and self.timelock < time.time() * 1e3:
                    logger.info('Invoice expired, leaving')
                    return False
        total = sum([int(utxo['utxoEntry']['amount']) for utxo in self.utxos]) / 1e8
        # logger.debug(f"Found {len(self.utxos)} UTXOs totaling {total} KAS")
        return total

    def spend_contract(self, secret=None, short_script=False):
        if secret is None:
            # refund path
            pubkey = self.sender_pubkey
            privkey = self.sender_private_key
            out_pubkey = self.output_pubkey if self.output_address else self.sender_pubkey
        else:
            pubkey = self.receiver_pubkey
            privkey = self.receiver_private_key
            out_pubkey = self.output_pubkey if self.output_address else self.receiver_pubkey

        # Spending
        # Generate input and store sum of amounts
        amounts_sum = 0
        tx_inputs = []
        for utxo in self.utxos:
            _outpoint = utxo['outpoint']
            outpoint = OutPoint(_outpoint['transactionId'], int(_outpoint['index']))
            _utxo_entry = utxo['utxoEntry']
            _script_public_key = _utxo_entry['scriptPublicKey']
            amount = int(_utxo_entry['amount'])
            utxo_entry = UtxoEntry(
                amount,
                ScriptPublicKey(
                    int(_script_public_key['version']),
                    bytes.fromhex(_script_public_key['scriptPublicKey'])
                ),
                int(_utxo_entry['blockDaaScore']),
                bool(_utxo_entry['isCoinbase'])
            )
            tx_input = Input(outpoint, utxo_entry, 1)
            tx_inputs.append(tx_input)
            amounts_sum += amount
        # Generate output
        tx_outputs = []
        fee = 100_000
        tx_output = Output(
            amounts_sum - fee,  # full amount - 0.001 KAS
            ScriptPublicKey(0, b'\x20' + out_pubkey + b'\xac')  # p2pk
        )
        tx_outputs.append(tx_output)

        # Generate transaction
        timelock = 0 if secret else self.timelock
        self.transaction = Transaction(tx_inputs, tx_outputs, 0, timelock)
        # Sign
        for i, txIn in enumerate(self.transaction.inputs):
            rv = SighashReusedValues()
            hashtype = SigHashType(1)
            signature = raw_tx_in_signature(self.transaction, i, hashtype, privkey, rv)
            if short_script:
                spend_builder = build_spend_script_short
            else:
                spend_builder = build_spend_script
            if secret is None:
                spend_contract_script = spend_builder(signature, pubkey, self.contract_script, refund=True)
            else:
                spend_contract_script = spend_builder(signature, pubkey, self.contract_script, secret=secret)
            self.transaction.inputs[i].signature_script = spend_contract_script
        # Print final tx and broadcast
        rpc_tx = gen_rpc_transaction(self.transaction)
        logger.debug(f"Finalized transaction, ready to broadcast:")
        logger.debug(pformat(json.loads(rpc_tx)))
        tx_id = self.broadcast_transaction(rpc_tx)
        if tx_id:
            logger.debug(f"txid: {tx_id}")
        return tx_id

