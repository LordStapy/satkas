
import struct

from bitcoinutils.script import Script

# define few opcodes
OP_IF = b'\x63'
OP_SIZE = b'\x82'
OP_32 = b'\x20'
OP_EQUAL_VERIFY = b'\x88'
OP_SHA256 = b'\xa8'
OP_DUP = b'\x76'
OP_HASH160 = b'\xa9'
OP_ELSE = b'\x67'
OP_CHECK_LOCK_TIME_VERIFY = b'\xb1'
OP_DROP = b'\x75'
OP_ENDIF = b'\x68'
OP_CHECK_SIG = b'\xac'


def build_contract_descriptor(sender, receiver, timelock, secret_hash):
    # not working, can't use pkh without the actual key, keyhash alone is not enough
    # even addr is bad, problem is probably the or_i block entirely
    sender = f"addr({sender})"
    receiver = f"addr({receiver})"
    redeem_path = f"and_v(sha256({secret_hash.hex()}),{receiver})"
    refund_path = f"and_v(after({timelock}),{sender})"
    descriptor = f"wsh(or_i({redeem_path},{refund_path}))"
    return descriptor


def build_btc_contract_script_old(sender_pkh, receiver_pkh, locktime, secret_hash):
    script = b''
    script += OP_IF
    script += OP_SIZE
    script += b'\x01'
    script += OP_32
    script += OP_EQUAL_VERIFY
    script += OP_SHA256
    script += b'\x20'
    script += secret_hash
    script += OP_EQUAL_VERIFY
    script += OP_DUP
    script += OP_HASH160
    script += b'\x14'
    script += receiver_pkh
    script += OP_ELSE
    script += b'\x02'  # <- hardcoded locktime len, working with test values, but will break
    script += struct.pack('<h', locktime)
    script += OP_CHECK_LOCK_TIME_VERIFY
    script += OP_DROP
    script += OP_DUP
    script += OP_HASH160
    script += b'\x14'
    script += sender_pkh
    script += OP_ENDIF
    script += OP_EQUAL_VERIFY
    script += OP_CHECK_SIG
    return script


def build_btc_contract_script(sender_pkh, receiver_pkh, locktime, secret_hash):
    redeem_script = Script(
        [
            "OP_IF",
            "OP_SIZE", 0X20, "OP_EQUALVERIFY",
            "OP_SHA256", secret_hash.hex(), "OP_EQUALVERIFY",
            "OP_DUP", "OP_HASH160", receiver_pkh,
            "OP_ELSE",
            locktime, "OP_CHECKLOCKTIMEVERIFY", "OP_DROP",
            "OP_DUP", "OP_HASH160", sender_pkh,
            "OP_ENDIF",
            "OP_EQUALVERIFY",
            "OP_CHECKSIG"
        ]
    )
    return redeem_script


def build_btc_spend_script(signature, pubkey, contract, refund=False, secret=None):
    payload = list()
    payload.append(signature)
    payload.append(pubkey.to_hex())
    if refund:
        payload.append('')
    else:
        if isinstance(secret, bytes):
            secret = secret.hex()
        payload.append(secret)
        payload.append('01')
    payload.append(contract)
    return payload
