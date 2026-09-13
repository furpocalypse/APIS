from django.contrib.staticfiles.storage import ManifestStaticFilesStorage


def _is_excluded(name: str) -> bool:
    return name.startswith("bundler/")


class SelectiveManifestStaticFilesStorage(ManifestStaticFilesStorage):
    """Storage backend for handling static files defined in a Vite mamifest."""
    def hashed_name(self, name, content=None, filename=None):
        if _is_excluded(name):
            return name
        else:
            return super().hashed_name(name, content, filename)
