class CodexierError(Exception):
    """Base class for safe, user-facing codexier errors."""


class ValidationError(CodexierError):
    """Input or domain validation failed."""


class CatalogError(CodexierError):
    """Provider catalog could not be loaded."""


class ConfigError(CodexierError):
    """Codex configuration could not be read or safely mapped."""
