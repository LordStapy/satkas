

from .script_builder import ScriptBuilder
from .kopcodes import *


def build_contract_script(secret_hash, pkh_receiver, timelock, pkh_sender, verbose=False):
    payload = ScriptBuilder()
    payload.add_op(OP_IF)
    payload.add_op(OP_SIZE)
    payload.add_op(OP_DATA1)
    payload.add_op(OP_DATA32)
    payload.add_op(OP_EQUAL_VERIFY)
    payload.add_op(OP_SHA256)
    payload.add_raw_data(secret_hash)
    payload.add_op(OP_EQUAL_VERIFY)
    payload.add_op(OP_DUP)
    payload.add_op(OP_BLAKE2B)
    payload.add_raw_data(pkh_receiver)
    payload.add_op(OP_ELSE)
    payload.add_locktime(timelock)
    payload.add_op(OP_CHECK_LOCK_TIME_VERIFY)
    payload.add_op(OP_DUP)
    payload.add_op(OP_BLAKE2B)
    payload.add_raw_data(pkh_sender)
    payload.add_op(OP_ENDIF)
    payload.add_op(OP_EQUAL_VERIFY)
    payload.add_op(OP_CHECK_SIG)

    if verbose:
        print(f"OP_IF {OP_IF.hex()}")
        print(f"OP_SIZE {OP_SIZE.hex()}")
        print(f"OP_DATA1 {OP_DATA1.hex()}")
        print(f"OP_DATA32 {OP_DATA32.hex()}")
        print(f"OP_EQUAL_VERIFY {OP_EQUAL_VERIFY.hex()}")
        print(f"OP_SHA256 {OP_SHA256.hex()}")
        print(f"secret_hash {secret_hash}")
        print(f"OP_EQUAL_VERIFY {OP_EQUAL_VERIFY.hex()}")
        print(f"OP_DUP {OP_DUP.hex()}")
        print(f"OP_BLAKE2B {OP_BLAKE2B.hex()}")
        print(f"pkh_receiver {pkh_receiver}")
        print(f"OP_ELSE {OP_ELSE.hex()}")
        print(f"timelock {timelock}")
        print(f"OP_CHECK_LOCK_TIME_VERIFY {OP_CHECK_LOCK_TIME_VERIFY.hex()}")
        print(f"OP_DUP {OP_DUP.hex()}")
        print(f"OP_BLAKE2B {OP_BLAKE2B.hex()}")
        print(f"pkh_sender {pkh_sender}")
        print(f"OP_ENDIF {OP_ENDIF.hex()}")
        print(f"OP_EQUAL_VERIFY {OP_EQUAL_VERIFY.hex()}")
        print(f"OP_CHECK_SIG {OP_CHECK_SIG.hex()}")

    return payload.script


def build_contract_script_checksequence(secret_hash, pkh_receiver, timelock, pkh_sender, verbose=False):
    payload = ScriptBuilder()
    payload.add_op(OP_IF)
    payload.add_op(OP_SIZE)
    payload.add_op(OP_DATA1)
    payload.add_op(OP_DATA32)
    payload.add_op(OP_EQUAL_VERIFY)
    payload.add_op(OP_SHA256)
    payload.add_raw_data(secret_hash)
    payload.add_op(OP_EQUAL_VERIFY)
    payload.add_op(OP_DUP)
    payload.add_op(OP_BLAKE2B)
    payload.add_raw_data(pkh_receiver)
    payload.add_op(OP_ELSE)

    payload.add_locktime(timelock)
    payload.add_op(OP_CHECK_LOCK_TIME_VERIFY)
    payload.add_op(OP_DUP)
    payload.add_op(OP_BLAKE2B)
    payload.add_raw_data(pkh_sender)
    payload.add_op(OP_ENDIF)
    payload.add_op(OP_EQUAL_VERIFY)
    payload.add_op(OP_CHECK_SIG)

    if verbose:
        print(f"OP_IF {OP_IF.hex()}")
        print(f"OP_SIZE {OP_SIZE.hex()}")
        print(f"OP_DATA1 {OP_DATA1.hex()}")
        print(f"OP_DATA32 {OP_DATA32.hex()}")
        print(f"OP_EQUAL_VERIFY {OP_EQUAL_VERIFY.hex()}")
        print(f"OP_SHA256 {OP_SHA256.hex()}")
        print(f"secret_hash {secret_hash}")
        print(f"OP_EQUAL_VERIFY {OP_EQUAL_VERIFY.hex()}")
        print(f"OP_DUP {OP_DUP.hex()}")
        print(f"OP_BLAKE2B {OP_BLAKE2B.hex()}")
        print(f"pkh_receiver {pkh_receiver}")
        print(f"OP_ELSE {OP_ELSE.hex()}")
        print(f"timelock {timelock}")
        print(f"OP_CHECK_LOCK_TIME_VERIFY {OP_CHECK_LOCK_TIME_VERIFY.hex()}")
        print(f"OP_DUP {OP_DUP.hex()}")
        print(f"OP_BLAKE2B {OP_BLAKE2B.hex()}")
        print(f"pkh_sender {pkh_sender}")
        print(f"OP_ENDIF {OP_ENDIF.hex()}")
        print(f"OP_EQUAL_VERIFY {OP_EQUAL_VERIFY.hex()}")
        print(f"OP_CHECK_SIG {OP_CHECK_SIG.hex()}")

    return payload.script


def build_contract_script_short(secret_hash, pk_receiver, timelock, pk_sender):

    # UNTESTED, DON'T USE THIS IN MAINNET

    payload = ScriptBuilder()
    payload.add_op(OP_IF)
    payload.add_op(OP_SHA256)
    payload.add_raw_data(secret_hash)
    payload.add_op(OP_EQUAL_VERIFY)
    payload.add_op(OP_DUP)
    payload.add_raw_data(pk_receiver)
    payload.add_op(OP_ELSE)
    payload.add_locktime(timelock)
    payload.add_op(OP_CHECK_LOCK_TIME_VERIFY)
    payload.add_op(OP_DUP)
    payload.add_raw_data(pk_sender)
    payload.add_op(OP_ENDIF)
    payload.add_op(OP_EQUAL_VERIFY)
    payload.add_op(OP_CHECK_SIG)

    return payload.script


def build_spend_script(signature, pubkey, contract, refund=False, secret=None):
    payload = ScriptBuilder()
    payload.add_raw_data(signature)
    payload.add_raw_data(pubkey)
    if refund:
        payload.add_op(OP_0)
    else:
        payload.add_raw_data(secret)
        payload.add_op(OP_1)
    payload.add_raw_data(contract)

    return payload.script


def build_spend_script_short(signature, pubkey, contract, refund=False, secret=None):

    # UNTESTED, DON'T USE THIS IN MAINNET

    payload = ScriptBuilder()
    payload.add_raw_data(signature)
    if refund:
        payload.add_op(OP_0)
    else:
        payload.add_raw_data(secret)
        payload.add_op(OP_1)
    payload.add_raw_data(pubkey)
    payload.add_raw_data(contract)

    return payload.script
