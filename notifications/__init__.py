"""Feishu application-bot paper notifications for ZAI-Paper.

This package is a standalone, additive module: it reads the final public JSON
produced by ``python main.py`` and delivers new papers to configured Feishu
targets.  It does not modify ``main.py`` or the papers schema.

Author:
    Ellen Song <jiaqi.song@z.ai>
"""

from __future__ import annotations

__all__ = ["cards", "client", "config", "service", "state"]
