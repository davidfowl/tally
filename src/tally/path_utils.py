"""
Path resolution helpers for data sources.
"""

import glob
import os


def resolve_data_source_paths(config_dir, file_spec):
    """Resolve a data source file spec into concrete file paths.

    Supports:
      - Direct file paths
      - Directory paths (top-level *.csv only)
      - Glob patterns with * and ** (recursive)

    Returns:
        (paths, kind) where kind is one of: 'file', 'dir', 'glob', 'missing'
    """
    if not file_spec:
        return [], 'missing'

    base_dir = os.path.dirname(os.path.abspath(config_dir))
    spec = os.path.expandvars(os.path.expanduser(str(file_spec)))
    if not os.path.isabs(spec):
        spec = os.path.normpath(os.path.join(base_dir, spec))

    if glob.has_magic(spec):
        matches = glob.glob(spec, recursive=True)
        files = [os.path.normpath(p) for p in matches if os.path.isfile(p)]
        return sorted(set(files)), 'glob'

    if os.path.isdir(spec):
        files = []
        for entry in os.listdir(spec):
            full_path = os.path.join(spec, entry)
            if os.path.isfile(full_path) and entry.lower().endswith('.csv'):
                files.append(os.path.normpath(full_path))
        return sorted(files), 'dir'

    if os.path.isfile(spec):
        return [os.path.normpath(spec)], 'file'

    return [], 'missing'
