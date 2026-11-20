# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

import pytest
from unittest.mock import MagicMock, patch
from codemie.datasource.loader.file_loader import FilesDatasourceLoader
from codemie_tools.base.file_object import FileObject
from langchain_core.documents import Document


@pytest.fixture
def sample_files_datasource_loader():
    return FilesDatasourceLoader(
        total_count_of_documents=10, files_paths=[MagicMock(name="file1.csv", owner="owner1")], csv_separator=","
    )


def test_files_datasource_loader_init(sample_files_datasource_loader):
    assert sample_files_datasource_loader.total_count_of_documents == 10
    assert sample_files_datasource_loader.file_repo is not None
    assert sample_files_datasource_loader.files_paths[0].owner == "owner1"
    assert sample_files_datasource_loader._csv_separator == ","


def test_fetch_remote_stats(sample_files_datasource_loader):
    """Test fetch_remote_stats returns both documents_count_key and total_documents."""
    stats = sample_files_datasource_loader.fetch_remote_stats()
    assert stats == {"documents_count_key": 10, "total_documents": 10}
    assert "documents_count_key" in stats
    assert "total_documents" in stats
    assert stats["documents_count_key"] == stats["total_documents"]


def test_lazy_load_csv(sample_files_datasource_loader):
    lazy_load_doc = []
    file = FileObject(name="file1.csv", content="col1,col2\nval1,val2\n", owner="owner1", mime_type="csv")
    sample_files_datasource_loader.file_repo.read_file = MagicMock(return_value=file)

    documents = sample_files_datasource_loader.lazy_load()

    for doc in documents:
        assert len(doc) == 1
        assert isinstance(doc[0], Document)
        assert doc[0].page_content == 'col1: val1\ncol2: val2'
        lazy_load_doc.append(doc)
    assert len(lazy_load_doc) == 1


def test_lazy_load_txt(sample_files_datasource_loader):
    file = FileObject(name="file1.txt", content="some,content\n", owner="owner1", mime_type="txt")
    sample_files_datasource_loader.file_repo.read_file = MagicMock(return_value=file)
    sample_files_datasource_loader.get_file_data = MagicMock(return_value=b"some,content\n")

    # Mock _lazy_load_documents to return a document
    sample_files_datasource_loader._lazy_load_documents = MagicMock(
        return_value=[Document(page_content="some,content\n", metadata={"source": "file1.txt"})]
    )

    documents = list(sample_files_datasource_loader.lazy_load())

    assert len(documents) == 1
    assert isinstance(documents[0], list)
    assert len(documents[0]) == 1
    assert documents[0][0].page_content == "some,content\n"


def test_lazy_load_documents(sample_files_datasource_loader):
    file = FileObject(name="file1.csv", content="col1,col2\nval1,val2\n", owner="owner1", mime_type="txt")
    sample_files_datasource_loader.file_repo.read_file = MagicMock(return_value=file)
    sample_files_datasource_loader.get_file_data = MagicMock(return_value=b"col1,col2\nval1,val2\n")

    documents = sample_files_datasource_loader._lazy_load_documents(file)

    assert len(documents) == 1
    assert isinstance(documents[0], Document)
    assert documents[0].page_content == "col1: val1\ncol2: val2"
    assert documents[0].metadata["source"] == "file1.csv"
    assert documents[0].metadata["row"] == 0


@pytest.mark.parametrize("file_ext", ["txt", "html", "epub", "ipynb", "msg", "docx", "xlsx"])
def test_lazy_load_with_new_loaders(sample_files_datasource_loader, file_ext):
    file = FileObject(name=f"file.{file_ext}", content="sample content", owner="owner1", mime_type=file_ext)
    sample_files_datasource_loader.file_repo.read_file = MagicMock(return_value=file)
    sample_files_datasource_loader.get_file_data = MagicMock(return_value=b"sample content")

    # Mock _lazy_load_documents to avoid actual file operations
    sample_files_datasource_loader._lazy_load_documents = MagicMock(return_value=[Document(page_content="test")])

    documents = list(sample_files_datasource_loader.lazy_load())

    # Verify _lazy_load_documents was called with correct parameters
    sample_files_datasource_loader._lazy_load_documents.assert_called_once_with(file)
    assert len(documents) == 1
    assert documents[0][0].page_content == "test"


def test_fetch_remote_stats_has_total_documents(sample_files_datasource_loader):
    """Test that total_documents is present in stats."""
    stats = sample_files_datasource_loader.fetch_remote_stats()

    assert "total_documents" in stats
    assert "documents_count_key" in stats


def test_fetch_remote_stats_keys_match_count(sample_files_datasource_loader):
    """Test both keys have the same value as total_count_of_documents."""
    sample_files_datasource_loader.total_count_of_documents = 15
    stats = sample_files_datasource_loader.fetch_remote_stats()

    assert stats["documents_count_key"] == 15
    assert stats["total_documents"] == 15


def test_get_load_stats_returns_zero_skipped_initially(sample_files_datasource_loader):
    stats = sample_files_datasource_loader.get_load_stats()
    assert stats == {"skipped_documents": 0, "failed_documents": 0}


def test_get_load_stats_increments_on_skipped_file(sample_files_datasource_loader):
    from codemie.datasource.exceptions import SkippedFileException
    from codemie_tools.base.file_object import FileObject
    from unittest.mock import patch

    file = FileObject(name="big.jpg", content=b"x" * 100, owner="owner1", mime_type="image/jpeg")

    with patch(
        "codemie.datasource.loader.file_loader.extract_documents_from_bytes",
        side_effect=SkippedFileException(file_name="big.jpg", reason="too large"),
    ):
        sample_files_datasource_loader._lazy_load_documents(file)

    assert sample_files_datasource_loader.get_load_stats() == {"skipped_documents": 1, "failed_documents": 0}


def test_get_load_stats_counts_multiple_skips(sample_files_datasource_loader):
    from codemie.datasource.exceptions import SkippedFileException
    from codemie_tools.base.file_object import FileObject
    from unittest.mock import patch

    file = FileObject(name="big.jpg", content=b"x", owner="owner1", mime_type="image/jpeg")

    with patch(
        "codemie.datasource.loader.file_loader.extract_documents_from_bytes",
        side_effect=SkippedFileException(file_name="big.jpg", reason="too large"),
    ):
        sample_files_datasource_loader._lazy_load_documents(file)
        sample_files_datasource_loader._lazy_load_documents(file)
        sample_files_datasource_loader._lazy_load_documents(file)

    assert sample_files_datasource_loader.get_load_stats() == {"skipped_documents": 3, "failed_documents": 0}


def test_lazy_load_documents_counts_empty_extract_as_skipped(sample_files_datasource_loader):
    from codemie_tools.base.file_object import FileObject
    from unittest.mock import patch

    file = FileObject(name="img.gif", content=b"x", owner="owner1", mime_type="image/gif")

    with patch(
        "codemie.datasource.loader.file_loader.extract_documents_from_bytes",
        return_value=[],
    ):
        result = sample_files_datasource_loader._lazy_load_documents(file)

    assert result == []
    assert sample_files_datasource_loader.get_load_stats() == {"skipped_documents": 1, "failed_documents": 0}


def test_lazy_load_documents_does_not_double_count_skipped_exception(sample_files_datasource_loader):
    from codemie.datasource.exceptions import SkippedFileException
    from codemie_tools.base.file_object import FileObject
    from unittest.mock import patch

    file = FileObject(name="big.jpg", content=b"x", owner="owner1", mime_type="image/jpeg")

    with patch(
        "codemie.datasource.loader.file_loader.extract_documents_from_bytes",
        side_effect=SkippedFileException(file_name="big.jpg", reason="too large"),
    ):
        sample_files_datasource_loader._lazy_load_documents(file)

    assert sample_files_datasource_loader.get_load_stats() == {"skipped_documents": 1, "failed_documents": 0}


def test_lazy_load_documents_returns_empty_list_on_skip(sample_files_datasource_loader):
    from codemie.datasource.exceptions import SkippedFileException
    from codemie_tools.base.file_object import FileObject
    from unittest.mock import patch

    file = FileObject(name="big.jpg", content=b"x", owner="owner1", mime_type="image/jpeg")

    with patch(
        "codemie.datasource.loader.file_loader.extract_documents_from_bytes",
        side_effect=SkippedFileException(file_name="big.jpg", reason="too large"),
    ):
        result = sample_files_datasource_loader._lazy_load_documents(file)

    assert result == []


class TestLoadDocsParallel:
    def _make_loader(self, file_datas):
        loader = FilesDatasourceLoader(
            total_count_of_documents=len(file_datas),
            files_paths=file_datas,
            csv_separator=",",
            request_uuid="uuid-1",
        )
        return loader

    def test_calls_maybe_pool_submit_per_file(self):
        """_load_docs_parallel routes each file through maybe_pool_submit."""
        file1 = FileObject(name="a.txt", content=b"x", owner="u1", mime_type="txt")
        file2 = FileObject(name="b.txt", content=b"y", owner="u1", mime_type="txt")

        fd1 = MagicMock()
        fd1.name = "a.txt"
        fd1.owner = "u1"
        fd2 = MagicMock()
        fd2.name = "b.txt"
        fd2.owner = "u1"

        loader = self._make_loader([fd1, fd2])
        loader.file_repo.read_file = MagicMock(side_effect=[file1, file2])

        docs1 = [Document(page_content="a")]
        docs2 = [Document(page_content="b")]

        with patch(
            "codemie.datasource.loader.file_loader.maybe_pool_submit",
            side_effect=[docs1, docs2],
        ) as mock_submit:
            results = list(loader._load_docs_parallel())

        assert mock_submit.call_count == 2
        assert results == [docs1, docs2]

    def test_error_per_file_is_logged_and_skipped(self):
        """If maybe_pool_submit raises for a file, error is logged and loop continues."""
        fd1 = MagicMock()
        fd1.name = "bad.txt"
        fd1.owner = "u1"

        loader = self._make_loader([fd1])
        file1 = FileObject(name="bad.txt", content=b"x", owner="u1", mime_type="txt")
        loader.file_repo.read_file = MagicMock(return_value=file1)

        with (
            patch(
                "codemie.datasource.loader.file_loader.maybe_pool_submit",
                side_effect=RuntimeError("pool exploded"),
            ),
            patch("codemie.datasource.loader.file_loader.logger") as mock_logger,
        ):
            results = list(loader._load_docs_parallel())

        assert results == []
        mock_logger.error.assert_called_once()
        assert "bad.txt" in str(mock_logger.error.call_args)

    def test_empty_result_counted_as_skipped(self):
        """_load_docs_parallel counts empty-result files as skipped."""
        fd1 = MagicMock()
        fd1.name = "img.gif"
        fd1.owner = "u1"

        loader = self._make_loader([fd1])
        file1 = FileObject(name="img.gif", content=b"x", owner="u1", mime_type="image/gif")
        loader.file_repo.read_file = MagicMock(return_value=file1)

        with patch(
            "codemie.datasource.loader.file_loader.maybe_pool_submit",
            return_value=[],
        ):
            results = list(loader._load_docs_parallel())

        assert results == [[]]
        assert loader.get_load_stats() == {"skipped_documents": 1, "failed_documents": 0}

    def test_skipped_file_exception_counted_as_skipped(self):
        """_load_docs_parallel counts SkippedFileException as skipped."""
        from codemie.datasource.exceptions import SkippedFileException

        fd1 = MagicMock()
        fd1.name = "big.jpg"
        fd1.owner = "u1"

        loader = self._make_loader([fd1])
        file1 = FileObject(name="big.jpg", content=b"x", owner="u1", mime_type="image/jpeg")
        loader.file_repo.read_file = MagicMock(return_value=file1)

        with patch(
            "codemie.datasource.loader.file_loader.maybe_pool_submit",
            side_effect=SkippedFileException(file_name="big.jpg", reason="too large"),
        ):
            results = list(loader._load_docs_parallel())

        assert results == [[]]
        assert loader.get_load_stats() == {"skipped_documents": 1, "failed_documents": 0}

    def test_unreadable_workbook_error_propagates(self):
        """A corrupt/unreadable workbook must NOT be swallowed by the parallel path's
        blanket except; it propagates so the file is surfaced as failed, consistent with
        the serial path (EPMCDME-11738 CR-001 / AC6)."""
        from codemie.datasource.exceptions import UnreadableWorkbookError

        fd1 = MagicMock()
        fd1.name = "report.xlsb"
        fd1.owner = "u1"

        loader = self._make_loader([fd1])
        file1 = FileObject(name="report.xlsb", content=b"corrupt", owner="u1", mime_type="application/octet-stream")
        loader.file_repo.read_file = MagicMock(return_value=file1)

        with patch(
            "codemie.datasource.loader.file_loader.maybe_pool_submit",
            side_effect=UnreadableWorkbookError("report.xlsb", "boom"),
        ):
            with pytest.raises(UnreadableWorkbookError) as exc_info:
                list(loader._load_docs_parallel())

        assert "report.xlsb" in str(exc_info.value)
        # A failed workbook is accounted as failed, not silently counted as skipped.
        assert loader.get_load_stats() == {"skipped_documents": 0, "failed_documents": 0}


class TestLoadDocsParallelTimeouts:
    def _make_loader(self, names):
        file_datas = []
        for name in names:
            fd = MagicMock()
            fd.name = name
            fd.owner = "u1"
            file_datas.append(fd)
        loader = FilesDatasourceLoader(
            total_count_of_documents=len(file_datas),
            files_paths=file_datas,
            csv_separator=",",
            request_uuid="uuid-1",
        )
        loader.file_repo.read_file = MagicMock(
            side_effect=lambda name, owner: FileObject(name=name, content=b"x", owner=owner, mime_type="txt")
        )
        return loader

    def test_timed_out_file_is_counted_as_failed_and_others_are_yielded(self):
        from codemie.datasource.exceptions import FileParsingTimeoutError

        loader = self._make_loader(["a.txt", "slow.xlsb", "c.txt"])
        docs_a = [Document(page_content="a")]
        docs_c = [Document(page_content="c")]

        with (
            patch(
                "codemie.datasource.loader.file_loader.maybe_pool_submit",
                side_effect=[docs_a, FileParsingTimeoutError(1800), docs_c],
            ),
            patch("codemie.datasource.loader.file_loader.logger") as mock_logger,
        ):
            results = list(loader._load_docs_parallel())

        assert results == [docs_a, docs_c]
        assert loader.get_load_stats() == {"skipped_documents": 0, "failed_documents": 1}
        mock_logger.error.assert_called_once()
        assert "slow.xlsb" in str(mock_logger.error.call_args)

    def test_all_files_timing_out_raises_all_files_timed_out_error(self):
        from codemie.datasource.exceptions import AllFilesTimedOutError, FileParsingTimeoutError

        loader = self._make_loader(["a.xlsb", "b.xlsb"])

        with patch(
            "codemie.datasource.loader.file_loader.maybe_pool_submit",
            side_effect=FileParsingTimeoutError(1800),
        ):
            with pytest.raises(AllFilesTimedOutError) as exc_info:
                list(loader._load_docs_parallel())

        assert str(exc_info.value) == "File parsing timed out for 2 file(s); nothing was indexed"
        assert loader.get_load_stats() == {"skipped_documents": 0, "failed_documents": 2}

    def test_timeout_with_other_files_skipped_still_raises(self):
        from codemie.datasource.exceptions import AllFilesTimedOutError, FileParsingTimeoutError

        loader = self._make_loader(["a.gif", "b.xlsb"])

        with patch(
            "codemie.datasource.loader.file_loader.maybe_pool_submit",
            side_effect=[[], FileParsingTimeoutError(1800)],
        ):
            with pytest.raises(AllFilesTimedOutError):
                list(loader._load_docs_parallel())

    def test_other_file_errors_do_not_raise_all_timed_out(self):
        loader = self._make_loader(["a.txt"])

        with patch(
            "codemie.datasource.loader.file_loader.maybe_pool_submit",
            side_effect=RuntimeError("boom"),
        ):
            assert list(loader._load_docs_parallel()) == []

        assert loader.get_load_stats() == {"skipped_documents": 0, "failed_documents": 0}

    def test_all_files_timed_out_error_is_picklable(self):
        import pickle

        from codemie.datasource.exceptions import AllFilesTimedOutError

        restored = pickle.loads(pickle.dumps(AllFilesTimedOutError(3)))

        assert str(restored) == "File parsing timed out for 3 file(s); nothing was indexed"
        assert restored.timed_out_count == 3
