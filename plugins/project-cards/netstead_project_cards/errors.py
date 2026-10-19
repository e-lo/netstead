"""The plugin's own errors. Its Action handlers turn them into the Workbench's ``ActionError`` / ``NotSupportedYet``."""

__all__ = ["CardError", "CardNotSupported", "not_supported"]


class CardError(ValueError):
    """A change or a card cannot be applied, read or written; the message says why (nothing was changed)."""


class CardNotSupported(CardError):
    """Declared, but not supported in this version: transit changes, ``pycode``, or re-resolving a query."""


def not_supported(change_type: str) -> CardNotSupported:
    """The refusal for a declared change type this version cannot apply or write (transit, until P6)."""
    return CardNotSupported(
        f"{change_type} is not yet supported: transit changes arrive with the transit component (P6)"
    )
