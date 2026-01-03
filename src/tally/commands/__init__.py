"""
Tally CLI commands.

Each command is in its own module for easier maintenance.
"""

from .run import cmd_run
from .workflow import cmd_workflow
from .update import cmd_update
from .reference import cmd_reference
from .diag import cmd_diag
from .discover import cmd_discover
from .inspect import cmd_inspect
from .init import cmd_init
from .explain import cmd_explain
from .rule import cmd_rule

__all__ = [
    'cmd_run',
    'cmd_workflow',
    'cmd_update',
    'cmd_reference',
    'cmd_diag',
    'cmd_discover',
    'cmd_inspect',
    'cmd_init',
    'cmd_explain',
    'cmd_rule',
]
