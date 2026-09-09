

import struct

from satkas.core.klib.kopcodes import *


class ScriptBuilder:
    def __init__(self, *args, **kwargs):
        self.script = b''

    def add_op(self, opcode):
        self.script += opcode

    def canonical_data_size(self):
        pass

    def add_raw_data(self, data):
        data_len = len(data)

        if data_len == 0 or (data_len == 1 and data[0] == 0):
            self.script += OP_0
            return
        elif data_len == 1 and data[0] <= 16:
            self.script += bytes([int.from_bytes(OP_1, byteorder='little') - 1 + data[0]])
            return
        elif data_len == 1 and data[0] == OP_1_NEGATE_VAL[0]:
            # Grok fix:
            # Was comparing int data[0] to bytes OP_1_NEGATE_VAL (always false) and
            # then writing the value 0x81 instead of opcode OP_1NEGATE (0x4f).
            # https://github.com/kaspanet/rusty-kaspa/blob/v1.0.0/crypto/txscript/src/script_builder.rs#L154-L156
            self.script += OP_1_NEGATE
            return

        if data_len <= int.from_bytes(OP_DATA75, byteorder='little'):
            self.script += bytes([int.from_bytes(OP_DATA1, byteorder='little') - 1 + data_len])
        elif data_len <= 0xff:
            # Grok fix:
            # Cutoffs are u8::MAX / u16::MAX, not 2**8 / 2**16. A 256-byte push
            # used to take PUSHDATA1 and then struct.pack('<B', 256) would raise.
            # https://github.com/kaspanet/rusty-kaspa/blob/v1.0.0/crypto/txscript/src/script_builder.rs#L163-L171
            self.script += OP_PUSHDATA1
            self.script += struct.pack('<B', data_len)
        elif data_len <= 0xffff:
            self.script += OP_PUSHDATA2
            self.script += struct.pack('<H', data_len)
        else:
            self.script += OP_PUSHDATA4
            self.script += struct.pack('<I', data_len)

        self.script += data

    def add_locktime(self, locktime):
        self.add_u64(locktime)

    def add_u64(self, value):
        # rusty-kaspa ScriptBuilder::add_u64: little-endian u64 with trailing zeros trimmed (fixed by Grok)
        valb = struct.pack('<Q', value)
        self.add_raw_data(valb.rstrip(b'\x00'))

    def add_i64(self, value):
        # rusty-kaspa ScriptBuilder::add_i64: OP_0 / OP_1NEGATE / OP_1..OP_16, else script number (fixed by Grok)
        if value == 0:
            self.add_op(OP_0)
            return
        if value == -1 or 1 <= value <= 16:
            self.add_op(bytes([int.from_bytes(OP_1, 'little') - 1 + value]))
            return
        # Script numbers are little-endian magnitude with the sign in the high
        # bit of the last byte — not two's complement. If that high bit is
        # already set, append 0x00 so the sign bit does not collide.
        negative = value < 0
        positive = abs(value)
        out = bytearray()
        last_saturated = False
        while positive:
            byte = positive & 0xff
            last_saturated = (byte & 0x80) != 0
            out.append(byte)
            positive >>= 8
        if last_saturated:
            out.append(0)
        if negative:
            out[-1] |= 0x80
        self.add_raw_data(bytes(out))





