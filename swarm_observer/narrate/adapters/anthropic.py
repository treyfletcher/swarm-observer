"""The Anthropic narrator adapter — the only module that imports ``anthropic`` (R41, R44).

**Read this before trusting anything below: none of the code in
:meth:`AnthropicNarratorClient.complete` past the import guard has ever
executed.** It needs the ``[explain]`` extra installed and a real API key, CI
has neither by design (R45), and this project's signature defect is a check
that reports green while structurally unable to fail — of which "a live-eval
replay path that could never have passed" was instance three. So the code here
is written to be as thin as a wrapper can be, and the PR write-up names every
line of it as unexecuted rather than implying otherwise.

What that thinness buys, concretely:

* the adapter **builds nothing**. The payload is
  :func:`~swarm_observer.narrate.summary.serialize_request`'s output, which is
  the string AC12's sentinel test inspects; this module cannot add a field to
  it, because it never sees a ``Trace``, a ``Finding`` or a ``CostReport``;
* the adapter **decides nothing**. Validation, fallback and the exit-code
  guarantee all live in :mod:`~swarm_observer.narrate.narrator`, which is
  exercised offline by the fixture client over the same protocol;
* the adapter **classifies by exception type, never by message**. A
  :class:`~swarm_observer.narrate.client.NarratorError` carries a code from a
  closed set and has nowhere to put a provider's text (R41), so "the message
  never echoes a response body" is a property of the type rather than a rule
  this module has to remember.

The three code paths that are *not* dead offline, and which the offline suite
can therefore reach: the ``ImportError`` branch (CI has no ``anthropic``, so
``sdk_not_installed`` is the live answer), the missing-credential branch (R45
scrubs the environment), and :func:`system_prompt` / the request assembly,
which are pure functions of a :class:`NarrationRequest`.
"""

from __future__ import annotations

import os
from typing import Any

from swarm_observer.narrate.client import (
    MAX_PARAGRAPH_CHARS,
    NarrationRequest,
    NarrationResponse,
    NarratorAuthError,
    NarratorResponseError,
    NarratorTransportError,
)
from swarm_observer.narrate.summary import serialize_request

#: The model this adapter asks by default. A narrator writes three sentences
#: about integers; the small model is the right default and the flagship one
#: would be a surprising bill for a flag whose failure mode is silent.
DEFAULT_NARRATOR_MODEL = "claude-haiku-4-5"

#: Seconds before the call is abandoned. R43 lists a timeout among the
#: conditions that take the fallback path, so this bound is what makes
#: ``--explain`` unable to hang a CI job.
DEFAULT_TIMEOUT_SECONDS = 30.0

#: An upper bound on the answer, in tokens. R43 caps a paragraph at 800
#: characters after normalization, so a generous token bound still cannot
#: produce a paragraph this package would accept — the cap is a cost control,
#: not a validation.
DEFAULT_MAX_TOKENS = 600

#: The environment variables the SDK reads for a credential. Named here so the
#: adapter can answer "no credential" *before* constructing a client, rather
#: than letting the SDK raise something this module would have to classify by
#: message.
CREDENTIAL_ENV_VARS: tuple[str, ...] = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def system_prompt() -> str:
    """The instruction sent with every request. A constant, never formatted.

    It says out loud what the payload is and is not, because a model told to
    "summarize the trace" will invent trace content it was never given, and an
    invented tool name in a report is worse than no paragraph at all — it is a
    paragraph a reader would act on.
    """
    return (
        "You are writing one short paragraph for an engineering report about a "
        "multi-agent AI run. You are given only aggregate counts, severity tallies, "
        "enumerated detector slugs and token and cost totals. You are NOT given the "
        "run's prompts, tool arguments, tool results, file names or any other text "
        "from it, and none exist in the input. Write about the numbers you were "
        "given and invent nothing else: no tool names, no file names, no quotations, "
        "no agent names. Reply with the paragraph and nothing else: no preamble, no "
        "markdown, no bullet list, no headings. "
        f"Keep it under {MAX_PARAGRAPH_CHARS} characters and on a single line."
    )


def user_prompt(request: NarrationRequest) -> str:
    """The one message body: the group being asked about, and the payload.

    The payload is :func:`serialize_request`'s exact bytes. Nothing is
    interpolated into it and nothing is added beside it except the group key,
    which is already inside it — so what AC12's sentinel test inspects is what
    goes on the wire.
    """
    return f"Write the paragraph for the group {request.group!r}.\n{serialize_request(request)}"


class AnthropicNarratorClient:
    """A :class:`~swarm_observer.narrate.client.NarratorClient` over the Anthropic SDK.

    Constructing one does nothing: no import, no network, no credential read.
    That is deliberate — R43 wants a missing SDK and an absent key to take the
    *fallback* path, and an exception in a constructor would have to be caught
    somewhere other than the one place that already catches everything.
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_NARRATOR_MODEL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens

    def _sdk(self) -> Any:
        """Import ``anthropic``, lazily, or say why it could not be (R41, R45).

        The one import in this package that may fail, and the reason it is
        inside a method rather than at module scope: R45 requires the whole
        suite to pass with the ``[explain]`` extra absent, and R43 requires a
        missing package to fall back rather than to crash.
        """
        try:
            import anthropic
        except ImportError as error:
            raise NarratorTransportError("sdk_not_installed") from error
        return anthropic

    def _credential_present(self) -> bool:
        """True when the environment carries something the SDK could use.

        Checked here rather than left to the SDK so an absent key is
        :class:`NarratorAuthError` — one of :data:`FATAL_CODES`, so the run
        stops asking — instead of a vendor exception this module would have to
        classify. No credential is ever read from a flag, a file or the trace.
        """
        return any(os.environ.get(name) for name in CREDENTIAL_ENV_VARS)

    def complete(self, request: NarrationRequest) -> NarrationResponse:
        """One paragraph from the provider, or a classified failure (R41, R43).

        Every line below the credential check is unexecuted code. It is kept
        short for that reason, and the classification is by exception *type*
        only, so nothing here can put a provider's message into an error.
        """
        sdk = self._sdk()
        if not self._credential_present():
            raise NarratorAuthError("no_credentials")
        client = sdk.Anthropic(timeout=self.timeout_seconds)
        try:
            message = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system_prompt(),
                messages=[{"role": "user", "content": user_prompt(request)}],
            )
        except sdk.AuthenticationError as error:
            raise NarratorAuthError("auth_rejected") from error
        except sdk.PermissionDeniedError as error:
            raise NarratorAuthError("auth_rejected") from error
        except sdk.RateLimitError as error:
            raise NarratorTransportError("rate_limited") from error
        except sdk.APITimeoutError as error:
            raise NarratorTransportError("timeout") from error
        except sdk.APIConnectionError as error:
            raise NarratorTransportError("transport_failed") from error
        except sdk.APIStatusError as error:
            raise NarratorTransportError("provider_error") from error
        return NarrationResponse(paragraph=extract_paragraph(message))


def extract_paragraph(message: Any) -> str:
    """The text of an SDK message, or :class:`NarratorResponseError`.

    Split out of :meth:`AnthropicNarratorClient.complete` so the one piece of
    response handling that has a shape worth checking is a function a test can
    drive with a stub object, rather than a branch reachable only with a key.
    It still has never run against a real SDK message.
    """
    blocks = getattr(message, "content", None)
    if not isinstance(blocks, list):
        raise NarratorResponseError("response_malformed")
    pieces = [
        block.text
        for block in blocks
        if getattr(block, "type", None) == "text" and isinstance(getattr(block, "text", None), str)
    ]
    if not pieces:
        raise NarratorResponseError("response_empty")
    return "\n".join(pieces)


__all__ = [
    "CREDENTIAL_ENV_VARS",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_NARRATOR_MODEL",
    "DEFAULT_TIMEOUT_SECONDS",
    "AnthropicNarratorClient",
    "extract_paragraph",
    "system_prompt",
    "user_prompt",
]
