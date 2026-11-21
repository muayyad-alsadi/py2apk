"""HTML helpers. ``escape`` covers the characters ``&``, ``<``, and ``>``."""


def escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
