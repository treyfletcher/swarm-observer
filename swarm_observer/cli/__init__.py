"""The command line interface (R38-R40).

``cli`` may import anything in the package and nothing may import ``cli``
(R44): it is the one place an adapter, a rate source or a narrator client is
selected, so every other subpackage stays independently testable.
"""

from __future__ import annotations
