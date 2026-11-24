"""app/storage/s3_client.py — Dual-endpoint S3 client for RustFS object storage.

Maintains two boto3 S3 clients:
1. Internal client (S3_ENDPOINT_URL): used by FastAPI for bucket initialization,
   downloading files during ingestion, and deleting objects.
2. Public client (S3_PUBLIC_ENDPOINT_URL): used strictly for generating
   presigned PUT URLs so the browser can upload directly to RustFS.
"""

import io
import logging
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import settings

logger = logging.getLogger(__name__)

# Standard timeout and retry config for S3 operations
_BOTO_CONFIG = Config(
    connect_timeout=3,
    read_timeout=10,
    retries={"max_attempts": 2},
)


class S3Storage:
    """Manages S3 object storage operations across dual endpoints."""

    def __init__(
        self,
        internal_endpoint: str | None = None,
        public_endpoint: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        bucket_name: str | None = None,
    ) -> None:
        self.internal_endpoint = internal_endpoint or settings.s3_endpoint_url
        self.public_endpoint = public_endpoint or settings.s3_public_endpoint_url
        self.access_key = access_key or settings.s3_access_key
        self.secret_key = secret_key or settings.s3_secret_key
        self.bucket_name = bucket_name or settings.s3_bucket_name

        # Internal client for backend operations
        self._internal_client = boto3.client(
            "s3",
            endpoint_url=self.internal_endpoint,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            config=_BOTO_CONFIG,
        )

        # Public client strictly for generating presigned URLs
        self._public_client = boto3.client(
            "s3",
            endpoint_url=self.public_endpoint,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            config=_BOTO_CONFIG,
        )

    @property
    def internal_client(self) -> Any:
        """Internal boto3 client."""
        return self._internal_client

    @property
    def public_client(self) -> Any:
        """Public boto3 client."""
        return self._public_client

    def ensure_bucket_exists(self) -> None:
        """Check if the target bucket exists, and create it if not."""
        try:
            self._internal_client.head_bucket(Bucket=self.bucket_name)
            logger.info("RustFS bucket '%s' exists.", self.bucket_name)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code in ("404", "NoSuchBucket"):
                logger.info("Creating RustFS bucket '%s'…", self.bucket_name)
                self._internal_client.create_bucket(Bucket=self.bucket_name)
                logger.info("RustFS bucket '%s' created.", self.bucket_name)
            else:
                logger.error("Failed to check/create bucket '%s': %s", self.bucket_name, exc)
                raise

    def configure_bucket_cors(self) -> None:
        """Apply CORS policy allowing PUT and OPTIONS from browser origins."""
        cors_configuration = {
            "CORSRules": [
                {
                    "AllowedHeaders": ["*"],
                    "AllowedMethods": ["PUT", "OPTIONS", "GET", "HEAD"],
                    "AllowedOrigins": [
                        "http://localhost:8000",
                        "http://127.0.0.1:8000",
                    ],
                    "MaxAgeSeconds": 3000,
                }
            ]
        }
        try:
            self._internal_client.put_bucket_cors(
                Bucket=self.bucket_name,
                CORSConfiguration=cors_configuration,
            )
            logger.info("CORS policy applied to bucket '%s'.", self.bucket_name)
        except ClientError as exc:
            logger.warning("Could not apply CORS to bucket '%s': %s", self.bucket_name, exc)

    def generate_presigned_put_url(
        self,
        object_key: str,
        content_type: str,
        expires_in: int = 300,
    ) -> str:
        """Generate a short-lived presigned PUT URL for direct browser uploads.

        Uses the public endpoint URL so the browser on the host machine can connect.
        """
        url: str = self._public_client.generate_presigned_url(
            ClientMethod="put_object",
            Params={
                "Bucket": self.bucket_name,
                "Key": object_key,
                "ContentType": content_type,
            },
            ExpiresIn=expires_in,
        )
        return url

    def get_object_bytes(self, object_key: str) -> bytes:
        """Download raw object bytes using the internal client."""
        response = self._internal_client.get_object(
            Bucket=self.bucket_name,
            Key=object_key,
        )
        return response["Body"].read()

    def put_object_bytes(
        self,
        object_key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> None:
        """Upload raw bytes using the internal client (e.g. for testing/internal use)."""
        self._internal_client.put_object(
            Bucket=self.bucket_name,
            Key=object_key,
            Body=io.BytesIO(data),
            ContentType=content_type,
        )

    def delete_object(self, object_key: str) -> bool:
        """Delete an object from RustFS using the internal client."""
        try:
            self._internal_client.delete_object(
                Bucket=self.bucket_name,
                Key=object_key,
            )
            logger.info("Deleted object '%s' from bucket '%s'.", object_key, self.bucket_name)
            return True
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code in ("404", "NoSuchKey"):
                return True
            logger.error("Failed to delete object '%s': %s", object_key, exc)
            return False

    def init_storage(self) -> None:
        """Ensure bucket exists and configure CORS."""
        self.ensure_bucket_exists()
        self.configure_bucket_cors()


# Default singleton instance
storage = S3Storage()
