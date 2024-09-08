
from dataclasses import dataclass
from typing import List, Optional


class SigHashType:
    def __init__(self, flag):
        self.flag = flag

    def is_sig_hash_all(self):
        return self.flag & 0x01

    def is_sig_hash_none(self):
        return self.flag & 0x02

    def is_sig_hash_single(self):
        return self.flag & 0x04

    def is_sig_hash_anyone_can_pay(self):
        return self.flag & 0x80  # Assume that the top bit dictates the ANYONECANPAY flag


@dataclass
class OutPoint:
    tx_id: str
    index: int


@dataclass
class ScriptPublicKey:
    version: int
    script: bytes


@dataclass
class UtxoEntry:
    amount: int
    script_public_key: ScriptPublicKey
    block_daa_score: int
    is_coinbase: bool


@dataclass
class Input:
    previous_outpoint: OutPoint
    utxo_entry: UtxoEntry
    sequence: int = 0
    sig_op_count: bytes = b'\x01'  # ToDo: calculate this field for complex script involving multiple sigs
    signature_script: bytes = None


@dataclass
class Output:
    value: int
    script_public_key: ScriptPublicKey


@dataclass
class Transaction:
    inputs: List[Input]
    outputs: List[Output]
    version: int = 0
    locktime: int = 0
    subnetwork_id: int = 0
    gas: int = 0
    payload: bytes = b''
    tx_id: Optional[str] = ''


@dataclass
class PubKeySignaturePair:
    extended_public_key: str
    signature: bytes


@dataclass
class PartiallySignedInput:
    prev_output: Output
    minimum_signatures: int
    pub_key_signature_pairs: List[PubKeySignaturePair]
    derivation_path: str


@dataclass
class PartiallySignedTransaction:
    tx: Transaction
    partially_signed_inputs: List[PartiallySignedInput]


@dataclass
class PreviousScriptPubkey:
    version: int
    script: bytes


@dataclass
class Subnetworks:
    subnetwork_id_native: int = 0
    subnetwork_id_coinbase: int = 1
    subnetwork_id_registry: int = 2


@dataclass
class SighashReusedValues:
    previous_outputs_hash: Optional[bytes] = None
    sequence_hash: Optional[bytes] = None
    sig_op_count_hash: Optional[bytes] = None
    output_hash: Optional[bytes] = None
    payload_hash: Optional[bytes] = None
