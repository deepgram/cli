"""Strings for ``dg init``'s errors."""

CODE_INVALID_ARGUMENT = "invalid_argument"

# Rerun contract: a directory dg init can't resume in.
E_INIT_DIR_IN_USE = "Directory already exists: {dest}"
E_INIT_DIR_OTHER_TEMPLATE = (
    'Directory {dest} holds the template "{other}", not "{template}".'
)
H_INIT_DIR = "Choose another directory with --dir, or remove this one."

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
