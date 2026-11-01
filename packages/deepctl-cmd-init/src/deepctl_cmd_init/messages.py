"""Strings for ``dg init``'s errors."""

# The template service sent a name that isn't plain.
E_INIT_NAME_REFUSED = (
    'Not using template "{template}": the template service sent a name that '
    'isn\'t plain (letters, digits, ".", "_" and "-", not starting with "-").'
)

# The template service named a repository outside Deepgram's organizations.
E_INIT_REPO_REFUSED = (
    'Not cloning template "{template}": its repository is not on '
    "github.com/deepgram or github.com/deepgram-starters."
)
