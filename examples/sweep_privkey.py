
from sys import exit
import asyncio

from coincurve import PublicKeyXOnly

from satkas.core.klib.kaddress import p2pk_address
from satkas.core.klib.kgrpc import getUtxosByAddresses, submitTransaction
from satkas.core.klib.ktransactions import pay_from_address, sign_p2pk_with_key
from satkas.core.klib.serialization import gen_rpc_transaction


# insert private key in hex format and target address
PRIVATE_KEY = bytes.fromhex('')
TARGET_ADDRESS = 'kaspa:qr2y4cg72p09fhpwfs3dxudwz5duxlx774ejwvwgvr9yf5p4a8edzdrt50e8q'

# if SEND_ALL is True, all funds are sent to TARGET_ADDRESS
# to send a specific KAS amount, set SEND_ALL to False and set SEND_AMOUNT_KAS
# change is sent back to source address
SEND_ALL = True
SEND_AMOUNT_KAS = 1
NETWORK_FEE = 5000


# generate address from private key
x_only_pubkey = PublicKeyXOnly.from_secret(PRIVATE_KEY).format()
source_address = p2pk_address(x_only_pubkey)
print(f"Source address: {source_address}")

# fetch utxos
utxos = getUtxosByAddresses(source_address).get('entries', [])
print(f"Available utxos: {utxos}")
if not utxos:
    exit(1)

available_amount = sum([int(x['utxoEntry']['amount']) for x in utxos])
if SEND_ALL:
    dwork_amount = available_amount - NETWORK_FEE
else:
    dwork_amount = (SEND_AMOUNT_KAS * 1e8) - NETWORK_FEE
amount = dwork_amount / 1e8
print(f"Sending {amount} KAS to {TARGET_ADDRESS}")

# generate unsigned tx, sign it, serialize for json rpc
unsigned = asyncio.run(pay_from_address(source_address, TARGET_ADDRESS, amount=amount, fee=NETWORK_FEE))
signed = sign_p2pk_with_key(unsigned, PRIVATE_KEY)
rpc_tx = gen_rpc_transaction(signed)
print(f"Ready to broadcast transaction: {rpc_tx}")

# broadcast signed rpc transaction
txid = submitTransaction(rpc_tx)
print(txid)
