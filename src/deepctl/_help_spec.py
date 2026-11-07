"""A command's ``--help`` as data, and the command rebuilt from it.

Each built-in fragment in ``deepctl/_manifest.d/`` carries a ``help`` block:
the Click definition of its command as far as help text depends on it (the
help with its examples, the parameters with their types, flags and
metavars). ``python -m deepctl._manifest_build --write-help`` writes it with
``to_spec()`` from the real command, built by ``PluginManager``; ``--check``
fails when a block no longer matches. ``dg <cmd> --help`` builds a
callback-less command with ``from_spec()`` and lets Click render it, so the
width, program name and telemetry footer come out as for the real command.

Only what help rendering reads is kept, and a command using a Click feature
outside that set (a default shown in help, an environment variable, a
custom parameter or command class) is refused by ``to_spec()`` rather than
written incompletely.
"""

from __future__ import annotations

from typing import Any

import click

# Help widths the generator renders each command at, real and rebuilt.
CHECK_WIDTHS = (50, 80, 200)

# Click's ParamType is generic from 8.5 on and plain before (the floor is
# 8.2), so parameter types are annotated Any here.
_NAMED_TYPES: dict[str, Any] = {
    "text": click.STRING,
    "integer": click.INT,
    "float": click.FLOAT,
    "boolean": click.BOOL,
    "uuid": click.UUID,
}


class SpecError(ValueError):
    """A command uses something a help spec can't carry."""


def _type_spec(param_type: Any) -> dict[str, Any]:
    if isinstance(param_type, click.Choice):
        choices = list(param_type.choices)
        if not all(isinstance(c, str) for c in choices):
            raise SpecError("choices must be strings")
        return {
            "name": "choice",
            "choices": choices,
            "case_sensitive": bool(param_type.case_sensitive),
        }
    if isinstance(param_type, (click.IntRange, click.FloatRange)):
        return {
            "name": "float range"
            if isinstance(param_type, click.FloatRange)
            else "integer range",
            "min": param_type.min,
            "max": param_type.max,
            "min_open": bool(param_type.min_open),
            "max_open": bool(param_type.max_open),
            "clamp": bool(param_type.clamp),
        }
    if isinstance(param_type, click.Path):
        return {
            "name": "path",
            "exists": bool(param_type.exists),
            "file_okay": bool(param_type.file_okay),
            "dir_okay": bool(param_type.dir_okay),
            "writable": bool(param_type.writable),
            "readable": bool(param_type.readable),
            "resolve_path": bool(param_type.resolve_path),
            "allow_dash": bool(param_type.allow_dash),
        }
    if isinstance(param_type, click.File):
        return {
            "name": "file",
            "mode": param_type.mode,
            "encoding": param_type.encoding,
            "lazy": param_type.lazy,
            "atomic": bool(param_type.atomic),
        }
    if isinstance(param_type, click.types.FuncParamType):
        return {"name": param_type.name, "func": True}
    for name, named in _NAMED_TYPES.items():
        if type(param_type) is type(named):
            return {"name": name}
    raise SpecError(f"unsupported parameter type {type(param_type).__name__}")


def _param_spec(param: click.Parameter) -> dict[str, Any]:
    if type(param) is click.Option:
        kind = "option"
    elif type(param) is click.Argument:
        kind = "argument"
    else:
        raise SpecError(f"unsupported parameter class {type(param).__name__}")
    if param.envvar or getattr(param, "deprecated", False):
        raise SpecError(f"{param.name}: envvar and deprecated aren't supported")
    spec: dict[str, Any] = {
        "class": kind,
        "name": param.name,
        "opts": list(param.opts),
        "secondary_opts": list(param.secondary_opts),
        "type": _type_spec(param.type),
        "required": bool(param.required),
        "nargs": param.nargs,
        "metavar": param.metavar,
        "eager": bool(param.is_eager),
        "expose": bool(param.expose_value),
    }
    if isinstance(param, click.Option):
        if param.prompt or param.show_envvar or param.show_default:
            raise SpecError(f"{param.name}: prompt, show_envvar and show_default")
        if param.show_choices is not True:
            raise SpecError(f"{param.name}: show_choices must stay on")
        spec.update(
            multiple=bool(param.multiple),
            is_flag=bool(param.is_flag),
            count=bool(param.count),
            hidden=bool(param.hidden),
            help=param.help,
        )
    return spec


def to_spec(command: click.Command) -> dict[str, Any]:
    """The help spec of ``command``. A group's spec describes the group
    itself (it is the parent in ``dg <group> <sub> --help``); each of its
    subcommands has its own fragment. A group's own help is never rendered
    from a spec: it lists plugin subcommands too."""
    if type(command) not in (click.Command, click.Group):
        raise SpecError(f"unsupported command class {type(command).__name__}")
    if command.context_settings or not command.add_help_option:
        raise SpecError(f"{command.name}: context_settings or no --help option")
    spec: dict[str, Any] = {
        "name": command.name,
        "help": command.help,
        "short_help": command.short_help,
        "epilog": command.epilog,
        "hidden": bool(command.hidden),
        "deprecated": command.deprecated,
        "no_args_is_help": bool(command.no_args_is_help),
        "options_metavar": command.options_metavar,
        "params": [_param_spec(p) for p in command.params],
    }
    if isinstance(command, click.Group):
        if command.chain:
            raise SpecError(f"{command.name}: chained groups aren't supported")
        spec["group"] = True
        spec["subcommand_metavar"] = command.subcommand_metavar
    return spec


def _type_from(spec: dict[str, Any]) -> Any:
    name = spec["name"]
    if name == "choice":
        return click.Choice(spec["choices"], case_sensitive=spec["case_sensitive"])
    if name in ("integer range", "float range"):
        cls = click.FloatRange if name == "float range" else click.IntRange
        return cls(
            spec["min"],
            spec["max"],
            min_open=spec["min_open"],
            max_open=spec["max_open"],
            clamp=spec["clamp"],
        )
    if name == "path":
        return click.Path(
            exists=spec["exists"],
            file_okay=spec["file_okay"],
            dir_okay=spec["dir_okay"],
            writable=spec["writable"],
            readable=spec["readable"],
            resolve_path=spec["resolve_path"],
            allow_dash=spec["allow_dash"],
        )
    if name == "file":
        return click.File(
            spec["mode"],
            encoding=spec["encoding"],
            lazy=spec["lazy"],
            atomic=spec["atomic"],
        )
    if spec.get("func"):

        def convert(value: Any) -> Any:
            return value

        convert.__name__ = name
        return click.types.FuncParamType(convert)
    return _NAMED_TYPES[name]


def _param_from(spec: dict[str, Any]) -> click.Parameter:
    common: dict[str, Any] = {
        "required": spec["required"],
        "nargs": spec["nargs"],
        "metavar": spec["metavar"],
        "is_eager": spec["eager"],
        "expose_value": spec["expose"],
    }
    if spec["class"] == "argument":
        return click.Argument(
            [*spec["opts"][:1]], type=_type_from(spec["type"]), **common
        )
    opts, secondary = spec["opts"], spec["secondary_opts"]
    decls = [f"{o}/{s}" for o, s in zip(opts, secondary, strict=False)]
    decls += opts[len(secondary) :]
    decls.append(spec["name"])
    kwargs: dict[str, Any] = {
        **common,
        "multiple": spec["multiple"],
        "is_flag": spec["is_flag"],
        "count": spec["count"],
        "hidden": spec["hidden"],
        "help": spec["help"],
    }
    if not spec["is_flag"] or spec["type"]["name"] != "boolean":
        kwargs["type"] = _type_from(spec["type"])
    return click.Option(decls, **kwargs)


def from_spec(spec: dict[str, Any]) -> click.Command:
    """A command with no callback that renders the help ``spec`` describes."""
    kwargs: dict[str, Any] = {
        "help": spec["help"],
        "short_help": spec["short_help"],
        "epilog": spec["epilog"],
        "hidden": spec["hidden"],
        "deprecated": spec["deprecated"],
        "no_args_is_help": spec["no_args_is_help"],
        "options_metavar": spec["options_metavar"],
        "params": [_param_from(p) for p in spec["params"]],
    }
    if spec.get("group"):
        return click.Group(
            spec["name"], subcommand_metavar=spec["subcommand_metavar"], **kwargs
        )
    return click.Command(spec["name"], **kwargs)


def render(command: click.Command, width: int, parent: click.Context) -> str:
    """``command``'s help as Click prints it under ``parent`` at ``width``."""
    ctx = click.Context(
        command, info_name=command.name, parent=parent, terminal_width=width
    )
    return command.get_help(ctx)


def verify(command: click.Command, spec: dict[str, Any]) -> None:
    """Raise ``SpecError`` unless the rebuilt command has the same spec and,
    for a leaf, renders the same help as ``command`` at each
    ``CHECK_WIDTHS`` width."""
    rebuilt = from_spec(spec)
    if to_spec(rebuilt) != spec:
        raise SpecError(f"{command.name}: the spec doesn't round-trip")
    if spec.get("group"):
        return
    root = click.Context(click.Group("dg"), info_name="dg")
    for width in CHECK_WIDTHS:
        if render(command, width, root) != render(rebuilt, width, root):
            raise SpecError(f"{command.name}: help differs at width {width}")
