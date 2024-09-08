
import asyncio
import os
import json
import hashlib
import logging
from gc import collect as gc_collect

from getpass import getpass
from mnemonic import Mnemonic
from bip44 import Wallet
# from dotenv import load_dotenv

from klib.kaddress import p2pk_address
from klib.ksign import sign_hash, verify_signature

# load_dotenv()

DERIVATION_PATH = "m/44'/111111'/0'/"
logger = logging.getLogger('counterparty')
# logging.basicConfig(level=logging.INFO)


# ToDo: move methods to async
class Counterparty:
    def __init__(
            self,
            wallet_db_table=None,
            keep_unlocked=False,
            wallet_index=1,
            swap_endpoint=None
            ):
        self.swap_endpoint = swap_endpoint
        self.wallet_db_table = wallet_db_table
        if self.wallet_db_table is None:
            raise Exception('wallet_db_table not defined')
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
        # logger.info(f"address: {self.address}, db_address: {self.wallet.next_address}")
        assert self.wallet.next_address == self.address

        del wallet
        gc_collect()

    def init_wallet(self):
        logger.info('Wallet not initialized... Generating a new one!')
        logger.info('Enter password for wallet encryption (leave empty for unencrypted wallet)')
        passphrase = getpass('Password: ').strip()
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
            if self.passphrase is None:
                passphrase = getpass("Wallet password: ").strip()
                if keep_unlocked:
                    self.passphrase = passphrase
            else:
                passphrase = self.passphrase
        else:
            passphrase = ''
        return Wallet(self.wallet.mnemonic, passphrase=passphrase)

    def get_next_pubkey(self, wallet=None, n_key=0):
        if wallet is None:
            wallet = self.get_wallet()
        if n_key:
            wanted_key = n_key
        else:
            wanted_key = self.wallet.address_counter + 1
        pubkey = wallet.derive_public_key(f"{DERIVATION_PATH}{wanted_key}")[1:]
        assert len(pubkey) == 32
        return pubkey

    def get_next_address(self, wallet=None, n_key=0):
        pubkey = self.get_next_pubkey(wallet, n_key)
        return p2pk_address(pubkey)

    def get_secret_key(self, n_key=0):
        if n_key:
            wanted_key = n_key
        else:
            wanted_key = self.wallet.address_counter + 1

        return self.get_wallet().derive_secret_key(f"{DERIVATION_PATH}{wanted_key}")

    def update_address_counter(self, new_counter=0, keep_unlocked=True):
        wallet = self.get_wallet(keep_unlocked=True)
        if new_counter:
            self.wallet.address_counter = new_counter
        else:
            self.wallet.address_counter += 1
        self.pubkey = self.get_next_pubkey(wallet)
        self.address = p2pk_address(self.pubkey)
        self.wallet.next_address = self.get_next_address(wallet)
        assert self.wallet.next_address == self.address
        self.wallet.save()
        if not keep_unlocked:
            self.passphrase = None
        del wallet
        gc_collect()

    @staticmethod
    async def gen_ln_invoice(amount, lncli=None):
        if lncli is None:
            lncli = 'lncli'
        cmd = f"{lncli}"
        if ln_rpc_server := os.getenv('LN_RPC_SERVER', ''):
            cmd += f" --rpcserver {ln_rpc_server}"
        cmd += f" addinvoice --amt {amount} --expiry 300 --memo SatKas"
        logger.debug(f"Executing {cmd}")
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        # ToDo: handle errors and maybe find a way to avoid using lncli
        for i in range(10):
            stdout, stderr = await proc.communicate()
            try:
                out_data = json.loads(stdout.decode())
                break
            except json.JSONDecodeError:
                await asyncio.sleep(0.2)
        else:
            raise Exception(stderr)
        return out_data['payment_request']

    @staticmethod
    async def lncli_pay(invoice, lncli=None):
        if lncli is None:
            lncli = "lncli"
        if not invoice.strip().isalnum():
            return False
        cmd = f"{lncli}"
        if ln_rpc_server := os.getenv('LN_RPC_SERVER', ''):
            cmd += f" --rpcserver {ln_rpc_server}"
        cmd += f" payinvoice {invoice} --json --force"
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        # ToDo: handle slow response or slow payment (sometimes ln routes get trafficked)
        logger.debug(f"stdout: {stdout.decode()}")
        logger.debug(f"stderr: {stderr.decode()}")

        try:
            data = json.loads(stdout.decode())
            return data['payment_preimage']
        except Exception as e:
            stderr = stderr.decode()
            if "AlreadyExists desc = payment is in transition" in stderr:
                pass
            elif "AlreadyExists desc = invoice is already paid" in stderr:
                pass
            else:
                logger.error(stderr)
                logger.error(e, exc_info=True)
            return False

    def sign_message(self, msg_type, payload, node_key=False):
        msg_hash = self.get_msg_hash(msg_type, payload)
        # logger.debug(f"msg_hash = {msg_hash.hex()}")
        return self.sign(msg_hash, node_key=node_key)

    @staticmethod
    async def fund_contract_address(address, amount=0):
        logger.info(f"Funding {address} with {amount} KAS ")
        kaspawallet_bin = os.getenv('KASPAWALLET', None)
        if kaspawallet_bin is None:
            raise Exception('kaspawallet not defined')
        cmd = f"{kaspawallet_bin} send"
        if wallet_daemon := os.getenv('KASPAWALLET_DAEMON', ''):
            cmd += f" -d {wallet_daemon}"
        cmd += f" -t {address}"
        if wallet_password := os.getenv('KASPAWALLET_PASSWORD', ''):
            cmd += f" -p {wallet_password}"
        cmd += f" -v {amount + 0.001}"  # we include a 0.001 fee for redeem/refund tx
        if wallet_file := os.getenv('KASPAWALLET_KEY_FILE', ''):
            cmd += f" -f {wallet_file}"

        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        await asyncio.sleep(0.5)
        stdout, stderr = await proc.communicate()
        # ToDo: handle error or slow response
        logger.info(f"stdout: {stdout.decode().strip()}")
        logger.debug(f"stderr: {stderr.decode().strip()}")

    @staticmethod
    def get_msg_hash(msg_type, payload):
        payload_string = json.dumps(payload)
        # logger.debug(f"msg_type = {msg_type}")
        # logger.debug(f"payload_string = {payload_string}")
        msg_hash = hashlib.sha256(f"{msg_type}:{payload_string}".encode()).digest()
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
