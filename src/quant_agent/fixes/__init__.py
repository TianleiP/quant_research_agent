from quant_agent.fixes.apply import apply_fix_proposal
from quant_agent.fixes.generalized import suggest_generalized_fix
from quant_agent.fixes.missing_exports import suggest_missing_export_fix
from quant_agent.fixes.proposal_contract import validate_structured_proposal
from quant_agent.fixes.revision import suggest_verification_revision
from quant_agent.fixes.revision_synthesis import synthesize_revised_patch
from quant_agent.fixes.stale_metadata import suggest_stale_metadata_fix

__all__ = [
    "apply_fix_proposal",
    "suggest_generalized_fix",
    "suggest_missing_export_fix",
    "suggest_stale_metadata_fix",
    "suggest_verification_revision",
    "synthesize_revised_patch",
    "validate_structured_proposal",
]
