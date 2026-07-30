"""Minimal JSON-schema-subset validator for tool arguments (stdlib only)."""

from __future__ import annotations

_TYPES = {
    "array": list,
    "boolean": bool,
    "integer": int,
    "number": (int, float),
    "string": str,
}


class ValidationError(Exception):
    def __init__(self, code: str, hint: str):
        super().__init__(hint)
        self.code = code
        self.hint = hint


def validate_args(schema: dict, args: dict) -> None:
    if not isinstance(args, dict):
        raise ValidationError("bad_args", "arguments must be an object")
    props = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in args:
            raise ValidationError(
                "missing_arg",
                f"missing required argument '{key}'; optional arguments and "
                "their default rules are documented in the tool description")
    for key, value in args.items():
        if key not in props:
            raise ValidationError("unknown_arg", f"unknown argument '{key}'")
        spec = props[key]
        expected = _TYPES.get(spec.get("type"))
        if expected is not None and not isinstance(value, expected):
            raise ValidationError("bad_type",
                                  f"argument '{key}' must be {spec['type']}")
        if isinstance(value, bool) and spec.get("type") in ("integer", "number"):
            raise ValidationError("bad_type", f"argument '{key}' must be numeric")
        if "enum" in spec and value not in spec["enum"]:
            raise ValidationError("bad_enum",
                                  f"argument '{key}' must be one of {spec['enum']}")
        if spec.get("type") == "array":
            item_type = _TYPES.get(spec.get("items", {}).get("type"))
            if item_type is not None and \
                    not all(isinstance(v, item_type) for v in value):
                raise ValidationError("bad_type",
                                      f"items of '{key}' must be {spec['items']['type']}")
        if "minimum" in spec and value < spec["minimum"]:
            raise ValidationError("out_of_range", f"argument '{key}' below minimum")
        if "maximum" in spec and value > spec["maximum"]:
            raise ValidationError("out_of_range", f"argument '{key}' above maximum")
