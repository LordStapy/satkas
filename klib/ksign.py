
import os
import hashlib
import struct
import logging

try:
    from coincurve import PrivateKey, PublicKeyXOnly
except ImportError:
    from .schnorr_signature import schnorr_sign, schnorr_verify

from .kdatatype import (SigHashType, OutPoint, ScriptPublicKey, UtxoEntry, Input,
                        Output, Transaction, Subnetworks, SighashReusedValues)

logger = logging.getLogger('ksign')

# Domain used to segregate hash calculations for transaction signatures
transactionHashDomain = "TransactionHash"
transactionIDDomain = "TransactionID"
transactionSigningDomain = "TransactionSigningHash"
transactionSigningECDSADomain = "TransactionSigningHashECDSA"
blockDomain = "BlockHash"
proofOfWorkDomain = "ProofOfWorkHash"
heavyHashDomain = "HeavyHash"
merkleBranchDomain = "MerkleBranchHash"


def new_transaction_signing_hash_writer() -> hashlib:
    key = bytes(transactionSigningDomain, 'utf-8')
    return hashlib.blake2b(key=key, digest_size=32)


def hash_data(hash_writer, data, dtype=None):
    # update the provided hash_writer with formatted data
    if dtype == 'bytes':
        hash_data(hash_writer, len(data), 'uint64')
    elif dtype == 'raw_bytes':
        pass
    elif dtype in ['int16', 'uint16']:
        data = struct.pack('<H', data)
    elif dtype in ['int32', 'uint32']:
        data = struct.pack('<I', data)
    elif dtype in ['int64', 'uint64']:
        data = struct.pack('<Q', data)
    elif dtype == 'uint8':
        data = struct.pack('<B', data)
    elif dtype == 'bool':
        data = struct.pack('?', data)
    elif dtype == 'domain_hash':
        pass
    elif dtype == 'domain_transaction_id':
        pass
    elif dtype == 'domain_subnetwork_id':
        _data = bytearray(20)
        _data[0] = data
        data = bytes(_data)
    elif dtype is None:
        if isinstance(data, str):
            data = bytes.fromhex(data)
    else:
        logger.warning('The fuck!?')
    hash_writer.update(data)


def get_previous_outputs_hash(tx: Transaction, hash_type: SigHashType, reused_values: SighashReusedValues) -> bytes:
    if hash_type.is_sig_hash_anyone_can_pay():
        return bytes(32)  # A zero hash, 32 bytes

    if reused_values.previous_outputs_hash is None:
        hash_writer = new_transaction_signing_hash_writer()
        for tx_input in tx.inputs:
            hash_data(hash_writer, tx_input.previous_outpoint.tx_id)
            hash_data(hash_writer, tx_input.previous_outpoint.index, 'uint32')
        reused_values.previous_outputs_hash = hash_writer.digest()

    return reused_values.previous_outputs_hash


def get_sequence_hash(tx, hash_type, reused_values):
    if hash_type.is_sig_hash_single() or hash_type.is_sig_hash_anyone_can_pay() or hash_type.is_sig_hash_none():
        return bytes(32)

    if reused_values.sequence_hash is None:
        hash_writer = new_transaction_signing_hash_writer()
        for tx_input in tx.inputs:
            hash_data(hash_writer, tx_input.sequence, 'uint64')
        reused_values.sequence_hash = hash_writer.digest()

    return reused_values.sequence_hash


def get_sig_op_count_hash(tx, hash_type, reused_values):
    if hash_type.is_sig_hash_anyone_can_pay():
        return bytes(32)

    if reused_values.sig_op_count_hash is None:
        hash_writer = new_transaction_signing_hash_writer()
        for tx_input in tx.inputs:
            sig_op_count = int.from_bytes(tx_input.sig_op_count, byteorder='little')
            hash_data(hash_writer, sig_op_count, 'uint8')
        reused_values.sig_op_count_hash = hash_writer.digest()

    return reused_values.sig_op_count_hash


def get_output_hash(tx, idx, hash_type, reused_values):
    if hash_type.is_sig_hash_none():
        return bytes(32)

    if hash_type.is_sig_hash_single():
        if idx > len(tx.outputs):
            return bytes(32)
        hash_writer = new_transaction_signing_hash_writer()
        tx_output = tx.outputs[idx]
        hash_data(hash_writer, tx_output.value, 'uint64')
        hash_data(hash_writer, tx_output.script_public_key.version, 'uint16')
        hash_data(hash_writer, tx_output.script_public_key.script, 'bytes')
        return hash_writer.digest()

    if reused_values.output_hash is None:
        hash_writer = new_transaction_signing_hash_writer()
        for tx_output in tx.outputs:
            hash_data(hash_writer, tx_output.value, 'uint64')
            hash_data(hash_writer, tx_output.script_public_key.version, 'uint16')
            hash_data(hash_writer, tx_output.script_public_key.script, 'bytes')
        reused_values.output_hash = hash_writer.digest()

    return reused_values.output_hash


def get_payload_hash(tx, reused_values):
    if tx.subnetwork_id == Subnetworks.subnetwork_id_native:
        return bytes(32)

    if reused_values.payload_hash is None:
        hash_writer = new_transaction_signing_hash_writer()
        hash_data(hash_writer, tx.payload, 'raw_bytes')
        reused_values.payload_hash = hash_writer.digest()

    return reused_values.payload_hash


def calculate_signature_hash(tx, idx, txin, prevscriptpk, hashtype, reused_values):

    hash_writer = new_transaction_signing_hash_writer()

    # tx version
    hash_data(hash_writer, tx.version, 'uint16')

    # previous output hash (get_previous_outpoint_hash)
    previous_outpoint_hash = get_previous_outputs_hash(tx, hashtype, reused_values)
    hash_data(hash_writer, previous_outpoint_hash, 'raw_bytes')

    # sequence hash
    sequence_hash = get_sequence_hash(tx, hashtype, reused_values)
    hash_data(hash_writer, sequence_hash, 'raw_bytes')

    # sig op count hash
    sig_op_count_hash = get_sig_op_count_hash(tx, hashtype, reused_values)
    hash_data(hash_writer, sig_op_count_hash, 'raw_bytes')

    # outpoint hash
    hash_data(hash_writer, txin.previous_outpoint.tx_id)
    hash_data(hash_writer, txin.previous_outpoint.index, 'uint32')

    # prevscriptpk
    hash_data(hash_writer, prevscriptpk.version, 'uint16')
    hash_data(hash_writer, bytes(prevscriptpk.script), 'bytes')

    # txin
    hash_data(hash_writer, txin.utxo_entry.amount, 'uint64')
    hash_data(hash_writer, txin.sequence, 'uint64')
    sig_op_count = int.from_bytes(txin.sig_op_count, byteorder='little')
    hash_data(hash_writer, sig_op_count, 'uint8')

    # output hash
    output_hash = get_output_hash(tx, idx, hashtype, reused_values)
    hash_data(hash_writer, output_hash, 'raw_bytes')

    # locktime, subnetwork, gas
    hash_data(hash_writer, tx.locktime, 'uint64')
    hash_data(hash_writer, tx.subnetwork_id, 'domain_subnetwork_id')
    hash_data(hash_writer, tx.gas, 'uint64')

    # payload hash
    payload_hash = get_payload_hash(tx, reused_values)
    hash_data(hash_writer, payload_hash, 'raw_bytes')

    # hashtype
    hash_data(hash_writer, hashtype.flag, 'uint8')

    return hash_writer.digest()


def raw_tx_in_signature(tx, idx, hashtype, priv_key, reused_values):
    msg_hash = calculate_signature_hash(tx, idx, tx.inputs[idx], tx.inputs[idx].utxo_entry.script_public_key, hashtype, reused_values)
    if not priv_key:
        logger.info(f"Msg hash: {msg_hash.hex()}")
        logger.info('External signing, you can use the schnorr_signature.py script included in klib')
        signature = bytes.fromhex(input('Insert signature (in hex): ').strip())
    else:
        signature = sign_hash(msg_hash, priv_key)
        logger.debug(f"Signature: {signature.hex()}")
    return signature + struct.pack('<B', hashtype.flag)


def sign_hash(msg_hash, priv_key):
    try:
        signing_key = PrivateKey(priv_key)
        signature = signing_key.sign_schnorr(msg_hash, aux_randomness=os.urandom(32))
    except NameError:
        signature = schnorr_sign(msg_hash, priv_key, os.urandom(32))
    return signature


def verify_signature(signature, msg_hash, pubkey):
    try:
        xonly_pubkey = PublicKeyXOnly(data=pubkey)
        sig_verify = xonly_pubkey.verify(signature, msg_hash)
    except NameError:
        logger.error(f"Coincurve schnorrsig verify failed, reverting to internal signing")
        sig_verify = schnorr_verify(msg_hash, pubkey, signature)
    return sig_verify


# Providing functionality for demonstration
if __name__ == "__main__":
    inputs = [
        Input(
            OutPoint(
                'a449ba289c7d7ef8641eb110deead0e334b685a2aafff836321b88851bbba11f',
                0
            ),
            UtxoEntry(
                500000000,
                ScriptPublicKey(
                    0,
                    bytes.fromhex('aa202c8922b6c21a55a8e868dddacd226760ea14d45c9febebc6a9be526495d8384987')
                ),
                2149,
                False
            )
        )
    ]
    outputs = [
        Output(
            400000000,
            ScriptPublicKey(
                0,
                bytes.fromhex('aa202c8922b6c21a55a8e868dddacd226760ea14d45c9febebc6a9be526495d8384987')
            )
        )
    ]
    tx = Transaction(
        inputs,
        outputs
    )
    rv = SighashReusedValues()
    sig_hash_type = SigHashType(1)
    idx = 0
    hash_result = calculate_signature_hash(
        tx,
        idx,
        inputs[idx],
        inputs[idx].utxo_entry.script_public_key,
        sig_hash_type,
        rv
    )
    print(hash_result.hex())
