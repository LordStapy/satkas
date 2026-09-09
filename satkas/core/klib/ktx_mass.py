from satkas.core.klib.kdatatype import (
    Input,
    Output,
    OutPoint,
    ScriptPublicKey,
    Transaction,
    UtxoEntry,
)

# Constants
HASH_SIZE = 32
SUBNETWORK_ID_SIZE = 20
MASS_PER_TX_BYTE = 1
MASS_PER_SCRIPT_PUB_KEY_BYTE = 10
MASS_PER_SIG_OP = 1000
KIP9_C = int(1e12)
# Toccata fee grams; KIP-13 raw transient is 4 * size.
NORMALIZED_TRANSIENT_BYTE_FACTOR = 2
DUMMY_HIGH_SOMPI = 10_000 * 10**8  # 10k KAS; C/this == 1
P2PK_SPK = b'\x20' + b'\x00' * 32 + b'\xac'  # 34
P2SH_SPK = b'\xaa\x20' + b'\x00' * 32 + b'\x87'  # 35
P2PK_SIGSCRIPT_SIZE = 66
# LN (off-chain) timestamp locktime: 6 payload bytes → redeem sigscript 257.
# On-chain DAA locktime: 4 payload bytes → 255.
#
# DAA 2_147_483_648 (2**31): high bit of the last of those 4 bytes is set.
# add_u64 does not append a 0x00 sign byte (same as rusty-kaspa). If CLTV
# treats the push as a signed script number, refunds break at this score
# even though the payload is still 4 bytes — check actual DAA against this
# before assuming 255 is still safe.
#
# DAA 4_294_967_296 (2**32): payload becomes 5 bytes → redeem 256.
P2SH_REDEEM_SIGSCRIPT_LN = 257
P2SH_REDEEM_SIGSCRIPT_DAA = 255


def tx_estimated_serialized_size(tx, signature_script_size=66):
    size = 0
    size += 2  # version u16
    size += 8  # number of inputs u64
    for i in tx.inputs:
        # outpoint size
        size += HASH_SIZE  # previous txid
        size += 4  # index u32
        # signature script
        size += 8  # length of signature script
        if i.signature_script is None:
            size += signature_script_size  # default to p2pk signature_script (OP_65 + 64 bytes sig + 1 byte hashtype)
        else:
            size += len(i.signature_script)  # u64
        # sequence
        size += 8
    size += 8  # number of outputs u64
    for o in tx.outputs:
        size += 8  # value u64
        size += 2  # scriptPublicKey version u16
        size += 8  # length of scriptPublicKey u64
        size += len(o.script_public_key.script)  # u64
    size += 8  # lock time u64
    size += SUBNETWORK_ID_SIZE  # u64
    size += 8  # gas u64
    size += HASH_SIZE  # payload hash u64
    size += 8  # length of payload
    size += len(tx.payload)  # u64
    return size


def compute_mass(tx):
    if tx.subnetwork_id == 1:
        # tx is coinbase
        return 0
    size = tx_estimated_serialized_size(tx)
    mass_for_size = size * MASS_PER_TX_BYTE
    total_script_publik_key_size = sum([2 + len(o.script_public_key.script) for o in tx.outputs])
    mass_for_script_public_key = total_script_publik_key_size * MASS_PER_SCRIPT_PUB_KEY_BYTE
    total_sigops = sum([int.from_bytes(i.sig_op_count, 'little') for i in tx.inputs])
    mass_for_sigops = total_sigops * MASS_PER_SIG_OP
    return mass_for_size + mass_for_script_public_key + mass_for_sigops


def negative_mass(input_values, output_count):
    if output_count == 1 or output_count <= len(input_values) <= 2:
        # not sure if int should be calculated on final value or on every (KIP9_C / v)
        return sum([KIP9_C // v for v in input_values])
    return len(input_values) * (KIP9_C // (sum([v for v in input_values]) // len(input_values)))


def storage_mass(inputs, outputs):
    n = negative_mass([i.utxo_entry.amount for i in inputs], len(outputs))
    p = sum([KIP9_C // o.value for o in outputs])
    # print(f"[storage_mass] Negative mass: {n}")
    # print(f"[storage_mass] Positive mass: {p}")
    return max(p - n, 0)


def mass(tx):
    s_mass = storage_mass(tx.inputs, tx.outputs)
    c_mass = compute_mass(tx)
    size = tx_estimated_serialized_size(tx)
    # print(f"Computed storage_mass: {s_mass}")
    # print(f"Computed compute_mass: {c_mass}")
    return max(s_mass, c_mass, size * NORMALIZED_TRANSIENT_BYTE_FACTOR)


def _dummy_input(amount_sompi, spk, signature_script_len):
    return Input(
        OutPoint('00' * 32, 0),
        UtxoEntry(amount_sompi, ScriptPublicKey(0, spk), 1, False),
        sig_op_count=b'\x01',
        signature_script=b'\x00' * signature_script_len,
    )


def _dummy_output(amount_sompi, spk):
    return Output(max(int(amount_sompi), 1), ScriptPublicKey(0, spk))


def estimate_funding_mass(contract_sompi):
    """1 p2pk in, p2sh contract + p2pk change. High dummy in/change so storage ≈ C/contract."""
    contract_sompi = max(int(contract_sompi or 0), 1)
    vin = max(DUMMY_HIGH_SOMPI, contract_sompi + DUMMY_HIGH_SOMPI)
    tx = Transaction(
        [_dummy_input(vin, P2PK_SPK, P2PK_SIGSCRIPT_SIZE)],
        [_dummy_output(contract_sompi, P2SH_SPK),
         _dummy_output(vin - contract_sompi, P2PK_SPK)],
    )
    return mass(tx)


def estimate_spend_mass(input_sompi, output_sompi=None, sigscript_size=P2SH_REDEEM_SIGSCRIPT_LN):
    """1 p2sh in, 1 p2pk out (redeem-sized sigscript)."""
    input_sompi = max(int(input_sompi or 0), 1)
    if output_sompi is None:
        output_sompi = input_sompi
    output_sompi = max(int(output_sompi), 1)
    tx = Transaction(
        [_dummy_input(input_sompi, P2SH_SPK, sigscript_size)],
        [_dummy_output(output_sompi, P2PK_SPK)],
    )
    return mass(tx)


if __name__ == '__main__':
    # dummy tx, we only need the amounts
    tx_inputs = [
        Input(
            OutPoint('a'*32, 0),
            UtxoEntry(99989999, ScriptPublicKey(0, b'1'*34), 100, False),
            sig_op_count=b'\x01',
            signature_script=b'1'*66
        ),
        Input(
            OutPoint('a'*32, 0),
            UtxoEntry(100000000, ScriptPublicKey(0, b'2'*35), 100, False),
            sig_op_count=b'\x02',
            signature_script=b'3'*189
        )
    ]
    tx_outputs = [
        Output(99994999, ScriptPublicKey(0, b'1'*34)),
        Output(99990000, ScriptPublicKey(0, b'1'*35))
    ]
    dummy_tx = Transaction(tx_inputs, tx_outputs)
    print(f"Final tx mass: {mass(dummy_tx)}")
