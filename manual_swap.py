
import os
import sys
import logging

from dotenv import load_dotenv

from swapper.atomic_swap import AtomicSwap

load_dotenv()
logging.basicConfig(level=logging.INFO)


SENDER_ADDRESS = ''
RECEIVER_ADDRESS = ''
SENDER_PRIVATE_KEY = None  # bytes.fromhex('')
RECEIVER_PRIVATE_KEY = None  # bytes.fromhex('')

INVOICE = ''

if not (invoice := INVOICE):
    invoice = input('Insert lightning invoice: ').strip()
if not (sender_address := SENDER_ADDRESS):
    sender_address = input('Insert sender p2pk address: ').strip()
if not (receiver_address := RECEIVER_ADDRESS):
    receiver_address = input('Insert receiver p2pk address: ').strip()

output_address = input('Insert p2pk address for sending redeem/refund tx: ').strip()

# check network consistency
network_prefix = os.getenv('KAS_NETWORK_PREFIX', 'kaspa')
sender_prefix = sender_address.split(':')[0]
receiver_prefix = receiver_address.split(':')[0]
if sender_prefix != network_prefix or receiver_prefix != network_prefix:
    print(f"ERROR: provided addresses do not match network prefix [{network_prefix}]")
    sys.exit(1)

swap = AtomicSwap(
    kas_rpc_server=os.getenv('KAS_RPC_SERVER', '127.0.0.1'),
    ln_rpc_server=os.getenv('LN_RPC_SERVER', ''),
    invoice=invoice,
    sender_address=sender_address,
    sender_private_key=SENDER_PRIVATE_KEY,
    receiver_address=receiver_address,
    receiver_private_key=RECEIVER_PRIVATE_KEY,
    output_address=output_address
)

swap.decode_ln_invoice()
swap.gen_contract_address()
utxo_sum = swap.check_utxo()
print(f"Found UTXOs totaling {utxo_sum} KAS")
secret = input('Insert secret (in hex format) to redeem or press Enter to refund: ').strip()
secret = bytes.fromhex(secret) if secret else None
result = swap.spend_contract(secret)
print(result)
