# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
from datetime import datetime, timezone

import pytest

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.hosted_apps_validator.scanner import ClamAvScanner, parse_scanner_version, scan_verdict


def test_scanner_health_requires_a_recent_real_signature_database():
    now = datetime(2026, 10, 4, tzinfo=timezone.utc)
    value = parse_scanner_version("ClamAV 1.4.3/28143/Sat Oct  3 12:00:00 2026", now=now)
    assert value["version"] == "1.4.3" and value["signature_version"] == "28143"
    for bad in ("ClamAV 1.4.3/1/Mon Jan  1 12:00:00 2024", "ClamAV dev", "PONG", "ERROR"):
        with pytest.raises(HostedApplicationError):
            parse_scanner_version(bad, now=now)


@pytest.mark.asyncio
async def test_real_scanner_clean_malware_and_unavailable(tmp_path):
    if not os.environ.get("HOSTED_TEST_CLAMD"):
        pytest.skip("Set HOSTED_TEST_CLAMD to test the owned real ClamAV instance")
    scanner = ClamAvScanner("127.0.0.1", 13310)
    provenance = await scanner.health()
    assert provenance["signature_version"]
    clean = tmp_path / "clean.html"
    clean.write_text("<html>Safe static visual</html>")
    await scanner.scan(clean)
    malware = tmp_path / "test.html"
    # Standard non-executable antivirus test string, generated only in temporary test data.
    malware.write_bytes(b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*")
    with pytest.raises(HostedApplicationError) as error:
        await scanner.scan(malware)
    assert error.value.code == "MALWARE_DETECTED"
    unavailable = ClamAvScanner("127.0.0.1", 13311)
    with pytest.raises(HostedApplicationError) as error:
        await unavailable.scan(clean)
    assert error.value.status == 503


def test_scan_verdict_separates_malware_from_scanner_limits():
    assert scan_verdict(b"stream: OK") is None
    with pytest.raises(HostedApplicationError) as error:
        scan_verdict(b"stream: Win.Test.EICAR_HDB-1 FOUND")
    assert error.value.code == "MALWARE_DETECTED"
    for limit in (b"MaxScanTime", b"MaxFileSize", b"MaxScanSize", b"MaxFiles", b"MaxRecursion"):
        with pytest.raises(HostedApplicationError) as error:
            scan_verdict(b"stream: Heuristics.Limits.Exceeded." + limit + b" FOUND")
        assert (error.value.code, error.value.status) == ("SCAN_LIMIT_EXCEEDED", 422)
        assert "Heuristics" not in error.value.message, "Signature names never leave the validator"
    for unclear in (b"stream: ERROR", b"INSTREAM size limit exceeded. ERROR", b""):
        with pytest.raises(ValueError):
            scan_verdict(unclear)
