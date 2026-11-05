"""Hermes plugin entry point for God's Eye View."""


def register(ctx) -> None:
    from .gev.hermes_tools import register_tools

    register_tools(ctx)
