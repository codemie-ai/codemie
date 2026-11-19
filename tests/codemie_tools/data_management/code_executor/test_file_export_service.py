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

from unittest.mock import MagicMock, patch

from codemie_tools.base.artifact_publishing import PublishContext, PublishResult
from codemie_tools.data_management.code_executor.file_export_service import FileExportService

USER_ID = "user-123"
CTX = PublishContext(user_id=USER_ID, conversation_id="conv-1", turn_id="turn-1")


def _publisher(result):
    publisher = MagicMock()
    publisher.publish.return_value = result
    return publisher


def test_store_exported_bytes_publishes_and_returns_link():
    publisher = _publisher(PublishResult(link="sandbox:/v1/files/ENC", name="report.csv"))
    service = FileExportService(user_id=USER_ID, publisher=publisher, publish_context=CTX)

    result = service.store_exported_bytes("out/report.csv", b"a,b,c")

    assert result == " File 'report.csv', URL `sandbox:/v1/files/ENC`"
    publisher.publish.assert_called_once_with(CTX, "out/report.csv", content=b"a,b,c")


def test_store_exported_bytes_surfaces_publisher_reason_instead_of_dropping():
    """Regression: a refused export must not vanish silently (AC #7)."""
    reason = "`huge.xlsx` exceeds the maximum upload size of 4 bytes and was not saved"
    service = FileExportService(
        user_id=USER_ID, publisher=_publisher(PublishResult(name="huge.xlsx", reason=reason)), publish_context=CTX
    )

    result = service.store_exported_bytes("huge.xlsx", b"12345")

    assert result == f" File 'huge.xlsx' was not stored: {reason}"


def test_store_exported_bytes_without_publisher_is_not_delivered():
    service = FileExportService(user_id=USER_ID)

    result = service.store_exported_bytes("report.csv", b"a,b,c")

    assert "report.csv" in result
    assert "not delivered: storage not configured" in result
    assert "sandbox:" not in result


def test_collect_single_file_without_owner_returns_none_and_warns():
    service = FileExportService(user_id="")
    session = MagicMock()

    with (
        patch("codemie_tools.data_management.code_executor.file_export_service.FileObject") as file_object_cls,
        patch("codemie_tools.data_management.code_executor.file_export_service.logger.warning") as warning,
    ):
        result = service._collect_single_file(session, "out/report.csv", "/workdir", "/tmp", 1)

    assert result is None
    file_object_cls.assert_not_called()
    warning.assert_called_once()


def test_export_single_file_publishes_by_path_so_size_is_checked_before_reading(tmp_path):
    publisher = _publisher(PublishResult(link="sandbox:/v1/files/ENC", name="big.xlsx"))
    service = FileExportService(user_id=USER_ID, publisher=publisher, publish_context=CTX)
    session = MagicMock()

    with patch("builtins.open") as opened:
        result = service._export_single_file(session, "out/big.xlsx", "/work", str(tmp_path), 1)

    assert result == " File 'big.xlsx', URL `sandbox:/v1/files/ENC`"
    opened.assert_not_called()
    publisher.publish.assert_called_once_with(CTX, "big.xlsx", path=str(tmp_path / "big.xlsx"))
