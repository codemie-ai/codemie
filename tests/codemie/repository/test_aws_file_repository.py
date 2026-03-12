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
from unittest.mock import patch, MagicMock
from codemie.repository.aws_file_repository import AWSFileRepository

BUCKET_NAME = "test_bucket"
FILE_NAME = "test_file.txt"
DIR_NAME = "test_directory"
CONTENT = b"Sample Content"
MIME_TYPE = "text/plain"
OWNER = "test_owner"
FULL_KEY_NAME = f"{OWNER}/{FILE_NAME}"
FULL_DIR_NAME = f"{OWNER}/{DIR_NAME}/"


@pytest.fixture
def aws_repo():
    with patch('boto3.client') as mock_boto_client:
        # Create a mock S3 client
        mock_s3_client = MagicMock()
        mock_boto_client.return_value = mock_s3_client

        # Create the repository with explicit bucket name
        repo = AWSFileRepository(root_bucket=BUCKET_NAME)

        yield repo, mock_s3_client


def test_write_file(aws_repo):
    repo, mock_s3_client = aws_repo
    repo.write_file(FILE_NAME, MIME_TYPE, OWNER, CONTENT)
    mock_s3_client.put_object.assert_called_once_with(
        Bucket=BUCKET_NAME, Key=FULL_KEY_NAME, Body=CONTENT, ContentType=MIME_TYPE
    )


def test_read_file(aws_repo):
    repo, mock_s3_client = aws_repo
    mock_s3_client.get_object.return_value = {
        'Body': MagicMock(read=MagicMock(return_value=CONTENT)),
        'ContentType': MIME_TYPE,
    }
    file_object = repo.read_file(FILE_NAME, OWNER)
    assert file_object.content == CONTENT
    assert file_object.mime_type == MIME_TYPE
    assert file_object.owner == OWNER
    assert file_object.name == FILE_NAME


def test_create_directory(aws_repo):
    repo, mock_s3_client = aws_repo
    repo.create_directory(DIR_NAME, OWNER)
    mock_s3_client.put_object.assert_called_once_with(Bucket=BUCKET_NAME, Key=FULL_DIR_NAME)


def test_write_csv_file(aws_repo):
    repo, mock_s3_client = aws_repo
    repo.write_file('test_file.csv', 'text/csv', OWNER, b'name,age\nAlice,30')
    mock_s3_client.put_object.assert_called_once_with(
        Bucket=BUCKET_NAME, Key=f'{OWNER}/test_file.csv', Body=b'name,age\nAlice,30', ContentType='text/csv'
    )


def test_write_pdf_file(aws_repo):
    repo, mock_s3_client = aws_repo
    repo.write_file('test_file.pdf', 'application/pdf', OWNER, b'%PDF-1.4...')
    mock_s3_client.put_object.assert_called_once_with(
        Bucket=BUCKET_NAME, Key=f'{OWNER}/test_file.pdf', Body=b'%PDF-1.4...', ContentType='application/pdf'
    )
