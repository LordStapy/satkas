
# Running this script requires the following environment variables:
# KAS_RPC_SERVER must point to a testnet node
# KAS_NETWORK_PREFIX must be set to "testnet"
# the script doesn't load the .env
# you should set these variables before running the script
# export KAS_RPC_SERVER=host:16210
# export KAS_NETWORK_PREFIX=testnet

import os
import sys

from satkas.klib.kbech32 import decode_address
from satkas.klib.scripting import (
    build_kip10_additive_borrower_script,
    build_kip10_borrower_spend_script,
    build_kip10_additive_threshold_script,
    build_kip10_threshold_spend_script
)
from satkas.klib.kaddress import get_script_hash, p2sh_address_from_script_hash
from satkas.klib.kgrpc import getUtxosByAddresses, submitTransaction
from satkas.klib.ktransactions import gen_input, gen_output, sign_p2pk_with_key
from satkas.klib.kdatatype import Transaction, SighashReusedValues, SigHashType
from satkas.klib.ksign import raw_tx_in_signature
from satkas.klib.serialization import gen_rpc_transaction


OWNER_PRIVKEY = bytes.fromhex('')
BORROWER_PRIVKEY = bytes.fromhex('')

OWNER_ADDRESS = 'kaspatest:qq8k273uwl4txhy08kxhhn6wu89r4trlnywsuw46ekchu0zauwe0wjpmx8p6s'
BORROWER_ADDRESS = 'kaspatest:qz8xewreet0w5zkw70arfn29dtzmzt2n8dhy96yktqztc4gx7zrru087r2ywj'

SPENDING_FEE = 10_000  # sompi
BORROWER_ADDED_SOMPI = 1000  # sompi that the borrower will add to kip-10 address
THRESHOLD = 1000  # sompi needed for threshold scenario

if os.environ['KAS_NETWORK_PREFIX'] != 'kaspatest':
    print('Error! KAS_NETWORK must be set to "kaspatest" for this script, KIP10 is not yet enabled on mainnet.')
    sys.exit(1)

owner_prefix, owner_payload, owner_version = decode_address(OWNER_ADDRESS)
owner_payload = bytes(owner_payload)
borrower_prefix, borrower_payload, borrower_version = decode_address(BORROWER_ADDRESS)
borrower_payload = bytes(borrower_payload)

match owner_version:
    case 0:
        owner_type = 'p2pk'
    case 1:
        owner_type = 'ecdsa'
    case 8:
        owner_type = 'p2sh'
    case _:
        owner_type = 'unknown'
match borrower_version:
    case 0:
        borrower_type = 'p2pk'
    case 1:
        borrower_type = 'ecdsa'
    case 8:
        borrower_type = 'p2sh'
    case _:
        borrower_type = 'unknown'

print(f"Owner:\nAddress: {OWNER_ADDRESS}\nPubkey: {owner_payload.hex()}\nType: {owner_type}\n")
print(f"Borrower:\nAddress: {BORROWER_ADDRESS}\nPubkey: {borrower_payload.hex()}\nType: {borrower_type}\n")

if not owner_type == 'p2pk' and not borrower_type == 'p2pk':
    print('Error, both owner and borrower address type must be p2pk.')
    sys.exit(1)

script_type = int(input('Select Kip-10 script type:\n'
                        '[1] Borrower secret (only selected borrower can add funds)\n'
                        '[2] Threshold (everyone can be the borrower and add funds with a threshold)\n'
                        'Your choice: ').strip())
if script_type == 1:
    print('Selected borrower secret.\n')
    additive_script = build_kip10_additive_borrower_script(owner_payload, borrower_payload)
elif script_type == 2:
    print('Selected threshold.\n')
    additive_script = build_kip10_additive_threshold_script(owner_payload, THRESHOLD)
else:
    sys.exit(1)

print(f"Generated additive script: {additive_script.hex()}")
additive_script_hash = get_script_hash(additive_script)
print(f"Generated script hash: {additive_script_hash.hex()}")
additive_address = p2sh_address_from_script_hash(additive_script_hash)
print(f"P2SH address: {additive_address}\n")

if not (OWNER_PRIVKEY or BORROWER_PRIVKEY):
    print('Fill either OWNER_PRIVKEY or BORROWER_PRIVKEY to test spending functionality.')
    sys.exit(0)

additive_address_utxos = getUtxosByAddresses(additive_address)
if not additive_address_utxos:
    print('Additive address is empty, send some funds and run again the script to test spending')
    sys.exit(1)

num_utxos = len(additive_address_utxos)
funded_amount_sompi = sum([int(u['utxoEntry']['amount']) for u in additive_address_utxos])
funded_amount_kaspa = funded_amount_sompi / 1e8
print(f"Additive address is funded with {num_utxos} utxo(s), totaling {funded_amount_kaspa} KAS\n")

spending_scenario = int(input('Select spending scenario:\n'
                              '[1] Owner sweeps all funds\n'
                              '[2] Borrower adds funds\n'
                              'Your choice: ').strip())

# Owner sweeps funds
if spending_scenario == 1:
    if not OWNER_PRIVKEY:
        print('Fill OWNER_PRIVKEY first!')
        sys.exit(1)
    # generate input from all p2sh utxos
    p2sh_inputs = [gen_input(utxo) for utxo in additive_address_utxos]
    tx_inputs = []
    for p2sh_input in p2sh_inputs:
        if script_type == 1:
            # set sig_op_count to 2 for borrower secret scenario
            p2sh_input.sig_op_count = b'\x02'
        tx_inputs.append(p2sh_input)
    # withdraw to own address
    amount = funded_amount_sompi - SPENDING_FEE
    p2pk_output = gen_output(OWNER_ADDRESS, amount)
    tx_outputs = [p2pk_output]

    tx = Transaction(tx_inputs, tx_outputs)

    # sign all transaction inputs
    rv = SighashReusedValues()
    hashtype = SigHashType(1)
    for i in range(len(tx.inputs)):
        signature = raw_tx_in_signature(tx, i, hashtype, OWNER_PRIVKEY, rv)
        if script_type == 1:
            signature_script = build_kip10_borrower_spend_script(signature, additive_script)
        elif script_type == 2:
            signature_script = build_kip10_threshold_spend_script(additive_script, signature=signature)
        else:
            sys.exit(1)
        tx.inputs[i].signature_script = signature_script

    # generate rpc transaction and broadcast
    rpc_tx = gen_rpc_transaction(tx)
    print(f"RPC Transaction: {rpc_tx}")
    res = submitTransaction(rpc_tx)
    print(f"Owner spending result: {res}")

# Borrower adds funds
elif spending_scenario == 2:
    if not BORROWER_PRIVKEY:
        print('Fill BORROWER_PRIVKEY first!')
        sys.exit(1)
    # generate input from one p2sh utxo
    p2sh_utxo = additive_address_utxos[0]
    p2sh_input = gen_input(p2sh_utxo)
    if script_type == 1:
        # set sig_op_count to 2 only for secret borrower scenario
        p2sh_input.sig_op_count = b'\x02'
    # for borrower scenario, we need to supply another input
    p2pk_utxos = getUtxosByAddresses(BORROWER_ADDRESS)
    if not p2pk_utxos:
        print('Borrower doesn\'t have any utxo available, fund it first.')
        sys.exit(1)
    p2pk_utxo = p2pk_utxos[0]
    p2pk_input = gen_input(p2pk_utxos[0])
    # generate tx inputs, order matters!!
    tx_inputs = [p2sh_input, p2pk_input]

    # output and change handling
    # add BORROWER_ADDED_SOMPI to p2sh output
    p2sh_out_amount = int(p2sh_utxo['utxoEntry']['amount']) + BORROWER_ADDED_SOMPI
    p2sh_output = gen_output(additive_address, p2sh_out_amount)
    # decrease p2pk by BORROWER_ADDED_SOMPI and SPENDING_FEE
    p2pk_out_amount = int(p2pk_utxo['utxoEntry']['amount']) - BORROWER_ADDED_SOMPI - SPENDING_FEE
    p2pk_output = gen_output(BORROWER_ADDRESS, p2pk_out_amount)
    # generate tx outputs, order matters!!
    tx_outputs = [p2sh_output, p2pk_output]

    tx = Transaction(tx_inputs, tx_outputs)

    # signatures
    rv = SighashReusedValues()
    hashtype = SigHashType(1)
    if script_type == 1:
        # sign input with index 0 (p2sh)
        signature_0 = raw_tx_in_signature(tx, 0, hashtype, BORROWER_PRIVKEY, rv)
        signature_script_0 = build_kip10_borrower_spend_script(
            signature_0,
            additive_script,
            is_owner=False,
            borrower_pubkey=borrower_payload)
    elif script_type == 2:
        # in threshold scenario borrower does not provide a signature for p2sh input
        signature_script_0 = build_kip10_threshold_spend_script(additive_script)
    else:
        sys.exit(1)
    tx.inputs[0].signature_script = signature_script_0
    # sign input with index 1
    signed_tx = sign_p2pk_with_key(tx, BORROWER_PRIVKEY, input_index=1)

    # generate rpc transaction and broadcast
    rpc_tx = gen_rpc_transaction(tx)
    print(f"RPC Transaction: {rpc_tx}")
    res = submitTransaction(rpc_tx)
    print(f"Borrower adding result: {res}")
