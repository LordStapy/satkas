

from satkas.klib.script_builder import ScriptBuilder
from satkas.klib.kopcodes import *


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


def build_kip10_additive_borrower_script(owner_pubkey, borrower_pubkey):
    payload = ScriptBuilder()
    payload.add_op(OP_IF)
    payload.add_raw_data(owner_pubkey)
    payload.add_op(OP_CHECK_SIG)
    payload.add_op(OP_ELSE)
    payload.add_op(OP_DUP)
    payload.add_raw_data(borrower_pubkey)
    payload.add_op(OP_EQUAL_VERIFY)
    payload.add_op(OP_CHECK_SIG_VERIFY)
    payload.add_op(OP_TX_INPUT_INDEX)
    payload.add_op(OP_TX_INPUT_SPK)
    payload.add_op(OP_TX_INPUT_INDEX)
    payload.add_op(OP_TX_OUTPUT_SPK)
    payload.add_op(OP_EQUAL_VERIFY)
    payload.add_op(OP_TX_INPUT_INDEX)
    payload.add_op(OP_TX_OUTPUT_AMOUNT)
    payload.add_op(OP_TX_INPUT_INDEX)
    payload.add_op(OP_TX_INPUT_AMOUNT)
    payload.add_op(OP_GREATER_THAN)
    payload.add_op(OP_ENDIF)

    return payload.script


def build_kip10_borrower_spend_script(signature, script, is_owner=True, borrower_pubkey=None):
    payload = ScriptBuilder()
    payload.add_raw_data(signature)
    if is_owner:
        payload.add_op(OP_1)
    else:
        payload.add_raw_data(borrower_pubkey)
        payload.add_op(OP_0)
    payload.add_raw_data(script)

    return payload.script


def build_kip10_additive_threshold_script(owner_pubkey, threshold_sompi):
    payload = ScriptBuilder()
    payload.add_op(OP_IF)
    payload.add_raw_data(owner_pubkey)
    payload.add_op(OP_CHECK_SIG)
    payload.add_op(OP_ELSE)
    payload.add_op(OP_TX_INPUT_INDEX)
    payload.add_op(OP_TX_INPUT_SPK)
    payload.add_op(OP_TX_INPUT_INDEX)
    payload.add_op(OP_TX_OUTPUT_SPK)
    payload.add_op(OP_EQUAL_VERIFY)
    payload.add_op(OP_TX_INPUT_INDEX)
    payload.add_op(OP_TX_OUTPUT_AMOUNT)
    payload.add_i64(threshold_sompi)
    payload.add_op(OP_SUB)
    payload.add_op(OP_TX_INPUT_INDEX)
    payload.add_op(OP_TX_INPUT_AMOUNT)
    payload.add_op(OP_GREATER_THAN_OR_EQUAL)
    payload.add_op(OP_ENDIF)

    return payload.script


def build_kip10_threshold_spend_script(script, signature=None):
    payload = ScriptBuilder()
    if signature:
        payload.add_raw_data(signature)
        payload.add_op(OP_1)
    else:
        payload.add_op(OP_0)
    payload.add_raw_data(script)

    return payload.script


def build_kip10_subtractive_borrower_script(owner_pubkey, borrower_pubkey, threshold):
    payload = ScriptBuilder()
    payload.add_op(OP_IF)
    payload.add_raw_data(owner_pubkey)
    payload.add_op(OP_CHECK_SIG)
    payload.add_op(OP_ELSE)
    payload.add_op(OP_DUP)
    payload.add_raw_data(borrower_pubkey)
    payload.add_op(OP_EQUAL_VERIFY)
    payload.add_op(OP_CHECK_SIG_VERIFY)
    payload.add_op(OP_TX_INPUT_INDEX)
    payload.add_op(OP_TX_INPUT_SPK)
    payload.add_op(OP_TX_INPUT_INDEX)
    payload.add_op(OP_TX_OUTPUT_SPK)
    payload.add_op(OP_EQUAL_VERIFY)
    payload.add_op(OP_TX_INPUT_INDEX)
    payload.add_op(OP_TX_INPUT_AMOUNT)
    payload.add_i64(threshold)
    payload.add_op(OP_SUB)
    payload.add_op(OP_TX_INPUT_INDEX)
    payload.add_op(OP_TX_OUTPUT_AMOUNT)
    payload.add_op(OP_LESS_THAN_OR_EQUAL)
    payload.add_op(OP_ENDIF)

    return payload.script


def build_kip10_subtractive_borrower_secure_script(owner_pubkey, borrower_pubkey, threshold):
    payload = ScriptBuilder()

    # check if we follow owner or borrower branch, consume 1 item from stack (it's a true/false OP)
    payload.add_op(OP_IF)
    # entering owner branch, stack is [signature]
    # step 1: push owner pubkey to stack [signature, owner_pubkey]
    payload.add_raw_data(owner_pubkey)
    # step 2: pop 2 items from stack, check if signature is valid for owner pubkey, push TRUE to stack [TRUE]
    payload.add_op(OP_CHECK_SIG)
    # end owner branch, if script didn't fail on check_sig stack is [TRUE]

    payload.add_op(OP_ELSE)
    # entering borrower branch, stack is [signature, borrower_pubkey]
    # step 1: duplicate top stack item [signature, borrower_pubkey, borrower_pubkey]
    payload.add_op(OP_DUP)
    # step 2: push borrower public key to stack [signature, borrower_pubkey, borrower_pubkey, borrower_pubkey]
    payload.add_raw_data(borrower_pubkey)
    # step 3: pop 2 items from stack, either fail/continue execution [signature, borrower_pubkey]
    payload.add_op(OP_EQUAL_VERIFY)
    # step 4: pop 2 items from stack, check if signature is valid, either fail/continue execution []
    payload.add_op(OP_CHECK_SIG_VERIFY)

    # here we check that input and output have the same scriptPublicKey (i.e. same address)
    # step 5: push tx input index [idx]
    payload.add_op(OP_TX_INPUT_INDEX)
    # step 6: pop 1 item from stack, push scriptPublicKey of selected index input [input_spk]
    payload.add_op(OP_TX_INPUT_SPK)
    # step 7: push tx input index [input_spk, idx]
    payload.add_op(OP_TX_INPUT_INDEX)
    # step 8: pop 1 item from stack, push scriptPublicKey of selected index output [index_spk, output_spk]
    payload.add_op(OP_TX_OUTPUT_SPK)
    # step 9: pop 2 items from stack, check that are equal, either fail/continue []
    payload.add_op(OP_EQUAL_VERIFY)

    # here we check that there is only one output (avoid the borrower siphoning out funds)
    # step 10: push tx output count [output_count]
    payload.add_op(OP_TX_OUTPUT_COUNT)
    # step 11: push 1 [output_count, 1]
    payload.add_i64(1)
    # step 12: pop 2 items, check that are equal, either fail/continue []
    payload.add_op(OP_EQUAL_VERIFY)

    # last, we check that (input - threshold) is less than or equal to output
    # said differently, output is bigger (or equal) than (input - threshold)
    # e.g. borrower is allowed to spend up to "threshold" sompi, but can also add funds to the address
    # we can add an extra check that output amount is bigger than lets say 0.5 KAS
    # step 10: push tx input index [idx]
    payload.add_op(OP_TX_INPUT_INDEX)
    # step 11: pop 1 item, push amount of selected index input [input_amount]
    payload.add_op(OP_TX_INPUT_AMOUNT)
    # step 12: push threshold value to stack [input_amount, threshold]
    payload.add_i64(threshold)
    # step 13: pop 2 items from stack, push (input_amount - threshold) -> "decreased_input" [decreased_input]
    payload.add_op(OP_SUB)
    # step 14: push tx input index [decreased_input, idx]
    payload.add_op(OP_TX_INPUT_INDEX)
    # step 15: pop 1 item, push amount of selected index output [decreased_input, output_amount]
    payload.add_op(OP_TX_OUTPUT_AMOUNT)
    # step 16: pop 2 items from stack, evaluate decreased_input <= output_amount, push TRUE [TRUE]
    payload.add_op(OP_LESS_THAN_OR_EQUAL)
    # step 17: leaving borrower branch, stack is [TRUE]
    payload.add_op(OP_ENDIF)

    return payload.script

