
import logging

from satkas.klib.kbech32 import decode_address
from satkas.klib.script_builder import ScriptBuilder
from satkas.klib.kdatatype import *
from satkas.klib.kopcodes import *
from satkas.klib.ksign import raw_tx_in_signature
from satkas.klib.kgrpc import getUtxosByAddresses


logger = logging.getLogger('ktransactions')


def select_utxos(address, amount=0, fee=0):
    # fetch available UTXOs
    utxos = getUtxosByAddresses(address)
    if not utxos:
        return [], 0
    selected_utxos = []
    selected_amount = 0
    if amount:
        # select enough utxos to cover requested amount
        for utxo in utxos:
            selected_utxos.append(utxo)
            sompi_amt = int(utxo['utxoEntry']['amount'])
            selected_amount += sompi_amt
            if selected_amount >= amount + fee:
                break
    else:
        # send all
        selected_utxos += utxos
    logger.info(f"Selected amount: {selected_amount / 1e8} KAS\nSelected utxos:\n{selected_utxos}")
    return selected_utxos, selected_amount


def gen_input(utxo):
    # outpoint
    _outpoint = utxo['outpoint']
    outpoint = OutPoint(
        _outpoint['transactionId'],
        _outpoint['index']
    )
    # utxo entry
    _utxo_entry = utxo['utxoEntry']
    utxo_entry = UtxoEntry(
        int(_utxo_entry['amount']),
        ScriptPublicKey(
            _utxo_entry['scriptPublicKey']['version'],
            bytes.fromhex(_utxo_entry['scriptPublicKey']['scriptPublicKey'])
        ),
        _utxo_entry['blockDaaScore'],
        _utxo_entry['isCoinbase']
    )
    return Input(outpoint, utxo_entry)


def gen_output(address, amount):
    # detect destination address type
    prefix, payload, version = decode_address(address)
    payload = bytes(payload)
    hex_payload = bytes(payload).hex()
    match version:
        case 0:
            logger.debug(f"Destination detected as P2PK: {hex_payload}")
            output_spk = ScriptPublicKey(0, OP_DATA32 + payload + OP_CHECK_SIG)
        case 1:
            logger.debug(f"Destination detected as ECDSA: {hex_payload}")
            output_spk = ScriptPublicKey(0, OP_DATA33 + payload + OP_CHECK_SIG_ECDSA)
        case 8:
            logger.debug(f"Destination detected as P2SH: {hex_payload}")
            output_spk = ScriptPublicKey(0, OP_BLAKE2B + OP_DATA32 + payload + OP_EQUAL)
        case _:
            logger.debug(f"Unknown address version: {version}")
            return False
    return Output(amount, output_spk)


def pay_from_address(sender_address, receiver_address, amount=0, fee=0, payload=None):
    # simple function that spends utxo(s) from sender address to receiver address
    # change is sent back to sender address
    # returns an unsigned transaction
    sompi_amount = int(amount * 1e8)
    if not fee:
        # network fee defaults to 10k sompi, or 0.0001 KAS
        fee = 10000
    # select outpoint and utxo_entry
    selected_utxos, selected_amount = select_utxos(sender_address, sompi_amount, fee)

    # Craft transaction input
    tx_inputs = []
    for utxo in selected_utxos:
        tx_input = gen_input(utxo)
        tx_inputs.append(tx_input)

    # Craft transaction output
    tx_outputs = []
    tx_output = gen_output(receiver_address, sompi_amount)
    tx_outputs.append(tx_output)
    # handle change
    if sompi_amount + fee < selected_amount:
        change_amount = selected_amount - sompi_amount - fee
        # send the leftover to sender address
        change_output = gen_output(sender_address, change_amount)
        tx_outputs.append(change_output)

    # Craft transaction
    tx = Transaction(tx_inputs, tx_outputs)
    # add payload
    if payload is not None:
        tx.payload = payload
    return tx


def sign_p2pk_with_key(tx, privkey, input_index=None):
    rv = SighashReusedValues()
    hashtype = SigHashType(1)
    if input_index is None:
        inputs_to_sign = range(len(tx.inputs))
    else:
        inputs_to_sign = [input_index]
    for i in inputs_to_sign:
        signature = raw_tx_in_signature(tx, i, hashtype, privkey, rv)
        builder = ScriptBuilder()
        builder.add_raw_data(signature)
        signature_script = builder.script
        tx.inputs[i].signature_script = signature_script
    return tx
