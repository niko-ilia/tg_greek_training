class AppError(Exception):
    """Base for errors whose message is safe to show the learner as-is."""


class ParseError(AppError):
    pass


class DuplicateWordError(AppError):
    pass
