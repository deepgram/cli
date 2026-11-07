## Naming your entry point

A plugin whose entry point is named like a built-in command, such as `login`,
is skipped before it's imported; give the entry point a different name. The
only exception is a command listed in `plugins.allow_override` in your user
config (see [Plugins](https://github.com/deepgram/cli/blob/main/README.md#plugins)).
