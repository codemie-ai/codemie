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

import hashlib
import os
from pathlib import Path

import pytest

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.service.hosted_applications.validator_client import ValidatorClient


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content,expected",
    [
        (b"<html><body>Safe static idea</body></html>", None),
        (b'X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*', "MALWARE_DETECTED"),
        (b"<html>AKIA1234567890ABCDEF</html>", "SECRET_DETECTED"),
    ],
)
async def test_real_shared_validator(content, expected):
    secret_file = os.environ.get("HOSTED_TEST_VALIDATOR_ENV")
    if not secret_file:
        pytest.skip("Set HOSTED_TEST_VALIDATOR_ENV for real isolated validator/scanner tests")
    fields = dict(line.split("=", 1) for line in Path(secret_file).read_text().splitlines())
    client = ValidatorClient("http://127.0.0.1:18091", fields["VALIDATOR_SERVICE_KEY"])

    async def chunks():
        yield content

    try:
        if expected:
            with pytest.raises(HostedApplicationError) as error:
                async with client.inspect(
                    chunks(), size=len(content), digest=hashlib.sha256(content).digest(), kind="html"
                ):
                    pytest.fail("Unsafe upload passed validation")
            assert error.value.code == expected
        else:
            async with client.inspect(
                chunks(), size=len(content), digest=hashlib.sha256(content).digest(), kind="html"
            ) as (identifier, candidate, scanner):
                assert candidate.entries == ["index.html"] and scanner["signature_version"]
                data = b"".join([chunk async for chunk in client.file_chunks(identifier, 0, candidate.files[0])])
                assert data == content
            response = await client.client.get(
                client.base_url + f"/results/{identifier}/files/0", headers=client.headers
            )
            assert response.status_code == 503
        denied = await client.client.get(client.base_url + "/health", headers={"x-validator-key": "invalid"})
        assert denied.status_code == 503
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_real_shared_validator_rejects_zip_traversal_without_claiming_an_outage():
    import io
    import zipfile

    secret_file = os.environ.get("HOSTED_TEST_VALIDATOR_ENV")
    if not secret_file:
        pytest.skip("Set HOSTED_TEST_VALIDATOR_ENV for real isolated validator/scanner tests")
    fields = dict(line.split("=", 1) for line in Path(secret_file).read_text().splitlines())
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('index.html', '<html>Safe entry</html>')
        archive.writestr('../outside.html', '<html>Unsafe path</html>')
    content = buffer.getvalue()
    client = ValidatorClient("http://127.0.0.1:18091", fields["VALIDATOR_SERVICE_KEY"])

    async def chunks():
        yield content

    try:
        with pytest.raises(HostedApplicationError) as error:
            async with client.inspect(chunks(), size=len(content), digest=hashlib.sha256(content).digest(), kind='zip'):
                pytest.fail('Traversal ZIP passed validation')
        assert error.value.code == 'INVALID_ARCHIVE_PATH' and error.value.status == 422
    finally:
        await client.close()
