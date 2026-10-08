"""Section 5 — commercial value rules. The ₹30 lakh cap applies to SERVICE value only."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.config import HybridOverCapAction, Settings, UnknownValueAction
from app.documents.values import format_inr

ACCEPT, REJECT, REVIEW = "ACCEPT", "REJECT", "REVIEW"


@dataclass
class CommercialDecision:
    status: str  # ACCEPT | REJECT | REVIEW
    code: str
    reason: str
    total_value_inr: int | None
    service_value_inr: int | None
    product_value_inr: int | None
    value_known: bool
    flags: list[str] = field(default_factory=list)


def _unknown_action(action: str) -> str:
    return {UnknownValueAction.ACCEPT: ACCEPT, UnknownValueAction.REJECT: REJECT}.get(action, REVIEW)


def apply_commercial_rules(
    opportunity_type: str,
    settings: Settings,
    *,
    total_value_inr: int | None,
    service_value_inr: int | None = None,
    product_value_inr: int | None = None,
) -> CommercialDecision:
    cap = settings.SERVICE_MAX_VALUE_INR
    cap_s = format_inr(cap)

    def d(status, code, reason, flags=(), sv=service_value_inr, pv=product_value_inr) -> CommercialDecision:
        return CommercialDecision(status, code, reason, total_value_inr, sv, pv,
                                  total_value_inr is not None or sv is not None, list(flags))

    if opportunity_type == "SERVICE":
        value = service_value_inr if service_value_inr is not None else total_value_inr
        if not settings.SERVICE_VALUE_RULE_ENABLED:
            return d(ACCEPT, "SERVICE_RULE_DISABLED", "Service value rule is disabled in configuration.", sv=value)
        if value is None:
            status = _unknown_action(settings.SERVICE_UNKNOWN_VALUE_ACTION)
            return d(status, "SERVICE_VALUE_UNKNOWN",
                     f"Service opportunity; value could not be determined (configured action: {settings.SERVICE_UNKNOWN_VALUE_ACTION}).",
                     ["VALUE_UNKNOWN"], sv=None)
        if value > cap:
            return d(REJECT, "SERVICE_OVER_CAP",
                     f"Service-only opportunity valued at {format_inr(value)} exceeds the {cap_s} service limit.", sv=value)
        return d(ACCEPT, "SERVICE_WITHIN_CAP",
                 f"Service opportunity valued at {format_inr(value)} is within the {cap_s} service limit.", sv=value)

    if opportunity_type == "OEM":
        if settings.OEM_VALUE_LIMIT_ENABLED and settings.OEM_MAX_VALUE_INR > 0 and total_value_inr is not None \
                and total_value_inr > settings.OEM_MAX_VALUE_INR:
            return d(REJECT, "OEM_OVER_LIMIT",
                     f"OEM value {format_inr(total_value_inr)} exceeds the configured OEM limit {format_inr(settings.OEM_MAX_VALUE_INR)}.")
        value_txt = format_inr(total_value_inr) if total_value_inr is not None else "an undetermined value"
        return d(ACCEPT, "OEM_NO_CAP",
                 f"OEM/product opportunity at {value_txt}: the {cap_s} cap applies only to services, so value does not reject it.",
                 [] if total_value_inr is not None else ["VALUE_UNKNOWN"],
                 pv=product_value_inr if product_value_inr is not None else total_value_inr)

    if opportunity_type == "HYBRID":
        total_txt = format_inr(total_value_inr) if total_value_inr is not None else "value not stated"
        if service_value_inr is not None:
            if settings.SERVICE_VALUE_RULE_ENABLED and service_value_inr > cap:
                action = settings.HYBRID_SERVICE_OVER_CAP_ACTION
                status = {HybridOverCapAction.ACCEPT: ACCEPT, HybridOverCapAction.REJECT: REJECT}.get(action, REVIEW)
                return d(status, "HYBRID_SERVICE_COMPONENT_OVER_CAP",
                         f"Hybrid tender (total {total_txt}); its service component {format_inr(service_value_inr)} exceeds "
                         f"the {cap_s} service limit (configured action: {action}). Product component is not capped.",
                         ["SERVICE_COMPONENT_OVER_CAP"])
            return d(ACCEPT, "HYBRID_COMPONENTS_OK",
                     f"Hybrid tender (total {total_txt}): service component {format_inr(service_value_inr)} is within the "
                     f"{cap_s} service limit; the product component is not subject to the cap.")
        if settings.HYBRID_REVIEW_ENABLED:
            return d(REVIEW, "HYBRID_REVIEW_REQUIRED",
                     f"Hybrid tender (total {total_txt}) without a separately stated service value; "
                     "service vs product split needs human review. Total value alone does not reject a hybrid.",
                     ["HYBRID_REVIEW_REQUIRED"])
        return d(ACCEPT, "HYBRID_SPLIT_UNKNOWN",
                 f"Hybrid tender (total {total_txt}); service component value not stated. Accepted because hybrid review is disabled.",
                 ["SERVICE_COMPONENT_VALUE_UNKNOWN"])

    return d(REVIEW, "CLASSIFICATION_UNKNOWN", "Opportunity type could not be determined; needs human review.",
             ["CLASSIFICATION_UNKNOWN"])
