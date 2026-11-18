"""Fixed BCH encoding for a 64-bit ID, with no expected-ID lookup."""
from itertools import combinations
import zlib

import galois
import numpy as np


class BCHIdentifier:
    def __init__(self):
        self.code = galois.BCH(255, 99)
        # Small fixed code: Python arithmetic avoids machine-specific JIT caches.
        self.code.field.compile("python-calculate")
        self.code.extension_field.compile("python-calculate")
        assert self.code.t >= 21

    def encode(self, identifier):
        data = bytes.fromhex(identifier)
        if len(data) != 8:
            raise ValueError("ID must be exactly 8 bytes")
        data += zlib.crc32(data).to_bytes(4, "big")
        message = np.zeros(99, dtype=np.uint8)
        message[:96] = np.unpackbits(np.frombuffer(data, dtype=np.uint8))
        return np.append(np.asarray(self.code.encode(message), dtype=np.uint8), 0).astype(np.uint8)

    def decode(self, bits):
        message, corrected = self.code.decode(np.asarray(bits[:255], dtype=np.uint8), errors=True)
        if corrected < 0 or np.any(message[96:]):
            return None, None
        data = np.packbits(np.asarray(message[:96], dtype=np.uint8)).tobytes()
        if zlib.crc32(data[:8]).to_bytes(4, "big") != data[8:]:
            return None, None
        return data[:8].hex(), int(corrected)

    def retry(self, bits, scores):
        """Try a bounded set of flips at the least reliable codeword positions."""
        reliability = np.abs(scores[:self.code.n])
        uncertain = np.argsort(reliability, kind="stable")[:8]
        guesses = sorted(combinations(uncertain, 4),
                         key=lambda positions: sum(reliability[i] for i in positions))
        for positions in guesses:
            candidate = bits.copy()
            candidate[list(positions)] ^= 1
            identifier, _ = self.decode(candidate)
            if identifier is not None:
                expected = self.encode(identifier)[:self.code.n]
                corrected = np.count_nonzero(bits[:self.code.n] != expected)
                return identifier, int(corrected)
        return None, None
