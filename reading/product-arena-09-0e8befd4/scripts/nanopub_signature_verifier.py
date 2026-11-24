#!/usr/bin/env python3
"""Canonical 4-graph Nanopublication serializer, Trusty-style hasher, and local signature verifier.

Implements strict separation of the four Nanopublication named graphs:
- head: links nanopub URI to assertion, provenance, and pubinfo graphs
- assertion: atomic scientific triple + statement + condition + polarity + assertion_agent
- provenance: source_file, paragraph_id, verbatim quote, evidence_type, paper metadata
- pubinfo: curator/creator, cryptographic signer (npx:signedBy), timestamp, local_draft state

Addresses nanopub-java Issue #127 / PR #128 identity separation:
- Never conflates assertion_agent (claim proposer / subject agent) with signer (npx:signedBy)
  or provenance_authors (source paper authors).
- Computes deterministic canonical digests per graph + overall Trusty-style artifact hash +
  local SHA-256 signature envelope so tampering with ANY triple in assertion, provenance,
  or signer metadata causes immediate deterministic verification failure.
- Strictly local offline verification; NEVER publishes to external Nanopublication servers.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import sys
from pathlib import Path
from typing import Any

JSONLD_CONTEXT: dict[str, Any] = {
    "np": "http://www.nanopub.org/nschema#",
    "npx": "http://purl.org/nanopub/x/",
    "prov": "http://www.w3.org/ns/prov#",
    "dct": "http://purl.org/dc/terms/",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "ro": "http://purl.obolibrary.org/obo/RO_",
    "sio": "http://semanticscience.org/resource/SIO_",
    "iao": "http://purl.obolibrary.org/obo/IAO_",
    "local": "urn:nanopub:local:",
}

PREDICATE_URIS: dict[str, str] = {
    "catalyzes": "http://purl.obolibrary.org/obo/RO_0002327",
    "increases": "http://purl.obolibrary.org/obo/RO_0002213",
    "decreases": "http://purl.obolibrary.org/obo/RO_0002212",
    "inhibits": "http://purl.obolibrary.org/obo/RO_0002449",
    "activates": "http://purl.obolibrary.org/obo/RO_0002448",
    "regulates": "http://purl.obolibrary.org/obo/RO_0002211",
    "correlates_with": "http://semanticscience.org/resource/SIO_001403",
    "associated_with": "http://semanticscience.org/resource/SIO_001403",
    "expresses": "http://purl.obolibrary.org/obo/RO_0002292",
    "reaches": "http://semanticscience.org/resource/SIO_000216",
    "achieves": "http://semanticscience.org/resource/SIO_000216",
    "outperforms": "http://semanticscience.org/resource/SIO_000734",
    "compares_with": "http://semanticscience.org/resource/SIO_000734",
    "uses": "http://purl.obolibrary.org/obo/IAO_0000136",
    "supports": "http://purl.obolibrary.org/obo/IAO_0000136",
    "contradicts": "http://purl.obolibrary.org/obo/IAO_0000136",
    "causes_direct": "http://purl.obolibrary.org/obo/RO_0002410",
    "does_not_differ": "http://semanticscience.org/resource/SIO_000734",
}

LOCAL_SIGNING_DOMAIN = "sci-nanopub-local-draft-v2"


def _canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _b64url_sha256(payload: bytes) -> str:
    digest = hashlib.sha256(payload).digest()
    return "RA" + base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")[:43]


def _hex_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _escape_trig_lit(val: str) -> str:
    escaped = (
        val.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )
    return f'"{escaped}"'


def _slug_uri(prefix: str, text: str) -> str:
    h = hashlib.sha256(text.strip().casefold().encode("utf-8")).hexdigest()[:12]
    safe = "".join(ch if ch.isalnum() else "_" for ch in text.strip())[:32].strip("_") or "entity"
    return f"urn:nanopub:{prefix}:{safe}_{h}"


def _unsigned_pubinfo(pubinfo: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in pubinfo.items() if k not in {"integrity", "signature"}}


def compute_graph_digests(nanopub: dict[str, Any]) -> dict[str, str]:
    """Compute independent canonical SHA-256 digests for each named graph and combined Trusty code."""
    head = nanopub.get("head") or {}
    assertion = nanopub.get("assertion") or {}
    provenance = nanopub.get("provenance") or {}
    pubinfo_u = _unsigned_pubinfo(nanopub.get("publication_info") or {})

    head_sha = _hex_sha256(_canonical_json(head))
    assertion_sha = _hex_sha256(_canonical_json(assertion))
    provenance_sha = _hex_sha256(_canonical_json(provenance))
    pubinfo_sha = _hex_sha256(_canonical_json(pubinfo_u))

    combined_payload = _canonical_json({
        "head_sha256": head_sha,
        "assertion_sha256": assertion_sha,
        "provenance_sha256": provenance_sha,
        "pubinfo_unsigned_sha256": pubinfo_sha,
    })
    trusty_code = _b64url_sha256(combined_payload)
    return {
        "head_sha256": head_sha,
        "assertion_sha256": assertion_sha,
        "provenance_sha256": provenance_sha,
        "pubinfo_unsigned_sha256": pubinfo_sha,
        "trusty_artifact_code": trusty_code,
    }


def _compute_local_signature(digests: dict[str, str], signer_id: str, signing_key_hint: str) -> str:
    """Deterministic offline HMAC-SHA256 binding all 4 graph digests to signer_id."""
    key = hashlib.sha256(f"{LOCAL_SIGNING_DOMAIN}|{signer_id}|{signing_key_hint}".encode("utf-8")).digest()
    msg = _canonical_json({
        "domain": LOCAL_SIGNING_DOMAIN,
        "signer_id": signer_id,
        "digests": digests,
    })
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def sign_nanopublication(
    assertion: dict[str, Any],
    provenance: dict[str, Any],
    pubinfo_base: dict[str, Any],
    *,
    signer_id: str = "urn:agent:local-verifier",
    signing_key_hint: str = "local-draft-ephemeral",
) -> dict[str, Any]:
    """Assemble a 4-graph Nanopublication with separated identities and cryptographic digest envelope."""
    # Pre-bind unsigned structure to derive deterministic Trusty artifact code
    pubinfo = dict(pubinfo_base)
    pubinfo["signed_by"] = signer_id
    pubinfo["no_external_publish"] = True
    pubinfo.setdefault("state", "local_draft")

    draft_seed = _canonical_json({
        "assertion": assertion,
        "provenance": provenance,
        "pubinfo_unsigned": _unsigned_pubinfo(pubinfo),
    })
    artifact_code = _b64url_sha256(draft_seed)
    base_uri = f"urn:nanopub:draft:{artifact_code}"

    head = {
        "nanopub_uri": base_uri,
        "has_assertion": f"{base_uri}#assertion",
        "has_provenance": f"{base_uri}#provenance",
        "has_publication_info": f"{base_uri}#pubinfo",
        "schema_version": "nanopub-4graph-v2",
    }

    temp_np = {
        "nanopub_id": f"np_{artifact_code[:20]}",
        "uri": base_uri,
        "head": head,
        "assertion": assertion,
        "provenance": provenance,
        "publication_info": pubinfo,
    }
    digests = compute_graph_digests(temp_np)
    sig_hex = _compute_local_signature(digests, signer_id=signer_id, signing_key_hint=signing_key_hint)

    pubinfo["integrity"] = {
        "algorithm": "canonical-json-sha256+hmac-sha256-local",
        "signing_key_hint": signing_key_hint,
        "signed_by": signer_id,
        "covers_graphs": ["head", "assertion", "provenance", "pubinfo_unsigned"],
        **digests,
        "signature_hex": sig_hex,
    }
    return temp_np


def verify_nanopublication(nanopub: dict[str, Any]) -> dict[str, Any]:
    """Independently verify 4-graph structural integrity, identity separation, and signature."""
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    for graph_name in ("head", "assertion", "provenance", "publication_info"):
        if not isinstance(nanopub.get(graph_name), dict):
            errors.append({"code": f"MISSING_GRAPH_{graph_name.upper()}", "message": f"Required graph '{graph_name}' is absent or not an object."})
    if errors:
        return {"valid": False, "nanopub_id": nanopub.get("nanopub_id"), "errors": errors, "warnings": warnings}

    head = nanopub["head"]
    assertion = nanopub["assertion"]
    provenance = nanopub["provenance"]
    pubinfo = nanopub["publication_info"]
    integrity = pubinfo.get("integrity")

    # 1. Head URI linkage checks
    uri = nanopub.get("uri")
    if head.get("nanopub_uri") != uri:
        errors.append({"code": "HEAD_URI_MISMATCH", "message": f"head.nanopub_uri ({head.get('nanopub_uri')}) != uri ({uri})."})
    for field, suffix in (
        ("has_assertion", "#assertion"),
        ("has_provenance", "#provenance"),
        ("has_publication_info", "#pubinfo"),
    ):
        expected = f"{uri}{suffix}"
        if head.get(field) != expected:
            errors.append({"code": "HEAD_GRAPH_LINK_BROKEN", "message": f"head.{field} expected {expected}, got {head.get(field)}."})

    # 2. Identity separation checks (nanopub-java Issue #127 / PR #128)
    assertion_agent = assertion.get("assertion_agent")
    signer_id = pubinfo.get("signed_by")
    creator_id = pubinfo.get("creator")
    if not signer_id:
        errors.append({"code": "MISSING_SIGNER_IDENTITY", "message": "publication_info.signed_by is required."})
    if not assertion_agent:
        warnings.append({"code": "ASSERTION_AGENT_UNSPECIFIED", "message": "assertion.assertion_agent not explicitly named; defaulted to source attribution."})
    if integrity and integrity.get("signed_by") != signer_id:
        errors.append({
            "code": "SIGNER_IDENTITY_TAMPERED",
            "message": f"publication_info.signed_by ({signer_id}) disagrees with integrity.signed_by ({integrity.get('signed_by')}).",
        })
    if assertion.get("asserted_by_distinct_agent") is True and assertion_agent and signer_id and assertion_agent == signer_id:
        errors.append({
            "code": "AGENT_SIGNER_ROLE_CONFLATION",
            "message": "Claim declared distinct assertion_agent vs signer, but both fields hold the same identity.",
        })
    if pubinfo.get("no_external_publish") is not True:
        errors.append({"code": "EXTERNAL_PUBLISH_GUARD_MISSING", "message": "Local draft must keep publication_info.no_external_publish == true."})

    # 3. Cryptographic digest & signature checks
    if not isinstance(integrity, dict):
        errors.append({"code": "MISSING_INTEGRITY_ENVELOPE", "message": "publication_info.integrity is missing."})
    else:
        recomputed = compute_graph_digests(nanopub)
        for key, code in (
            ("head_sha256", "HEAD_GRAPH_TAMPERED"),
            ("assertion_sha256", "ASSERTION_GRAPH_TAMPERED"),
            ("provenance_sha256", "PROVENANCE_GRAPH_TAMPERED"),
            ("pubinfo_unsigned_sha256", "PUBINFO_GRAPH_TAMPERED"),
            ("trusty_artifact_code", "TRUSTY_ARTIFACT_HASH_MISMATCH"),
        ):
            if integrity.get(key) != recomputed[key]:
                errors.append({
                    "code": code,
                    "message": f"{key} mismatch: recorded={integrity.get(key)!r} vs recomputed={recomputed[key]!r}.",
                })
        hint = str(integrity.get("signing_key_hint") or "local-draft-ephemeral")
        expected_sig = _compute_local_signature(recomputed, signer_id=str(signer_id or ""), signing_key_hint=hint)
        if not hmac.compare_digest(str(integrity.get("signature_hex") or ""), expected_sig):
            errors.append({
                "code": "SIGNATURE_VERIFICATION_FAILED",
                "message": "Local HMAC-SHA256 signature does not match canonical 4-graph digest and signer_id.",
            })

    return {
        "valid": len(errors) == 0,
        "nanopub_id": nanopub.get("nanopub_id"),
        "uri": uri,
        "identity_roles": {
            "assertion_agent": assertion_agent,
            "provenance_authors": (provenance.get("source_metadata") or {}).get("authors"),
            "pubinfo_creator": creator_id,
            "pubinfo_signer": signer_id,
        },
        "errors": errors,
        "warnings": warnings,
    }


def export_jsonld(case_id: str, nanopubs: list[dict[str, Any]]) -> dict[str, Any]:
    """Export Nanopublications as machine-readable JSON-LD with named @graph containers."""
    graph_entries: list[dict[str, Any]] = []
    for npub in nanopubs:
        h = npub["head"]
        a = npub["assertion"]
        p = npub["provenance"]
        pub = npub["publication_info"]
        pred_uri = PREDICATE_URIS.get(a["predicate"], f"urn:nanopub:predicate:{a['predicate']}")
        subj_uri = _slug_uri("subject", a["subject"])
        obj_uri = _slug_uri("object", a["object"])

        graph_entries.append({
            "@id": h["nanopub_uri"],
            "@type": "np:Nanopublication",
            "np:hasAssertion": {
                "@id": h["has_assertion"],
                "@graph": [
                    {
                        "@id": subj_uri,
                        "rdfs:label": a["subject"],
                        pred_uri: {"@id": obj_uri, "rdfs:label": a["object"]},
                        "local:predicateCode": a["predicate"],
                        "local:atomicStatement": a["atomic_statement"],
                        "local:polarity": a["polarity"],
                        "local:condition": a.get("condition"),
                        "local:assertionAgent": a.get("assertion_agent"),
                    }
                ],
            },
            "np:hasProvenance": {
                "@id": h["has_provenance"],
                "@graph": [
                    {
                        "@id": h["has_assertion"],
                        "prov:wasDerivedFrom": p.get("source_file"),
                        "local:paragraphId": p.get("paragraph_id"),
                        "local:verbatimQuote": p.get("quote"),
                        "local:evidenceType": p.get("evidence_type"),
                        "local:sourceMetadata": p.get("source_metadata") or {},
                    }
                ],
            },
            "np:hasPublicationInfo": {
                "@id": h["has_publication_info"],
                "@graph": [
                    {
                        "@id": h["nanopub_uri"],
                        "dct:creator": pub.get("creator"),
                        "npx:signedBy": pub.get("signed_by"),
                        "local:state": pub.get("state"),
                        "local:noExternalPublish": pub.get("no_external_publish"),
                        "local:trustyArtifactCode": (pub.get("integrity") or {}).get("trusty_artifact_code"),
                        "local:signatureHex": (pub.get("integrity") or {}).get("signature_hex"),
                    }
                ],
            },
        })

    return {
        "@context": JSONLD_CONTEXT,
        "local:caseId": case_id,
        "@graph": graph_entries,
    }


def export_trig(nanopubs: list[dict[str, Any]]) -> str:
    """Serialize 4-graph Nanopublications in valid, human- and parser-readable RDF TriG syntax."""
    lines: list[str] = [
        "@prefix np: <http://www.nanopub.org/nschema#> .",
        "@prefix npx: <http://purl.org/nanopub/x/> .",
        "@prefix prov: <http://www.w3.org/ns/prov#> .",
        "@prefix dct: <http://purl.org/dc/terms/> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "@prefix local: <urn:nanopub:local:> .",
        "",
    ]
    for npub in nanopubs:
        h = npub["head"]
        a = npub["assertion"]
        p = npub["provenance"]
        pub = npub["publication_info"]
        uri = h["nanopub_uri"]
        pred_uri = PREDICATE_URIS.get(a["predicate"], f"urn:nanopub:predicate:{a['predicate']}")
        subj_uri = _slug_uri("subject", a["subject"])
        obj_uri = _slug_uri("object", a["object"])
        integ = pub.get("integrity") or {}

        lines.extend([
            f"<{uri}#Head> {{",
            f"  <{uri}> a np:Nanopublication ;",
            f"    np:hasAssertion <{h['has_assertion']}> ;",
            f"    np:hasProvenance <{h['has_provenance']}> ;",
            f"    np:hasPublicationInfo <{h['has_publication_info']}> .",
            "}",
            "",
            f"<{h['has_assertion']}> {{",
            f"  <{subj_uri}> rdfs:label {_escape_trig_lit(a['subject'])} ;",
            f"    <{pred_uri}> <{obj_uri}> .",
            f"  <{obj_uri}> rdfs:label {_escape_trig_lit(a['object'])} .",
            f"  <{h['has_assertion']}> local:atomicStatement {_escape_trig_lit(a['atomic_statement'])} ;",
            f"    local:predicateCode {_escape_trig_lit(a['predicate'])} ;",
            f"    local:polarity {_escape_trig_lit(a['polarity'])} ;",
        ])
        if a.get("condition"):
            lines.append(f"    local:condition {_escape_trig_lit(str(a['condition']))} ;")
        if a.get("assertion_agent"):
            lines.append(f"    local:assertionAgent {_escape_trig_lit(str(a['assertion_agent']))} ;")
        lines[-1] = lines[-1].rstrip(" ;") + " ."
        lines.extend(["}", ""])

        lines.extend([
            f"<{h['has_provenance']}> {{",
            f"  <{h['has_assertion']}> prov:wasDerivedFrom {_escape_trig_lit(str(p.get('source_file', '')))} ;",
            f"    local:paragraphId {_escape_trig_lit(str(p.get('paragraph_id', '')))} ;",
            f"    local:verbatimQuote {_escape_trig_lit(str(p.get('quote', '')))} ;",
            f"    local:evidenceType {_escape_trig_lit(str(p.get('evidence_type', 'direct_empirical')))} ;",
        ])
        meta = p.get("source_metadata") or {}
        if meta.get("doi"):
            lines.append(f"    dct:source {_escape_trig_lit(str(meta['doi']))} ;")
        if meta.get("year"):
            lines.append(f"    dct:issued {_escape_trig_lit(str(meta['year']))} ;")
        lines[-1] = lines[-1].rstrip(" ;") + " ."
        lines.extend(["}", ""])

        lines.extend([
            f"<{h['has_publication_info']}> {{",
            f"  <{uri}> dct:creator {_escape_trig_lit(str(pub.get('creator', 'local-curator')))} ;",
            f"    npx:signedBy {_escape_trig_lit(str(pub.get('signed_by', '')))} ;",
            f"    local:state {_escape_trig_lit(str(pub.get('state', 'local_draft')))} ;",
            f"    local:noExternalPublish \"{str(bool(pub.get('no_external_publish'))).lower()}\"^^xsd:boolean ;",
            f"    local:trustyArtifactCode {_escape_trig_lit(str(integ.get('trusty_artifact_code', '')))} ;",
            f"    local:signatureHex {_escape_trig_lit(str(integ.get('signature_hex', '')))} .",
            "}",
            "",
        ])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify 4-graph Nanopublication integrity & signatures locally.")
    parser.add_argument("--verify", type=Path, required=True, help="Path to nanopublications.json")
    args = parser.parse_args(argv)
    data = json.loads(args.verify.read_text(encoding="utf-8"))
    items = data.get("nanopublications", []) if isinstance(data, dict) else data
    reports = [verify_nanopublication(npub) for npub in items]
    all_ok = all(r["valid"] for r in reports) and len(reports) > 0
    out = {
        "ok": all_ok,
        "checked_count": len(reports),
        "valid_count": sum(1 for r in reports if r["valid"]),
        "failed_count": sum(1 for r in reports if not r["valid"]),
        "reports": reports,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
