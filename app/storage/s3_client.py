"""app/storage/s3_client.py — Dual-endpoint S3 client for RustFS & Backblaze B2.

Maintains two boto3 S3 clients:
1. Internal client (S3_ENDPOINT_URL): used by FastAPI for bucket initialization,
   downloading files during ingestion, and deleting objects.
2. Public client (S3_PUBLIC_ENDPOINT_URL): used strictly for generating
   presigned PUT URLs so the browser can upload directly to object storage.
"""

import io
import logging
from typing import Any
from urllib.parse import urlparse

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import settings

logger = logging.getLogger(__name__)


def build_boto_config(
    addressing_style: str | None = None,
    connect_timeout: int = 3,
    read_timeout: int = 10,
    max_attempts: int = 2,
) -> Config:
    """Build standard SigV4 boto3 Config compatible with RustFS and Backblaze B2."""
    style = addressing_style or settings.s3_addressing_style
    return Config(
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        retries={"max_attempts": max_attempts},
        signature_version="s3v4",
        s3={"addressing_style": style},
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
        region_name: str | None = None,
        addressing_style: str | None = None,
    ) -> None:
        self.internal_endpoint = internal_endpoint or settings.s3_endpoint_url
        self.public_endpoint = public_endpoint or settings.s3_public_endpoint_url
        self.access_key = access_key or settings.s3_access_key
        self.secret_key = secret_key or settings.s3_secret_key
        self.bucket_name = bucket_name or settings.s3_bucket_name
        self.region_name = region_name or settings.s3_region
        self.addressing_style = addressing_style or settings.s3_addressing_style

        boto_config = build_boto_config(addressing_style=self.addressing_style)

        # Internal client for backend operations
        self._internal_client = boto3.client(
            "s3",
            endpoint_url=self.internal_endpoint,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region_name,
            config=boto_config,
        )

        # Public client strictly for generating presigned URLs
        self._public_client = boto3.client(
            "s3",
            endpoint_url=self.public_endpoint,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region_name,
            config=boto_config,
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
        """Check if the target bucket exists via head_bucket; create only if missing."""
        try:
            self._internal_client.head_bucket(Bucket=self.bucket_name)
            logger.info("S3 bucket '%s' exists.", self.bucket_name)
            return
        except ClientError as exc:
            error_code = str(exc.response.get("Error", {}).get("Code", ""))
            if error_code in ("404", "NoSuchBucket"):
                logger.info("Creating S3 bucket '%s'…", self.bucket_name)
                try:
                    self._internal_client.create_bucket(Bucket=self.bucket_name)
                    logger.info("S3 bucket '%s' created.", self.bucket_name)
                except ClientError as create_exc:
                    logger.warning(
                        "Could not create bucket '%s' (if pre-created in B2, ignore): %s",
                        self.bucket_name,
                        create_exc,
                    )
            else:
                logger.error("Failed to check bucket '%s': %s", self.bucket_name, exc)
                raise

    def configure_bucket_cors(self) -> None:
        """Apply CORS policy allowing PUT/GET/HEAD from configured browser origins.

        Omits explicit 'OPTIONS' in AllowedMethods for Backblaze B2 S3 compatibility
        (B2 handles preflight OPTIONS automatically from PUT/GET/HEAD). Non-fatal
        if scoped keys disallow PutBucketCors.
        """
        origins: list[str] = [o.strip() for o in settings.s3_cors_origins.split(",") if o.strip()]
        # Include scheme://netloc from public_endpoint if custom
        parsed = urlparse(self.public_endpoint)
        if parsed.scheme and parsed.netloc:
            pub_origin = f"{parsed.scheme}://{parsed.netloc}"
            if pub_origin not in origins:
                origins.append(pub_origin)

        cors_configuration = {
            "CORSRules": [
                {
                    "AllowedHeaders": ["*"],
                    "AllowedMethods": ["PUT", "GET", "HEAD"],
                    "AllowedOrigins": origins,
                    "ExposeHeaders": ["ETag", "x-amz-request-id"],
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

        Uses the public endpoint URL so the browser can upload directly.
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
        """Delete an object from storage using the internal client."""
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
        """Ensure bucket exists and apply CORS configuration."""
        self.ensure_bucket_exists()
        self.configure_bucket_cors()


# Default singleton instance
storage = S3Storage()
