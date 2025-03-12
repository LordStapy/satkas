

# Constants
HASH_SIZE = 32
SUBNETWORK_ID_SIZE = 20
MASS_PER_TX_BYTE = 1
MASS_PER_SCRIPT_PUB_KEY_BYTE = 10
MASS_PER_SIG_OP = 1000
KIP9_C = int(1e12)


def tx_estimated_serialized_size(tx):
    size = 0
    size += 2  # version u16
    size += 8  # number of inputs u64
    for i in tx.inputs:
        # outpoint size
        size += HASH_SIZE  # previous txid
        size += 4  # index u32
        # signature script
        size += 8  # length of signature script
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
    print(f"[storage_mass] Negative mass: {n}")
    print(f"[storage_mass] Positive mass: {p}")
    return max(p - n, 0)


def mass(tx):
    s_mass = storage_mass(tx.inputs, tx.outputs)
    c_mass = compute_mass(tx)
    print(f"Computed storage_mass: {s_mass}")
    print(f"Computed compute_mass: {c_mass}")
    return max(s_mass, c_mass)


if __name__ == '__main__':
    from kdatatype import Transaction, Input, Output, OutPoint, UtxoEntry, ScriptPublicKey
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
