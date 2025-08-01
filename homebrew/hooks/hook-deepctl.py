"""PyInstaller hook for deepctl to include entry point metadata."""

from PyInstaller.utils.hooks import collect_entry_point

# Collect entry points for deepctl commands
datas, hiddenimports = collect_entry_point("deepctl.commands")

# Also collect entry points for plugins
plugin_datas, plugin_hiddenimports = collect_entry_point("deepctl.plugins")
datas.extend(plugin_datas)
hiddenimports.extend(plugin_hiddenimports)

# Collect subcommand entry points (for group commands)
for group_name in ["debug"]:  # Add other group names as needed
    sub_datas, sub_hiddenimports = collect_entry_point(
        f"deepctl.subcommands.{group_name}"
    )
    datas.extend(sub_datas)
    hiddenimports.extend(sub_hiddenimports)
