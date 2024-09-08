
import json

from . import kdatatype as kdt
# from .wallet_pb2 import (
#     PartiallySignedTransaction,
#     TransactionMessage,
#     SubnetworkId,
#     TransactionInput,
#     Outpoint,
#     TransactionId,
#     TransactionOutput,
#     ScriptPublicKey,
#     PartiallySignedInput,
#     PubKeySignaturePair,
#     RpcTransaction,
#     RpcTransactionInput,
#     RpcTransactionOutput,
#     RpcOutpoint,
#     RpcScriptPublicKey
# )

# This code is mostly unused


def gen_rpc_transaction(tx):
    rpc_tx = {}
    rpc_tx["version"] = tx.version
    rpc_tx["inputs"] = [
        {"previousOutpoint":
            {"transactionId": i.previous_outpoint.tx_id,
             "index": i.previous_outpoint.index},
         "signatureScript": i.signature_script.hex(),
         "sequence": i.sequence,
         "sigOpCount": int.from_bytes(i.sig_op_count, byteorder='little')}
        for i in tx.inputs
    ]
    rpc_tx["outputs"] = [
        {"amount": o.value,
         "scriptPublicKey":
             {"version": o.script_public_key.version,
              "scriptPublicKey": o.script_public_key.script.hex()}}
        for o in tx.outputs
    ]
    rpc_tx['lockTime'] = tx.locktime
    rpc_tx['subnetworkId'] = '00'*20
    rpc_tx['gas'] = tx.gas
    rpc_tx['payload'] = tx.payload.hex()
    return json.dumps(rpc_tx)

#
# def deserialize_partially_signed_transaction():
#     pass
#
#
# def serialize_partially_signed_transaction(pst: kdt.PartiallySignedTransaction):
#     res = partially_signed_transaction_to_proto(pst)
#     return res.SerializeToString()
#
#
# def deserialize_domain_transaction():
#     pass
#
#
# def serialize_domain_transaction(tx: kdt.Transaction):
#     res = transaction_to_proto(tx)
#     return res.SerializeToString()
#
#
# def partially_signed_transaction_from_proto():
#     pass
#
#
# def partially_signed_transaction_to_proto(pst: kdt.PartiallySignedTransaction):
#     proto_inputs = []
#     for i in pst.partially_signed_inputs:
#         proto_inputs.append(partially_signed_input_to_proto(i))
#     res = PartiallySignedTransaction()
#     res.tx.CopyFrom(transaction_to_proto(pst.tx))
#     for i in proto_inputs:
#         res.partiallySignedInputs.append(i)
#     return res
#
#
# def partially_signed_input_from_proto():
#     pass
#
#
# def partially_signed_input_to_proto(psi: kdt.PartiallySignedInput):
#     proto_pairs = []
#     for pair in psi.pub_key_signature_pairs:
#         proto_pairs.append(public_signature_pair_to_proto(pair))
#     res = PartiallySignedInput()
#     res.prevOutput.CopyFrom(transaction_output_to_proto(psi.prev_output))
#     res.minimumSignatures = psi.minimum_signatures
#     for pair in proto_pairs:
#         res.pubKeySignaturePairs.append(pair)
#     res.derivationPath = psi.derivation_path
#     return res
#
#
# def public_signature_pair_from_proto():
#     pass
#
#
# def public_signature_pair_to_proto(pair: kdt.PubKeySignaturePair):
#     res = PubKeySignaturePair()
#     res.extendedPubKey = pair.extended_public_key
#     res.signature = pair.signature
#     return res
#
#
# def transaction_from_proto():
#     pass
#
#
# def transaction_to_proto(tx: kdt.Transaction):
#     proto_inputs = []
#     for i in tx.inputs:
#         proto_inputs.append(transaction_input_to_proto(i))
#
#     proto_outputs = []
#     for o in tx.outputs:
#         proto_outputs.append(transaction_output_to_proto(o))
#
#     res = TransactionMessage()
#     res.version = tx.version  # uint32
#     for i in proto_inputs:
#         res.inputs.append(i)
#     for o in proto_outputs:
#         res.outputs.append(o)
#     res.lockTime = tx.locktime
#     sub_id = SubnetworkId()
#     sub_id.bytes = bytes(20)
#     res.subnetworkId.CopyFrom(sub_id)
#     res.gas = tx.gas
#     res.payload = tx.payload
#
#     return res
#
#
# def transaction_input_from_proto():
#     pass
#
#
# def transaction_input_to_proto(tx_input: kdt.Input):
#     res = TransactionInput()
#     res.previousOutpoint.CopyFrom(outpoint_to_proto(tx_input.previous_outpoint))
#     res.signatureScript = tx_input.signature_script
#     res.sequence = tx_input.sequence
#     res.sigOpCount = int.from_bytes(tx_input.sig_op_count, byteorder='little')  # uint32
#
#     return res
#
#
# def outpoint_from_proto():
#     pass
#
#
# def outpoint_to_proto(o: kdt.OutPoint):
#     res = Outpoint()
#     tx_id = TransactionId()
#     tx_id.bytes = bytes.fromhex(o.tx_id)
#     res.transactionId.CopyFrom(tx_id)
#     res.index = o.index
#     return res
#
#
# def transaction_id_from_proto():
#     pass
#
#
# def transaction_output_from_proto():
#     pass
#
#
# def transaction_output_to_proto(tx_output: kdt.Output):
#     res = TransactionOutput()
#     res.value = tx_output.value
#     res.scriptPublicKey.CopyFrom(script_public_key_to_proto(tx_output.script_public_key))
#
#     return res
#
#
# def script_public_key_from_proto():
#     pass
#
#
# def script_public_key_to_proto(spk: kdt.ScriptPublicKey):
#     res = ScriptPublicKey()
#     res.script = spk.script
#     res.version = spk.version  # uint32
#     return res

