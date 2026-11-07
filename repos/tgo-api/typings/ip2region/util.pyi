# Verified against py-ip2region 3.0.2 ip2region/util.py.
from collections.abc import Callable
from typing import Final

class Version:
    id: int
    name: str
    byte_num: int
    index_size: int
    def __init__(
        self,
        id: int,
        name: str,
        byte_num: int,
        index_size: int,
        ip_compare: Callable[[bytes, bytes, int], int],
    ) -> None: ...

IPv4: Final[Version]
IPv6: Final[Version]
