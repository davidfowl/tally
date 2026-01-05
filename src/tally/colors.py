"""
Terminal color utilities for tally CLI.
"""

import os
import sys


def supports_color():
    """Check if the terminal supports color output."""
    if not sys.stdout.isatty():
        return False
    if os.environ.get('NO_COLOR'):
        return False
    if os.environ.get('FORCE_COLOR'):
        return True
    # Check for common terminal types
    term = os.environ.get('TERM', '')
    return term != 'dumb'


def setup_windows_encoding():
    """Set UTF-8 encoding on Windows to support Unicode output."""
    if sys.platform != 'win32':
        return

    import codecs

    for stream_name in ('stdout', 'stderr'):
        stream = getattr(sys, stream_name)
        # Skip if already UTF-8
        if getattr(stream, 'encoding', '').lower().replace('-', '') == 'utf8':
            continue
        try:
            # Method 1: reconfigure (works in normal Python 3.7+)
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, OSError):
            try:
                # Method 2: Use codecs writer (more reliable for PyInstaller)
                if hasattr(stream, 'buffer'):
                    writer = codecs.getwriter('utf-8')(stream.buffer, errors='replace')
                    writer.encoding = 'utf-8'
                    setattr(sys, stream_name, writer)
            except Exception:
                pass


# Run Windows encoding setup on import
setup_windows_encoding()


class Colors:
    """ANSI color codes with automatic detection."""
    def __init__(self):
        if supports_color():
            self.RESET = '\033[0m'
            self.BOLD = '\033[1m'
            self.DIM = '\033[2m'
            self.GREEN = '\033[32m'
            self.CYAN = '\033[36m'
            self.BLUE = '\033[34m'
            self.YELLOW = '\033[33m'
            self.RED = '\033[31m'
            self.UNDERLINE = '\033[4m'
        else:
            self.RESET = ''
            self.BOLD = ''
            self.DIM = ''
            self.GREEN = ''
            self.CYAN = ''
            self.BLUE = ''
            self.YELLOW = ''
            self.RED = ''
            self.UNDERLINE = ''


# Singleton instance
C = Colors()
