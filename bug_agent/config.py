"""Load optional TOML defaults without adding third-party dependencies."""

from pathlib import Path
import tomllib


CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def load_config(path, fields):
    """Read known fields, returning empty defaults if the file does not exist.

    Reject typos and incorrect types early so configuration mistakes produce
    useful messages instead of failing during an API request or file search.
    """
    path = Path(path)
    try:
        with path.open("rb") as file:
            values = tomllib.load(file)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Invalid TOML in {path}: {exc}") from None
    for key, value in values.items():
        if key not in fields:
            raise ValueError(f"Unknown setting '{key}' in {path}.")
        if type(value) is not fields[key]:
            raise ValueError(f"Setting '{key}' in {path} must be {fields[key].__name__}.")
    return values
