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

"""The S3 ops seam of hosted object storage and its boto3 implementation.

The client uses the default credential chain and ``AWS_S3_REGION``, as ``AWSFileRepository`` does, then
signs for the region the bucket is in. It never creates buckets and never touches ``AWS_S3_BUCKET_NAME``.
Presigned Create and Complete go out on botocore's own HTTP session, built with the proxy and CA bundle
settings the SDK client uses.
"""

from __future__ import annotations

import os
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

import boto3
import botocore.session
from botocore.awsrequest import AWSRequest
from botocore.client import BaseClient
from botocore.compat import ETree, XMLParseError
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from botocore.httpsession import URLLib3Session
from botocore.parsers import ResponseParserError, create_parser
from botocore.serialize import create_serializer
from botocore.utils import get_environ_proxies

from codemie.configs import config
from codemie.repository.hosted_applications.object_storage import (
    OBJECT_THREADS,
    ObjectExists,
    ObjectInfo,
    ObjectMissing,
    ObjectOps,
    ObjectOpsError,
    ObjectUnavailable,
    PresignExpired,
    UploadGone,
)

MAX_DELETE_KEYS = 1000
# A conditional create lost to another create of the same key: the key exists, or soon will.
NOT_CREATED_CODES = frozenset({"PreconditionFailed", "ConditionalRequestConflict"})
MISSING_CODES = frozenset({"NoSuchKey", "404", "NotFound"})
# Keys never hold control characters (``_parts``), so NUL safely joins a key marker and an upload-id marker.
UPLOAD_MARKER_SEPARATOR = "\0"
# Presigned requests: (connect, read) timeouts in seconds. botocore's session never retries: the writer decides.
PRESIGNED_TIMEOUT = (10, 120)
BUCKET_REGION_HEADER = "x-amz-bucket-region"


class S3Ops(ObjectOps, Protocol):
    """The full S3 seam. Every call is synchronous and runs on ``OBJECT_EXECUTOR``."""

    def presigned_create(self, key: str, expires_in: int) -> str:
        """Send a presigned CreateMultipartUpload; returns the UploadId."""

    def upload_part(self, key: str, upload_id: str, number: int, body: bytes) -> str:
        """Upload one part; returns its ETag."""

    def presigned_complete(self, key: str, upload_id: str, parts: list[tuple[int, str]], expires_in: int) -> None:
        """Send a presigned CompleteMultipartUpload with If-None-Match: *."""

    def abort(self, key: str, upload_id: str) -> None:
        """Abort one multipart upload."""

    def list_uploads(self, prefix: str, marker: str | None) -> tuple[list[tuple[str, str]], str | None]:
        """One page of open (key, upload id) pairs under ``prefix`` and the next marker, or None."""

    def server_date(self, key: str) -> datetime:
        """The S3 ``Date`` of a HEAD on ``key``."""

    def head(self, key: str) -> tuple[int, str]:
        """The size and ETag of ``key``; ``ObjectMissing`` when it does not exist."""


def _error(exc: ClientError) -> tuple[str, int | None]:
    return exc.response.get("Error", {}).get("Code", ""), exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")


def _typed(code: str, status: int | None) -> ObjectOpsError:
    """412 is an existing key and ``NoSuchUpload`` a gone upload; the rest stays ``ObjectUnavailable``.

    A 404 with another code (``NoSuchBucket``) or any 403 is a misconfiguration, not a gone upload.
    """
    if code == "PreconditionFailed" or status == 412:
        return ObjectExists(code, status)
    if code == "NoSuchUpload" or (status == 404 and not code):
        return UploadGone(code, status)
    return ObjectUnavailable(code, status)


def _expired(status: int, error: dict[str, Any]) -> bool:
    """S3 answers a presigned request past its expiry with 403 ``AccessDenied`` that names the expiry."""
    return status == 403 and ("Expires" in error or error.get("Message") == "Request has expired")


def _error_in_body(status: int, body: bytes) -> bool:
    """S3 can answer a Complete with 200 and an ``<Error>`` body; a body that does not parse fails too."""
    if status != 200 or not body:
        return False
    parser = ETree.XMLParser(target=ETree.TreeBuilder(), encoding="utf-8")
    try:
        parser.feed(body)
        return parser.close().tag == "Error"
    except XMLParseError:
        return True


def s3_client(**overrides: object) -> BaseClient:
    """The hosted storage client: SigV4 presigning, so a presigned Complete signs its If-None-Match."""
    arguments = {
        "region_name": config.AWS_S3_REGION or None,
        "config": Config(
            max_pool_connections=OBJECT_THREADS,
            signature_version="s3v4",
            # The multipart Create names no checksum algorithm, so parts carry none either.
            request_checksum_calculation="when_required",
        ),
    }
    return boto3.client("s3", **{**arguments, **overrides})


def bucket_region(client: BaseClient, bucket: str) -> str | None:
    """The region the bucket is in, from the ``x-amz-bucket-region`` header of a HeadBucket.

    S3 sends the header with a 200, and with the 301 or 403 that a client in another region gets.
    """
    try:
        headers = client.head_bucket(Bucket=bucket)["ResponseMetadata"]["HTTPHeaders"]
    except ClientError as exc:
        headers = exc.response.get("ResponseMetadata", {}).get("HTTPHeaders", {})
    except BotoCoreError as exc:
        raise ObjectUnavailable(type(exc).__name__) from exc
    return headers.get(BUCKET_REGION_HEADER) or None


def regional_client(bucket: str) -> BaseClient:
    """A client for the bucket's own region: a presigned URL signed for another region is refused."""
    client = s3_client()
    region = bucket_region(client, bucket)
    if region and region != client.meta.region_name:
        return s3_client(region_name=region)
    return client


def presigned_session(endpoint_url: str) -> URLLib3Session:
    """botocore's HTTP session with the settings the SDK client resolves for itself.

    Proxies come from ``HTTPS_PROXY`` / ``NO_PROXY``, the CA bundle from ``AWS_CA_BUNDLE`` (or the AWS config
    file), then ``REQUESTS_CA_BUNDLE``, then botocore's default bundle.
    """
    verify = (
        botocore.session.get_session().get_config_variable("ca_bundle") or os.environ.get("REQUESTS_CA_BUNDLE") or True
    )
    return URLLib3Session(
        verify=verify,
        proxies=get_environ_proxies(endpoint_url),
        timeout=PRESIGNED_TIMEOUT,
        max_pool_connections=OBJECT_THREADS,
    )


class BotoS3Ops:
    """``S3Ops`` on boto3. Presigned Create and Complete are sent on ``http`` (botocore's HTTP session).

    Without a ``client`` it makes one HeadBucket call to find the bucket's region, so build it on the
    object executor.
    """

    def __init__(self, bucket: str, client: BaseClient | None = None, http: URLLib3Session | None = None):
        if not bucket:
            raise ValueError("bucket must not be empty")
        self.bucket = bucket
        self.client = client or regional_client(bucket)
        self.http = http or presigned_session(self.client.meta.endpoint_url)

    def put_if_absent(self, key: str, body: bytes) -> bool:
        try:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=body, IfNoneMatch="*")
            return True
        except ClientError as exc:
            code, status = _error(exc)
            if code in NOT_CREATED_CODES or status in (409, 412):
                return False
            raise ObjectUnavailable(code, status) from exc
        except BotoCoreError as exc:
            raise ObjectUnavailable(type(exc).__name__) from exc

    def get_range(self, key: str, start: int, end: int) -> bytes:
        """Inclusive ``Range: bytes=start-end``; S3 clips it to the object's end and answers 416 past it."""
        try:
            body = self.client.get_object(Bucket=self.bucket, Key=key, Range=f"bytes={start}-{end}")["Body"]
            try:
                return body.read()
            finally:
                body.close()
        except ClientError as exc:
            code, status = _error(exc)
            if code in MISSING_CODES or status == 404:
                raise ObjectMissing(code, status) from exc
            if code == "InvalidRange" or status == 416:
                # Start at or past the end, including every range of a 0-byte object.
                return b""
            raise ObjectUnavailable(code, status) from exc
        except BotoCoreError as exc:
            raise ObjectUnavailable(type(exc).__name__) from exc

    def list_keys(self, prefix: str, token: str | None) -> tuple[list[ObjectInfo], str | None]:
        arguments = {"Bucket": self.bucket, "Prefix": prefix, "MaxKeys": MAX_DELETE_KEYS}
        if token is not None:
            arguments["ContinuationToken"] = token
        try:
            response = self.client.list_objects_v2(**arguments)
        except ClientError as exc:
            raise ObjectUnavailable(*_error(exc)) from exc
        except BotoCoreError as exc:
            raise ObjectUnavailable(type(exc).__name__) from exc
        page = [ObjectInfo(item["Key"], item["Size"], item["LastModified"]) for item in response.get("Contents", [])]
        return page, response.get("NextContinuationToken") if response.get("IsTruncated") else None

    def delete_keys(self, keys: list[str]) -> None:
        if len(keys) > MAX_DELETE_KEYS:
            raise ValueError("S3 deletes at most 1000 keys per request")
        if not keys:
            return
        try:
            response = self.client.delete_objects(
                Bucket=self.bucket, Delete={"Objects": [{"Key": key} for key in keys], "Quiet": True}
            )
        except ClientError as exc:
            raise ObjectUnavailable(*_error(exc)) from exc
        except BotoCoreError as exc:
            raise ObjectUnavailable(type(exc).__name__) from exc
        errors = response.get("Errors") or []
        if errors:
            raise ObjectUnavailable(errors[0].get("Code", ""))

    def server_date(self, key: str) -> datetime:
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key)
            return parsedate_to_datetime(response["ResponseMetadata"]["HTTPHeaders"]["date"])
        except ClientError as exc:
            code, status = _error(exc)
            if code in MISSING_CODES or status == 404:
                raise ObjectMissing(code, status) from exc
            raise ObjectUnavailable(code, status) from exc
        except (BotoCoreError, KeyError, TypeError, ValueError) as exc:
            raise ObjectUnavailable(type(exc).__name__) from exc

    def head(self, key: str) -> tuple[int, str]:
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key)
            return response["ContentLength"], response["ETag"]
        except ClientError as exc:
            code, status = _error(exc)
            if code in MISSING_CODES or status == 404:
                raise ObjectMissing(code, status) from exc
            raise ObjectUnavailable(code, status) from exc
        except (BotoCoreError, KeyError) as exc:
            raise ObjectUnavailable(type(exc).__name__) from exc

    def presigned_create(self, key: str, expires_in: int) -> str:
        """POST a presigned CreateMultipartUpload; botocore models no If-None-Match on it."""
        url = self._presign("create_multipart_upload", {"Bucket": self.bucket, "Key": key}, expires_in)
        upload_id = self._send("CreateMultipartUpload", url, b"", {}).get("UploadId")
        if not upload_id:
            raise ObjectUnavailable("MissingUploadId")
        return upload_id

    def upload_part(self, key: str, upload_id: str, number: int, body: bytes) -> str:
        try:
            response = self.client.upload_part(
                Bucket=self.bucket, Key=key, UploadId=upload_id, PartNumber=number, Body=body
            )
            return response["ETag"]
        except ClientError as exc:
            raise _typed(*_error(exc)) from exc
        except BotoCoreError as exc:
            raise ObjectUnavailable(type(exc).__name__) from exc

    def presigned_complete(self, key: str, upload_id: str, parts: list[tuple[int, str]], expires_in: int) -> None:
        """POST a presigned CompleteMultipartUpload whose signature covers ``If-None-Match: *``."""
        params = {"Bucket": self.bucket, "Key": key, "UploadId": upload_id, "IfNoneMatch": "*"}
        url = self._presign("complete_multipart_upload", params, expires_in)
        # The presigned URL carries no body: serialize the part list with botocore's own rest-xml serializer.
        model = self.client.meta.service_model.operation_model("CompleteMultipartUpload")
        upload = {"Parts": [{"PartNumber": number, "ETag": etag} for number, etag in parts]}
        body = create_serializer("rest-xml").serialize_to_request({**params, "MultipartUpload": upload}, model)["body"]
        self._send("CompleteMultipartUpload", url, body, {"If-None-Match": "*"})

    def abort(self, key: str, upload_id: str) -> None:
        try:
            self.client.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id)
        except ClientError as exc:
            code, status = _error(exc)
            if code == "NoSuchUpload" or status == 404:
                # Already completed or aborted: nothing is left open.
                return
            raise ObjectUnavailable(code, status) from exc
        except BotoCoreError as exc:
            raise ObjectUnavailable(type(exc).__name__) from exc

    def _presign(self, method: str, params: dict[str, str], expires_in: int) -> str:
        try:
            return self.client.generate_presigned_url(
                ClientMethod=method, Params=params, ExpiresIn=expires_in, HttpMethod="POST"
            )
        except BotoCoreError as exc:
            raise ObjectUnavailable(type(exc).__name__) from exc

    def _send(self, operation: str, url: str, body: bytes, headers: dict[str, str]) -> dict[str, Any]:
        """Send one presigned POST and parse it; an ``<Error>`` in a 200 body is that error, not success."""
        request = AWSRequest(method="POST", url=url, data=body, headers=headers).prepare()
        try:
            response = self.http.send(request)
        except BotoCoreError as exc:
            raise ObjectUnavailable(type(exc).__name__) from exc
        status, data = response.status_code, response.content
        output = self.client.meta.service_model.operation_model(operation).output_shape
        failed = status >= 300 or _error_in_body(status, data)
        # A 200 that carries an error is parsed as the server error it is.
        parsed_status = 500 if failed and status < 300 else status
        try:
            parsed = create_parser("rest-xml").parse(
                {"status_code": parsed_status, "headers": dict(response.headers), "body": data}, output
            )
        except ResponseParserError as exc:
            raise ObjectUnavailable(type(exc).__name__, status) from exc
        if failed:
            error = parsed.get("Error", {})
            if _expired(status, error):
                raise PresignExpired(error.get("Code", ""), status)
            raise _typed(error.get("Code", ""), status if status >= 300 else None)
        return parsed

    def list_uploads(self, prefix: str, marker: str | None) -> tuple[list[tuple[str, str]], str | None]:
        """One page of open uploads; the marker joins S3's key and upload-id markers with a NUL."""
        arguments = {"Bucket": self.bucket, "Prefix": prefix, "MaxUploads": MAX_DELETE_KEYS}
        if marker is not None:
            key_marker, _, upload_id_marker = marker.rpartition(UPLOAD_MARKER_SEPARATOR)
            arguments["KeyMarker"], arguments["UploadIdMarker"] = key_marker, upload_id_marker
        try:
            response = self.client.list_multipart_uploads(**arguments)
        except ClientError as exc:
            raise ObjectUnavailable(*_error(exc)) from exc
        except BotoCoreError as exc:
            raise ObjectUnavailable(type(exc).__name__) from exc
        page = [(item["Key"], item["UploadId"]) for item in response.get("Uploads", [])]
        if not response.get("IsTruncated"):
            return page, None
        key_marker, upload_id_marker = response.get("NextKeyMarker", ""), response.get("NextUploadIdMarker", "")
        return page, key_marker + UPLOAD_MARKER_SEPARATOR + upload_id_marker
