"""Exercise the installed SDK with small synthetic IPv4/IPv6 databases."""

import ipaddress
import struct
from pathlib import Path

import ip2region.util as xdb_util
import pytest

from app.services.geoip_service import GeoIPService


def write_xdb(path: Path, address: str, region: str) -> None:
    """Write one exact-address segment in the public XDB format."""
    packed = ipaddress.ip_address(address).packed
    version = xdb_util.IPv4 if len(packed) == 4 else xdb_util.IPv6
    header = bytearray(256)
    struct.pack_into("<H", header, 0, 3)
    struct.pack_into("<HH", header, 16, version.id, 4)
    vector = bytearray(256 * 256 * 8)
    segment_offset = len(header) + len(vector)
    vector_offset = (packed[0] * 256 + packed[1]) * 8
    struct.pack_into(
        "<II", vector, vector_offset, segment_offset, segment_offset
    )
    boundary = packed[::-1] if len(packed) == 4 else packed
    data = region.encode("utf-8")
    segment = boundary * 2 + struct.pack(
        "<HI", len(data), segment_offset + version.index_size
    )
    path.write_bytes(header + vector + segment + data)


@pytest.mark.parametrize(
    "address,version",
    [
        ("8.8.8.8", xdb_util.IPv4),
        ("2001:4860:4860::8888", xdb_util.IPv6),
    ],
)
def test_loads_real_sdk_version_and_reads_region(
    tmp_path: Path,
    address: str,
    version: xdb_util.Version,
) -> None:
    path = tmp_path / "synthetic.xdb"
    write_xdb(path, address, "中国|0|广东省|深圳市|电信")
    service = GeoIPService()
    try:
        assert service._load_ip2region_db(str(path), version, version.name)
        location = service._lookup_ip2region(address)
        assert location.country == "中国"
        assert location.country_code == "CN"
        assert location.region == "广东省"
        assert location.city == "深圳市"
        assert location.isp == "电信"
    finally:
        service.close()
    assert service._ip2region_searcher_v4 is None
    assert service._ip2region_searcher_v6 is None
    # Closed handles must allow a Windows rename, too.
    path.rename(tmp_path / "closed.xdb")


def test_directory_loads_both_versions_and_keeps_queries_separate(
    tmp_path: Path,
) -> None:
    write_xdb(tmp_path / "ip2region_v4.xdb", "8.8.8.8", "中国|0|广东省|深圳市|电信")
    write_xdb(
        tmp_path / "ip2region_v6.xdb",
        "2001:4860:4860::8888",
        "中国|0|浙江省|杭州市|联通",
    )
    service = GeoIPService()
    try:
        assert service._init_ip2region(str(tmp_path))
        assert service._lookup_ip2region("8.8.8.8").city == "深圳市"
        assert service._lookup_ip2region("2001:4860:4860::8888").city == "杭州市"
    finally:
        service.close()


def test_database_open_failure_remains_controlled(tmp_path: Path) -> None:
    service = GeoIPService()
    assert not service._load_ip2region_db(
        str(tmp_path / "absent.xdb"), xdb_util.IPv4, "IPv4"
    )
    assert service._lookup_ip2region("8.8.8.8").is_empty()


@pytest.mark.parametrize("address", ["8.8.8.8", "2001:4860:4860::8888"])
def test_missing_version_remains_an_empty_location(address: str) -> None:
    assert GeoIPService()._lookup_ip2region(address).is_empty()
