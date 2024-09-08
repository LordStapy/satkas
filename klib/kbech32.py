
# Python port of https://github.com/kaspanet/kaspad/blob/master/util/bech32/bech32.go
# Original header:
# Copyright (c) 2017 The btcsuite developers
# Use of this source code is governed by an ISC
# license that can be found in the LICENSE file.

import struct


GENERATOR = [0x98f2bc8e61, 0x79b76d99e2, 0xf33e5fb3c4, 0xae2eabe2a8, 0x1e4f43e470]
CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
CHECKSUM_LENGTH = 8


class ConversionType:
    def __init__(self, from_bits, to_bits, pad):
        self.from_bits = from_bits
        self.to_bits = to_bits
        self.pad = pad


five_to_eight_bits = ConversionType(5, 8, False)
eight_to_five_bits = ConversionType(8, 5, True)


def convert_bits(data, conversion_type):
    regrouped = []
    next_byte = 0
    filled_bits = 0
    for b in data:
        b = b << (8 - conversion_type.from_bits)
        remaining_from_bits = conversion_type.from_bits
        while remaining_from_bits > 0:
            remaining_to_bits = conversion_type.to_bits - filled_bits
            to_extract = min(remaining_from_bits, remaining_to_bits)
            next_byte = (next_byte << to_extract) | (b >> (8 - to_extract))
            b = (b << to_extract) % (2 ** 8)
            remaining_from_bits -= to_extract
            filled_bits += to_extract
            if filled_bits == conversion_type.to_bits:
                regrouped.append(next_byte)
                filled_bits = 0
                next_byte = 0
    if conversion_type.pad and filled_bits > 0:
        next_byte <<= (conversion_type.to_bits - filled_bits)
        regrouped.append(next_byte)
    return regrouped


def calculate_checksum(prefix, payload):
    prefix_lower5_bits = prefix_to_uint5_array(prefix)
    payload_ints = [int(b) for b in payload]
    template_zeroes = [0] * CHECKSUM_LENGTH
    concat = prefix_lower5_bits + [0] + payload_ints + template_zeroes
    poly_mod_result = poly_mod(concat)
    res = [(poly_mod_result >> (5 * (CHECKSUM_LENGTH - 1 - i))) & 31 for i in range(CHECKSUM_LENGTH)]
    return res


def poly_mod(values):
    # G = [0x98f2bc8e61, 0x79b76d99e2, 0xf33e5fb3c4, 0xae2eabe2a8, 0x1e4f43e470]
    checksum = int.from_bytes(struct.pack('>Q', 1), byteorder='big')
    for value in values:
        top_bits = checksum >> 35
        checksum = ((checksum & 0x07ffffffff) << 5) ^ value
        for i in range(5):
            if (top_bits >> i) & 1:
                checksum = checksum ^ GENERATOR[i]
    return checksum ^ 1


def prefix_to_uint5_array(prefix):
    return [ord(char) & 31 for char in prefix]


def verify_checksum(prefix, payload):
    prefix_lower5_bits = prefix_to_uint5_array(prefix)
    payload_ints = [int(b) for b in payload]
    data_to_verify = prefix_lower5_bits + [0] + payload_ints
    return poly_mod(data_to_verify) == 0


# Converts the byte list 'data' to a string where each byte in 'data'
# encodes the index of a character in 'charset'.
# IMPORTANT: this function expects the data to be in uint5 format.
# CAUTION: for legacy reasons, in case of an error this function returns
# an empty string instead of an error.
def encode_to_base32(data):
    result = []
    for b in data:
        if int(b) >= len(CHARSET):
            print(f"Error encoding to base32, b={b}")
            return ""
        result.append(CHARSET[b])
    return "".join(result)


def decode_from_base32(base32_string):
    decoded = []
    for char in base32_string:
        index = CHARSET.find(char)
        if index < 0:
            raise ValueError('invalid character not part of charset: {}'.format(char))
        decoded.append(index)
    return decoded


def encode(prefix, data):
    checksum = calculate_checksum(prefix, data)
    combined = data + checksum
    base32_string = encode_to_base32(combined)
    return f"{prefix}:{base32_string}"


def decode(encoded):
    checksum_length = 8
    # The minimum allowed length for a Bech32 string is 10 characters,
    # since it needs a non-empty prefix, a separator, and an 8 character
    # checksum.
    if len(encoded) < checksum_length + 2:
        raise ValueError(f"invalid bech32 string length {len(encoded)}")

    # Only ASCII characters between 33 and 126 are allowed.
    for i in range(len(encoded)):
        if ord(encoded[i]) < 33 or ord(encoded[i]) > 126:
            raise ValueError(f"invalid character in string: '{encoded[i]}'")

    # The characters must be either all lowercase or all uppercase.
    lower = encoded.lower()
    upper = encoded.upper()
    if encoded != lower and encoded != upper:
        raise ValueError("string not all lowercase or all uppercase")

    # We'll work with the lowercase string from now on.
    encoded = lower

    # The string is invalid if the last ':' is non-existent, it is the
    # first character of the string (no human-readable part) or one of the
    # last 8 characters of the string (since checksum cannot contain ':'),
    # or if the string is more than 90 characters in total.
    colon_index = encoded.rfind(':')
    if colon_index < 1 or colon_index + checksum_length + 1 > len(encoded):
        raise ValueError("invalid index of ':'")

    # The prefix part is everything before the last ':'.
    prefix = encoded[:colon_index]
    data = encoded[colon_index + 1:]

    # Each character corresponds to the byte with value of the index in
    # 'charset'.
    try:
        decoded = decode_from_base32(data)
    except Exception as e:
        raise ValueError(f"failed converting data to bytes: {str(e)}")

    if not verify_checksum(prefix, decoded):
        checksum = encoded[-checksum_length:]
        expected = encode_to_base32(
            calculate_checksum(prefix, decoded[:-checksum_length])
        )
        raise ValueError(f"checksum failed. Expected {expected}, got {checksum}")

    # We exclude the last 8 bytes, which is the checksum.
    return prefix, decoded[:-checksum_length]


def encode_address(prefix, payload, version):
    data = [version]
    data += payload
    converted = convert_bits(data, eight_to_five_bits)
    return encode(prefix, converted)


def decode_address(addr):
    prefix, decoded = decode(addr)
    converted = convert_bits(decoded, five_to_eight_bits)
    version = converted[0]
    payload = converted[1:]
    return prefix, payload, version


