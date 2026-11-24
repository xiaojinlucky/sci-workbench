"""sciglyph slim layout package — canvas aspect helpers and lazy collision auditor."""

from ._canvas import aspect, set_canvas

__version__ = "0.2.2"
__all__ = ["layout", "set_canvas", "aspect", "text_collisions", "report", "__version__"]


def __getattr__(name):
    if name in ("layout", "text_collisions", "report"):
        import importlib

        _layout = importlib.import_module(".layout", __name__)
        return _layout if name == "layout" else getattr(_layout, name)
    raise AttributeError(f"module 'sciglyph' has no attribute {name!r}")
