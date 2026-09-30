"""The optional ``--explain`` narrator (R41-R43).

Four rules govern everything in this package, and all four are security
properties rather than conveniences:

1. **Nothing here runs unless ``--explain`` is given.** R43 says so in the
   strongest available form — with the flag off, ``narrate/`` is "never
   imported by the analyze path". ``cli/main.py`` therefore imports this
   package inside the ``--explain`` branch, not at module scope, and R46's
   no-socket guarantee for the default path is a consequence of the import
   never happening rather than of a client never being called.

2. **The request carries no trace text** (R42). Not "is filtered of trace
   text": the payload models in :mod:`~swarm_observer.narrate.client` have no
   field that can *hold* free text — every string in the tree is a member of a
   closed vocabulary this package computed, and construction raises otherwise.
   See :mod:`~swarm_observer.narrate.summary`.

3. **A narrator failure is never a run failure** (R43, R39). Every group falls
   back to a deterministic template independently, the exit code is untouched,
   and ``--explain`` can never turn a 0 into a 1.

4. **The narrator's output is untrusted** (R43). A paragraph is validated,
   then redacted and escaped at the render boundary exactly like trace text —
   a compromised or prompt-injected narrator is just another attacker-influenced
   string source, and increment 5's ``narrator`` text kind is where that is
   written down.
"""

from __future__ import annotations

__all__: list[str] = []
