"""The one base every sillytherin-raised exception derives from, so a caller can catch them as a family."""


class SillytherinError(Exception):
    """A failure sillytherin raises on purpose, as opposed to a bug or a library error."""
