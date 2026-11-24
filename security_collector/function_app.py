"""Receive Agent Gateway OTLP access logs and archive security metadata in Blob Storage."""

import gzip
import hashlib
import io
import json
import logging
import os
import zlib
from datetime import datetime, timezone
from functools import lru_cache

import azure.functions as func
from azure.core.exceptions import AzureError, ResourceExistsError
from azure.identity import ManagedIdentityCredential
from azure.storage.blob import BlobServiceClient, ContentSettings, StorageErrorCode
from google.protobuf.message import DecodeError
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
    ExportLogsServiceRequest,
    ExportLogsServiceResponse,
)

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)
MAX_BATCH_BYTES = 4 * 1024 * 1024
CONTENT_TYPE = "application/x-protobuf"
AUDIT_ATTRIBUTES = frozenset({
    "http.request.method", "http.response.status_code", "url.path", "client.address",
    "mcp.method.name", "mcp.target", "mcp.error.code", "gen_ai.tool.name", "reason",
    "security.caller.app_id", "security.guardrail.rejected",
})
SCALAR_TYPES = {"string_value", "bool_value", "int_value"}


def attributes(items, allowed):
    return {
        item.key: getattr(item.value, kind)
        for item in items
        if item.key in allowed and (kind := item.value.WhichOneof("value")) in SCALAR_TYPES
    }


def audit_events(batch):
    for resource in batch.resource_logs:
        source = attributes(resource.resource.attributes, {"service.name", "service.version"})
        for scope in resource.scope_logs:
            if scope.scope.name != "agentgateway.access":
                continue
            for record in scope.log_records:
                yield {
                    "time_unix_nano": record.time_unix_nano or record.observed_time_unix_nano,
                    "trace_id": record.trace_id.hex() or None,
                    "span_id": record.span_id.hex() or None,
                    "source": source,
                    "attributes": attributes(record.attributes, AUDIT_ATTRIBUTES),
                }


@lru_cache(maxsize=1)
def archive():
    service = BlobServiceClient(os.environ["SECURITY_BLOB_ENDPOINT"], ManagedIdentityCredential())
    return service.get_container_client(os.environ["SECURITY_BLOB_CONTAINER"])


@app.route(route="v1/logs", methods=["POST"])
def collect_security_logs(req: func.HttpRequest) -> func.HttpResponse:
    if req.headers.get("Content-Type", "").partition(";")[0].strip().lower() != CONTENT_TYPE:
        return func.HttpResponse(status_code=415)

    encoding = req.headers.get("Content-Encoding", "identity").strip().lower()
    if encoding not in {"identity", "gzip"}:
        return func.HttpResponse(status_code=415)

    data = req.get_body()
    if len(data) > MAX_BATCH_BYTES:
        return func.HttpResponse(status_code=413)
    try:
        if encoding == "gzip":
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                data = stream.read(MAX_BATCH_BYTES + 1)
            if len(data) > MAX_BATCH_BYTES:
                return func.HttpResponse(status_code=413)
        batch = ExportLogsServiceRequest.FromString(data)
    except (DecodeError, OSError, EOFError, zlib.error):
        return func.HttpResponse(status_code=400)

    events = list(audit_events(batch))
    if events:
        payload = "".join(json.dumps(event, sort_keys=True) + "\n" for event in events).encode()
        created = datetime.fromtimestamp(events[0]["time_unix_nano"] // 1_000_000_000, timezone.utc)
        blob_name = f"{created:%Y/%m/%d}/{hashlib.sha256(payload).hexdigest()}.ndjson"
        try:
            archive().upload_blob(
                name=blob_name,
                data=payload,
                overwrite=False,
                content_settings=ContentSettings(content_type="application/x-ndjson"),
            )
        except AzureError as exc:
            # A previous delivery may already have committed this batch.
            duplicate = (
                isinstance(exc, ResourceExistsError)
                and exc.error_code == StorageErrorCode.blob_already_exists
            )
            if not duplicate:
                logging.exception("Could not write Agent Gateway security events to Blob Storage")
                return func.HttpResponse(status_code=503, headers={"Retry-After": "5"})

    return func.HttpResponse(ExportLogsServiceResponse().SerializeToString(), mimetype=CONTENT_TYPE)
