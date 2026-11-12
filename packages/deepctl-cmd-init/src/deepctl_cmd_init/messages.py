"""Strings for ``dg init``'s coded errors.

The confirmation strings are the catalogue's (``E-CONFIRM-INIT-*``). H is
for a person, A for an AI agent; ``*_NULL`` when ``next_step`` is null.
The ``*_BEFORE_CLONE`` hints are their variants for a confirmation raised
before anything was cloned.
"""

CODE_CONFIRMATION_REQUIRED = "confirmation_required"
CODE_INVALID_ARGUMENT = "invalid_argument"
YES_FLAG = "--yes"

E_CONFIRM_INIT_STEPS = 'Template "{template}" wants to run setup commands: {steps}'
H_CONFIRM_INIT_STEPS = (
    "The template is cloned in {dest}. Review the commands, then run again with "
    "--yes to run them, or --no-install to skip them."
)
A_CONFIRM_INIT_STEPS = (
    "The template is cloned in {dest}. next_step finishes without running the "
    "setup commands. Show the user the commands and run them (with --yes) only "
    "if the user approves."
)
A_CONFIRM_INIT_STEPS_NULL = (
    "The template is cloned in {dest}. Show the user the setup commands and ask "
    "them to finish setup in their terminal."
)
# Before the clone, the setup that --yes approves is the listed commands,
# or the Makefile targets if the repository turns out to have a Makefile.
E_CONFIRM_INIT_STEPS_BEFORE_CLONE = (
    'Template "{template}" has setup commands. --yes runs {steps}, or make '
    "check-prereqs and make init if the repository has a Makefile."
)
H_CONFIRM_INIT_STEPS_BEFORE_CLONE = (
    "Nothing was cloned. Review the commands, then run again with --yes to run "
    "them, or --no-install to skip them."
)
A_CONFIRM_INIT_STEPS_BEFORE_CLONE = (
    "Nothing was cloned. next_step clones the template without running the "
    "setup commands. Show the user the commands and run them (with --yes) only "
    "if the user approves."
)
A_CONFIRM_INIT_STEPS_BEFORE_CLONE_NULL = (
    "Nothing was cloned. Show the user the setup commands and ask them to run "
    "{prog} init in their terminal."
)

E_CONFIRM_INIT_START = 'Template "{template}" can start the app after setup.'
H_CONFIRM_INIT_START = "Run again with --start to start it, or --no-start to skip."
A_CONFIRM_INIT_START = (
    "next_step skips starting the app. Start it only if the user asks."
)
A_CONFIRM_INIT_START_NULL = (
    "Ask the user whether to start the app; rerun with --no-start to skip it."
)

# No template named, and no picker to choose one (an AI agent session).
E_CONFIRM_INIT_PICK = "{prog} init needs a template name when it can't show the picker."
H_CONFIRM_INIT_PICK = (
    "List templates with {prog} init --list, then run {prog} init <TEMPLATE>."
)

# Rerun contract: a directory dg init can't resume in.
E_INIT_DIR_IN_USE = "Directory already exists: {dest}"
E_INIT_DIR_OTHER_TEMPLATE = (
    'Directory {dest} holds the template "{other}", not "{template}".'
)
E_INIT_DIR_CHANGED = (
    "{dest} changed while {prog} init was running, so nothing more was done there."
)
# dg's record of the clone couldn't be written.
W_INIT_PROGRESS_UNSAVED = (
    "Couldn't save setup progress ({exc}), so running {prog} init again won't "
    "resume in {dest}."
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
