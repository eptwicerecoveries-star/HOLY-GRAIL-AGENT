from surplus_ai.utils.exceptions import AppError


class AuthError(AppError):
    """Base for local authentication-data failures (not HTTP auth)."""


class AuthDependencyError(AuthError):
    """The optional auth extra (pwdlib) is not installed."""


class PasswordPolicyError(AuthError):
    """A password does not meet the local length policy."""


class UserAdminError(AuthError):
    """Local user-admin input or persistence failure."""


class UserAlreadyExistsError(UserAdminError):
    """Create refused because the email is already registered."""


class UserNotFoundError(UserAdminError):
    """Reset refused because no User matches the given email."""


class AuthSessionError(AuthError):
    """Local auth-session create/resolve failure (not HTTP auth)."""
