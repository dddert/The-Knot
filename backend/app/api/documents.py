from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Query
from app.api.deps import AuditContext, require_roles
from app.services.audit_service import AuditService
from app.services.document_service import DocumentService
from app.services.graph_service import GraphService
from app.services.ml_client import MLClient
from app.schemas.contracts import ExtractedDocument

router = APIRouter(prefix="/api/documents", tags=["documents"])
documents = DocumentService()
audit = AuditService()
graph = GraphService()
ml = MLClient()

INTERNAL_ROLES = ("researcher", "analyst", "manager", "admin")


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    access_level: str = Query(default="internal"),
    ctx: AuditContext = Depends(require_roles("admin", "manager", "analyst", "researcher")),
):
    if access_level not in {"public", "internal", "confidential"}:
        raise HTTPException(status_code=400, detail="Invalid access_level")
    if access_level == "confidential" and ctx.role not in {"admin", "manager"}:
        raise HTTPException(status_code=403, detail="Only admin / manager can upload confidential documents")

    result = documents.save_upload(file, access_level=access_level)
    document_id = result["document_id"]

    # Make the uploaded document searchable immediately. This path performs
    # deterministic page extraction + chunking + lexical/dense indexing only;
    # expensive LLM graph enrichment remains a separate "Process selected" step.
    try:
        payload = documents.build_extraction_payload(
            document_id,
            visible_access_levels=ctx.visible_access_levels,
        )
        index_result = await ml.index_document(payload)
        documents.mark_indexed(document_id)
        result["status"] = "indexed"
        result["retrieval_index"] = index_result
    except Exception as exc:
        result["status"] = "uploaded"
        result["indexing_error"] = f"{type(exc).__name__}: {str(exc)[:500]}"

    audit.log(
        "document_uploaded",
        user_id=ctx.user_id,
        role=ctx.role,
        target_type="document",
        target_id=document_id,
        payload=result,
    )
    return result


@router.post("/import-extracted")
def import_extracted_document(
    extracted: ExtractedDocument,
    ctx: AuditContext = Depends(require_roles("admin", "analyst")),
):
    result = graph.import_extracted_document(extracted, demo=False)
    audit.log(
        "extracted_document_imported",
        user_id=ctx.user_id,
        role=ctx.role,
        target_type="document",
        target_id=extracted.document_id,
        payload=result,
    )
    return result


@router.get("")
def list_documents(ctx: AuditContext = Depends(require_roles(*INTERNAL_ROLES))):
    include_storage = ctx.role == "admin"
    return {"items": documents.list_documents(include_storage_path=include_storage, visible_access_levels=ctx.visible_access_levels)}


@router.get("/{document_id}")
def get_document(document_id: str, ctx: AuditContext = Depends(require_roles(*INTERNAL_ROLES))):
    result = documents.get_document(document_id, include_storage_path=(ctx.role == "admin"), visible_access_levels=ctx.visible_access_levels)
    if not result:
        raise HTTPException(status_code=404, detail="Document not found")
    return result


@router.post("/{document_id}/process")
async def process_document(document_id: str, ctx: AuditContext = Depends(require_roles("admin", "manager", "analyst", "researcher"))):
    payload = documents.build_extraction_payload(
        document_id,
        visible_access_levels=ctx.visible_access_levels,
    )

    # Idempotent retrieval upsert first: old uploads created before this patch
    # also become searchable when the user presses "Process selected".
    index_result = await ml.index_document(payload)

    extracted = await ml.extract(payload)
    graph_result = graph.import_extracted_document(extracted, demo=False)
    documents.mark_processed(document_id)

    result = {
        **graph_result,
        "retrieval_index": index_result,
        "extraction_status": extracted.status,
        "extraction_warnings": extracted.warnings,
    }
    audit.log(
        "document_processed",
        user_id=ctx.user_id,
        role=ctx.role,
        target_type="document",
        target_id=document_id,
        payload=result,
    )
    return result


@router.post("/process-mock")
async def process_mock_document(ctx: AuditContext = Depends(require_roles("admin", "analyst", "manager", "researcher"))):
    document_id = "doc_mock_001"
    payload = documents.build_mock_extraction_payload()
    extracted = await ml.extract(payload)
    result = graph.import_extracted_document(extracted, demo=True)
    audit.log("mock_document_processed", user_id=ctx.user_id, role=ctx.role, target_type="document", target_id=document_id, payload=result)
    return result
