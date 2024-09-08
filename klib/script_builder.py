

import struct

from .kopcodes import *


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
        elif data_len == 1 and data[0] == OP_1_NEGATE_VAL:
            self.script += OP_1_NEGATE_VAL
            return

        if data_len <= int.from_bytes(OP_DATA75, byteorder='little'):
            self.script += bytes([int.from_bytes(OP_DATA1, byteorder='little') - 1 + data_len])
        elif data_len <= 2**8:
            self.script += OP_PUSHDATA1
            self.script += struct.pack('<B', data_len)
        elif data_len <= 2**16:
            self.script += OP_PUSHDATA2
            self.script += struct.pack('<H', data_len)
        elif data_len <= 2**32:
            self.script += OP_PUSHDATA4
            self.script += struct.pack('<L', data_len)

        self.script += data

    def add_locktime(self, locktime):
        self.add_u64(locktime)

    def add_u64(self, value):
        valb = struct.pack('<Q', value)
        # trim zeros
        res = valb.rstrip(b'\x00')
        res_len = len(res)
        if res_len < 4:
            res += b'\x00' * (4 - res_len)
        self.add_raw_data(res)




