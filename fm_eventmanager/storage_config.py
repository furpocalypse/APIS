"""Module providing storage config and options for settings.py."""

from os import environ as _environ, getenv as _getenv


def get_storage_config():
    """Provides configuration of cloud (or local) storage for upload fields.

    Checks environment variables to determine the storage backend to use and
    associated settings values. In `settings.py`, simply import `storage_config`
    and assign it to the default storage.

    If the environment variable `DEFAULT_STORAGE_METHOD` is unset or doesn't
    match a supported storage backend, it will fall back on
    `django.core.files.storage.FileSystemStorage`. Currently this module
    supports:

    * Amazon S3 (`storages.backends.s3.S3Storage`)
    * Azure Storage (`storages.backend.azure_storage.AzureStorage`)

    Additional support can be added as necessary.

    Refer to .env.production.example in the project root and django-storages's
    documentation for more information on storage settings.

    Example:

    .. code-block::
        from fm_eventmanager.storage_config import get_storage_config

        STORAGE = {
            "DEFAULT": get_storage_config()
        }

    """

    storage_backend = _getenv(
        "DEFAULT_STORAGE_METHOD", "django.core.files.storage.FileSystemStorage"
    )

    storage_options = None

    storage_config = {"BACKEND": storage_backend}
    if storage_backend == "storages.backends.s3.S3Storage":
        storage_options = {
            "bucket_name": _getenv("AWS_STORAGE_BUCKET_NAME"),
        }

        if "AWS_LOCATION" in _environ:
            storage_options["location"] = _getenv("AWS_LOCATION")
        if "AWS_DEFAULT_ACL" in _environ:
            storage_options["default_acl"] = _getenv("AWS_DEFAULT_ACL")
        if "AWS_QUERYSTRING_AUTH" in _environ:
            storage_options["querystring_auth"] = _getenv("AWS_QUERYSTRING_AUTH")
        if "AWS_S3_MAX_MEMORY_SIZE" in _environ:
            storage_options["max_memory_size"] = _getenv("AWS_S3_MAX_MEMORY_SIZE")
        if "AWS_QUERYSTRING_EXPIRE" in _environ:
            storage_options["querystring_expire"] = _getenv("AWS_QUERYSTRING_EXPIRE")
        if "AWS_S3_URL_PROTOCOL" in _environ:
            storage_options["url_protocol"] = _getenv("AWS_S3_URL_PROTOCOL")
        if "AWS_S3_FILE_OVERWRITE" in _environ:
            storage_options["file_overwrite"] = _getenv("AWS_S3_FILE_OVERWRITE")
        if "AWS_IS_GZIPPED" in _environ:
            storage_options["gzip"] = _getenv("AWS_IS_GZIPPED")
        if "AWS_GZIP_CONTENT_TYPES" in _environ:
            storage_options["gzip_content_types"] = ",".split(_getenv("AWS_GZIP_CONTENT_TYPES"))
        if "AWS_S3_REGION_NAME" in _environ:
            storage_options["region_name"] = _getenv("AWS_S3_REGION_NAME")
        if "AWS_S3_USE_SSL" in _environ:
            storage_options["use_ssl"] = _getenv("AWS_S3_USE_SSL")
        if "AWS_S3_VERIFY" in _environ:
            storage_options["verify"] = _getenv("AWS_S3_VERIFY")
        if "AWS_S3_ENDPOINT_URL" in _environ:
            storage_options["endpoint_url"] = _getenv("AWS_S3_ENDPOINT_URL")
        if "AWS_S3_ADDRESSING_STYLE" in _environ:
            storage_options["addressing_style"] = _getenv("AWS_S3_ADDRESSING_STYLE")

    if storage_backend == "storages.backends.azure_storage.AzureStorage":
        storage_options = {"azure_container": _getenv("AZURE_CONTAINER")}

        if "AZURE_CONNECTION_STRING" in _environ:
            storage_options["connection_string"] = _getenv("AZURE_CONNECTION_STRING")
        if "AZURE_ACCOUNT_NAME" in _environ:
            storage_options["account_name"] = _getenv("AZURE_ACCOUNT_NAME")
        if "AZURE_ACCOUNT_KEY" in _environ:
            storage_options["account_key"] = _getenv("AZURE_ACCOUNT_KEY")
        if "AZURE_TOKEN_CREDENTIAL" in _environ:
            storage_options["token_credential"] = _getenv("AZURE_TOKEN_CREDENTIAL")
        if "AZURE_SAS_TOKEN" in _environ:
            storage_options["sas_token"] = _getenv("AZURE_SAS_TOKEN")
        if "AZURE_LOCATION" in _environ:
            storage_options["azure_location"] = _getenv("AZURE_LOCATION")
        if "AZURE_SSL" in _environ:
            storage_options["azure_ssl"] = _getenv("AZURE_SSL")
        if "AZURE_UPLOAD_MAX_CONN" in _environ:
            storage_options["upload_max_conn"] = _getenv("AZURE_UPLOAD_MAX_CONN")
        if "AZURE_CONNECTION_TIMEOUT_SECS" in _environ:
            storage_options["timeout"] = _getenv("AZURE_CONNECTION_TIMEOUT_SECS")
        if "AZURE_BLOB_MAX_MEMORY_SIZE" in _environ:
            storage_options["max_memory_size"] = _getenv("AZURE_BLOB_MAX_MEMORY_SIZE")
        if "AZURE_URL_EXPIRATION_SECS" in _environ:
            storage_options["expiration_secs"] = _getenv("AZURE_URL_EXPIRATION_SECS")
        if "AZURE_OVERWRITE_FILES" in _environ:
            storage_options["overwrite_files"] = _getenv("AZURE_OVERWRITE_FILES")
        if "AZURE_ENDPOINT_SUFFIX" in _environ:
            storage_options["endpoint_suffix"] = _getenv("AZURE_ENDPOINT_SUFFIX")
        if "AZURE_CUSTOM_DOMAIN" in _environ:
            storage_options["custom_domain"] = _getenv("AZURE_CUSTOM_DOMAIN")
        if "AZURE_CACHE_CONTROL" in _environ:
            storage_options["cache_control"] = _getenv("AZURE_CACHE_CONTROL")

    if storage_options:
        storage_config["OPTIONS"] = storage_options

    return storage_config
