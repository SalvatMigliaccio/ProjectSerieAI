"""
UUID v7 primary keys, with a fallback for Python < 3.14.

WHY v7 AND NOT v4. A v4 is random, so every insert lands anywhere in the
B-tree: pages fill halfway and the index of a growing table fragments. A v7
carries the timestamp in its first 48 bits, so inserts are almost always at
the tail — the same behaviour as a serial key, without revealing how many
users exist or letting anyone guess the next id.

WHY NOT A SERIAL INTEGER. A sequential id in a URL tells the world how many
users you have and makes every resource enumerable. On an accounts table that
is exactly the information not to give away.

WHY NOT POSTGRES `uuidv7()`. It ships with Postgres 18; this runs 17.
Generating it in the application keeps an advantage even after that: the id
exists BEFORE the INSERT, so an audit row naming it can be written in the same
transaction without a RETURNING round trip.
"""

from __future__ import annotations

import os
import time
import uuid

_NATIVE = hasattr(uuid, "uuid7")


def uuid7() -> uuid.UUID:
    """A UUID v7. Uses the standard library one where available (3.14+)."""
    if _NATIVE:
        return uuid.uuid7()

    # RFC 9562 section 5.7: 48 bits of Unix milliseconds, 4 of version, 12
    # random, 2 of variant, 62 random. This fallback does not guarantee
    # monotonicity within the same millisecond, which is fine: we need ids
    # that grow over time, not a counter.
    ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF          # 48 bits
    noise = int.from_bytes(os.urandom(10), "big")           # 80 bits of entropy
    rand_a = (noise >> 62) & 0xFFF                          # 12 bits
    rand_b = noise & ((1 << 62) - 1)                        # 62 bits
    return uuid.UUID(int=(ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b)
