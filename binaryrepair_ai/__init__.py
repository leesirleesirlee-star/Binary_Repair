"""BinaryRepair AI layer (role 5 starter kit).

Public surface kept deliberately small so the integration owner can wire it in:

    from binaryrepair_ai import DeepSeekProvider, RepairContext
    provider = DeepSeekProvider.from_env()
    result = provider.request_candidate(ctx)
    if result.ok:
        candidate = result.candidate          # validated CandidateV1
    else:
        failure = result.failure              # typed, never an exception for expected cases
"""

from .errors import Failure, ProviderError
from .provider import DeepSeekProvider, ProviderResult
from .schemas import CandidateV1, RepairContext, TemplateParam, TemplateSpec

__all__ = [
    "CandidateV1",
    "DeepSeekProvider",
    "Failure",
    "ProviderError",
    "ProviderResult",
    "RepairContext",
    "TemplateParam",
    "TemplateSpec",
]
