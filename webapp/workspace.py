from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from tools.scenario_schema import ScenarioValidationError, validate_scenario_document
from webapp.auth import current_user_required
from webapp.db import get_db
from webapp.models import Collection, CollectionEnvValue, CollectionShare, Scenario, User

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = ROOT / "examples"
COLLECTION_FILENAME = "collection.json"
LEGACY_COLLECTION_FILENAME = "suite.json"
COLLECTION_FILENAMES = frozenset({COLLECTION_FILENAME, LEGACY_COLLECTION_FILENAME})
WORKSPACE_PREFIX = "workspace/"
LIBRARY_READONLY = "The public library is read-only. Copy the collection to your workspace to edit."

router = APIRouter(prefix="/api/workspace", tags=["workspace"])


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_collection_filename(name: str) -> bool:
    return str(name or "") in COLLECTION_FILENAMES


def next_owned_collection_position(db: Session, user: User, folder: str = "") -> int:
    current = db.scalar(
        select(func.max(Collection.position)).where(Collection.owner_id == user.id, Collection.folder == folder)
    )
    return int(current or 0) + 1


def is_workspace_path(path: str) -> bool:
    return str(path or "").replace("\\", "/").lstrip("./").startswith(WORKSPACE_PREFIX)


def workspace_collection_path(collection_id: str) -> str:
    return f"workspace/{collection_id}/{COLLECTION_FILENAME}"


def workspace_scenario_path(collection_id: str, name: str) -> str:
    return f"workspace/{collection_id}/{name}"


def parse_workspace_path(path: str) -> tuple[str, str]:
    rel = str(path or "").strip().replace("\\", "/").lstrip("./")
    if not rel.startswith(WORKSPACE_PREFIX):
        raise HTTPException(status_code=400, detail="Not a workspace path")
    parts = [part for part in rel.split("/") if part]
    if len(parts) != 3 or parts[0] != "workspace":
        raise HTTPException(status_code=400, detail="Workspace path must be workspace/<collection-id>/<file>")
    collection_id, filename = parts[1], parts[2]
    if filename in {".", ".."} or filename.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid workspace filename")
    return collection_id, filename


def safe_filename(value: str) -> str:
    raw = Path(str(value or "").strip().replace("\\", "/")).name
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "_", raw).strip("._")
    if not cleaned:
        cleaned = "scenario"
    if not cleaned.lower().endswith(".json"):
        cleaned += ".json"
    return cleaned


def is_collection_document(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    members = payload.get("scenarios")
    steps = payload.get("steps") or []
    if not isinstance(members, list):
        return False
    return not isinstance(steps, list) or len(steps) == 0


def empty_collection_document(name: str, description: str = "") -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "scenarios": [],
        "steps": [],
        "selected_environment": "",
        "environments": {},
        "random_generators": {},
    }


def collection_document_for_client(collection: Collection) -> dict[str, Any]:
    document = dict(collection.document or {})
    document["name"] = collection.name
    document["description"] = collection.description or ""
    document["selected_environment"] = collection.selected_environment or document.get("selected_environment") or ""
    document["scenarios"] = [item.name for item in collection.scenarios]
    document["steps"] = []
    return document


def owned_collection(db: Session, user: User, collection_id: str) -> Collection:
    collection = db.scalar(
        select(Collection)
        .options(selectinload(Collection.scenarios), selectinload(Collection.env_values))
        .where(Collection.id == collection_id, Collection.owner_id == user.id)
    )
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    return collection


def normalize_share_permission(value: Any) -> str:
    permission = str(value or "read").strip().lower()
    if permission not in {"read", "edit"}:
        raise HTTPException(status_code=400, detail="permission must be read or edit")
    return permission


def get_share_for_user(db: Session, collection_id: str, user_id: str) -> CollectionShare | None:
    return db.scalar(
        select(CollectionShare).where(
            CollectionShare.collection_id == collection_id,
            CollectionShare.user_id == user_id,
        )
    )


def upsert_collection_share(
    db: Session,
    *,
    collection: Collection,
    owner: User,
    recipient: User,
    permission: str,
    share_environment: bool = False,
) -> CollectionShare:
    if collection.owner_id != owner.id:
        raise HTTPException(status_code=403, detail="Only the owner can manage shares")
    if recipient.id == owner.id:
        raise HTTPException(status_code=400, detail="Cannot share a collection with yourself")
    cleaned = normalize_share_permission(permission)
    existing = get_share_for_user(db, collection.id, recipient.id)
    if existing is not None:
        existing.permission = cleaned
        existing.owner_id = owner.id
        existing.share_environment = bool(share_environment)
        return existing
    share = CollectionShare(
        collection_id=collection.id,
        owner_id=owner.id,
        user_id=recipient.id,
        permission=cleaned,
        share_environment=bool(share_environment),
    )
    db.add(share)
    db.flush()
    return share


def share_environment_enabled(db: Session, user: User, collection: Collection) -> bool:
    if collection.owner_id == user.id:
        return False
    share = get_share_for_user(db, collection.id, user.id)
    return bool(share and share.share_environment)


def env_values_owner_id(db: Session, user: User, collection: Collection) -> str:
    """Owner of the env row to read/write for this user on the collection."""
    if collection.owner_id == user.id:
        return user.id
    if share_environment_enabled(db, user, collection):
        return collection.owner_id
    return user.id


def load_collection_env_values(db: Session, user: User, collection_id: str, environment: str) -> dict[str, str]:
    collection = db.scalar(select(Collection).where(Collection.id == collection_id))
    owner_id = user.id
    if collection is not None:
        owner_id = env_values_owner_id(db, user, collection)
    row = db.scalar(
        select(CollectionEnvValue).where(
            CollectionEnvValue.collection_id == collection_id,
            CollectionEnvValue.owner_id == owner_id,
            CollectionEnvValue.environment_name == environment,
        )
    )
    return string_env_map(row.values if row else {})


def upsert_collection_env_values(
    db: Session, user: User, collection: Collection, environment: str, values: Any
) -> dict[str, str]:
    cleaned = string_env_map(values)
    owner_id = env_values_owner_id(db, user, collection)
    # Read-only shares cannot write even when environments are shared for viewing.
    if collection.owner_id != user.id:
        share = get_share_for_user(db, collection.id, user.id)
        if share is None or normalize_share_permission(share.permission) != "edit":
            raise HTTPException(status_code=403, detail="Read-only share")
    row = db.scalar(
        select(CollectionEnvValue).where(
            CollectionEnvValue.collection_id == collection.id,
            CollectionEnvValue.owner_id == owner_id,
            CollectionEnvValue.environment_name == environment,
        )
    )
    if row is None:
        row = CollectionEnvValue(
            owner_id=owner_id,
            collection_id=collection.id,
            environment_name=environment,
            values=cleaned,
        )
        db.add(row)
    else:
        row.values = cleaned
        row.updated_at = utcnow()
    return cleaned


def accessible_collection(
    db: Session,
    user: User,
    collection_id: str,
    *,
    write: bool = False,
) -> tuple[Collection, str]:
    """Return (collection, permission) where permission is owner|edit|read."""
    collection = db.scalar(
        select(Collection)
        .options(
            selectinload(Collection.scenarios),
            selectinload(Collection.env_values),
            selectinload(Collection.owner),
        )
        .where(Collection.id == collection_id)
    )
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    if collection.owner_id == user.id:
        return collection, "owner"
    share = get_share_for_user(db, collection_id, user.id)
    if share is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    permission = normalize_share_permission(share.permission)
    if write and permission != "edit":
        raise HTTPException(status_code=403, detail="This shared collection is read-only")
    return collection, permission


def apply_collection_fields(collection: Collection, payload: dict[str, Any]) -> None:
    document = dict(payload) if isinstance(payload, dict) else {}
    name = str(document.get("name") or collection.name or "Untitled").strip() or "Untitled"
    collection.name = name
    collection.description = str(document.get("description") or "")
    collection.selected_environment = str(document.get("selected_environment") or "")
    members = document.get("scenarios")
    if not isinstance(members, list):
        members = [item.name for item in collection.scenarios]
    document["name"] = collection.name
    document["description"] = collection.description
    document["selected_environment"] = collection.selected_environment
    document["scenarios"] = [str(item).strip() for item in members if isinstance(item, str) and item.strip()]
    document["steps"] = []
    collection.document = document
    collection.updated_at = utcnow()


def unique_scenario_name(collection: Collection, desired: str) -> str:
    candidate = safe_filename(desired)
    existing = {item.name.lower() for item in collection.scenarios}
    if candidate.lower() not in existing:
        return candidate
    stem = Path(candidate).stem
    for idx in range(2, 1000):
        next_name = f"{stem}_{idx}.json"
        if next_name.lower() not in existing:
            return next_name
    raise HTTPException(status_code=500, detail="Failed to allocate a unique scenario name")


def attach_scenario(db: Session, user: User, collection: Collection, name: str, document: dict[str, Any]) -> Scenario:
    filename = unique_scenario_name(collection, name)
    scenario = Scenario(
        owner_id=collection.owner_id,
        collection_id=collection.id,
        name=filename,
        document=document,
    )
    db.add(scenario)
    members = [item.name for item in collection.scenarios]
    if filename not in members:
        members.append(filename)
    payload = dict(collection.document or {})
    payload["scenarios"] = members
    collection.document = payload
    collection.updated_at = utcnow()
    db.flush()
    return scenario


def workspace_tree(user: User, db: Session) -> dict[str, Any]:
    collections = db.scalars(
        select(Collection)
        .options(selectinload(Collection.scenarios))
        .where(Collection.owner_id == user.id)
        .order_by(Collection.updated_at.desc())
    ).all()
    children: list[dict[str, Any]] = []
    for collection in collections:
        members = [item.name for item in collection.scenarios]
        children.append({
            "type": "file",
            "kind": "collection",
            "name": collection.name or COLLECTION_FILENAME,
            "path": workspace_collection_path(collection.id),
            "source": "workspace",
            "base_url": (collection.document or {}).get("base_url"),
            "step_count": 0,
            "member_count": len(members),
            "members": members,
        })
    return {
        "type": "dir",
        "name": "My workspace",
        "path": "workspace",
        "source": "workspace",
        "children": children,
    }


def _read_examples_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid scenario JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Scenario JSON must be an object")
    return payload


def _normalize_examples_rel(relative: str) -> str:
    raw = (relative or "").strip().replace("\\", "/")
    parts: list[str] = []
    for part in raw.split("/"):
        if part in ("", "."):
            continue
        if part == ".." or part.startswith("."):
            raise HTTPException(status_code=400, detail="Invalid path")
        parts.append(part)
    if not parts:
        raise HTTPException(status_code=400, detail="Invalid path")
    return "/".join(parts)


def resolve_examples_path(relative: str) -> Path:
    rel = _normalize_examples_rel(relative)
    base = EXAMPLES_DIR.resolve()
    path = (base / rel).resolve()
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid path") from exc
    return path


def clone_library_path(db: Session, user: User, library_path: str) -> Collection:
    file_path = resolve_examples_path(library_path)
    if not file_path.is_file() or file_path.suffix.lower() != ".json":
        raise HTTPException(status_code=404, detail="Library file not found")
    payload = _read_examples_json(file_path)
    source_dir = file_path.parent
    if not is_collection_document(payload):
        from tools.scenario_runner import discover_parent_collection

        parent = discover_parent_collection(file_path, payload)
        if isinstance(parent, dict) and parent.get("_file"):
            parent_path = source_dir / str(parent["_file"])
            if parent_path.is_file():
                payload = _read_examples_json(parent_path)
                source_dir = parent_path.parent
            else:
                payload = {
                    **empty_collection_document(str(payload.get("name") or file_path.stem)),
                    "environments": payload.get("environments") or {},
                    "random_generators": payload.get("random_generators") or {},
                    "selected_environment": payload.get("selected_environment") or "",
                    "scenarios": [file_path.name],
                }
        else:
            payload = {
                **empty_collection_document(str(payload.get("name") or file_path.stem)),
                "environments": payload.get("environments") or {},
                "random_generators": payload.get("random_generators") or {},
                "selected_environment": payload.get("selected_environment") or "",
                "scenarios": [file_path.name],
            }
            source_dir = file_path.parent

    collection = Collection(
        owner_id=user.id,
        name=str(payload.get("name") or source_dir.name or "Untitled"),
        description=str(payload.get("description") or ""),
        selected_environment=str(payload.get("selected_environment") or ""),
        folder="",
        position=next_owned_collection_position(db, user, ""),
        document=payload,
    )
    db.add(collection)
    db.flush()
    members = payload.get("scenarios") if isinstance(payload.get("scenarios"), list) else []
    cloned_names: list[str] = []
    for member in members:
        if not isinstance(member, str) or not member.strip():
            continue
        member_name = safe_filename(Path(member).name)
        member_path = source_dir / Path(member).name
        document = _read_examples_json(member_path) if member_path.is_file() else {"steps": []}
        db.add(Scenario(owner_id=user.id, collection_id=collection.id, name=member_name, document=document))
        cloned_names.append(member_name)
    payload = dict(payload)
    payload["scenarios"] = cloned_names
    apply_collection_fields(collection, payload)
    db.flush()
    db.refresh(collection)
    return collection


def clone_workspace_collection_to_user(
    db: Session,
    source: Collection,
    recipient: User,
    *,
    as_fork: bool = False,
) -> Collection:
    """Deep-copy a workspace collection (scenarios) to another user.

    Private env values are only copied when the recipient is taking a fork for themselves
    from a collection they can already access; live shares keep separate per-user env rows.
    """
    if source.owner_id == recipient.id and not as_fork:
        raise HTTPException(status_code=400, detail="Cannot share a collection with yourself")

    document = dict(source.document or {})
    document["name"] = source.name
    document["description"] = source.description or ""
    document["selected_environment"] = source.selected_environment or document.get("selected_environment") or ""
    document["scenarios"] = [item.name for item in source.scenarios]
    document["steps"] = []

    collection = Collection(
        owner_id=recipient.id,
        name=source.name if not as_fork else f"{source.name} (copy)",
        description=source.description or "",
        selected_environment=source.selected_environment or "",
        folder="",
        position=next_owned_collection_position(db, recipient, ""),
        source_collection_id=source.id if as_fork else None,
        document=document,
    )
    db.add(collection)
    db.flush()

    cloned_names: list[str] = []
    for scenario in source.scenarios:
        member_name = safe_filename(scenario.name)
        db.add(
            Scenario(
                owner_id=recipient.id,
                collection_id=collection.id,
                name=member_name,
                document=dict(scenario.document or {}),
            )
        )
        cloned_names.append(member_name)

    document = dict(document)
    document["name"] = collection.name
    document["scenarios"] = cloned_names
    apply_collection_fields(collection, document)

    if as_fork:
        for env_row in source.env_values:
            if env_row.owner_id not in {source.owner_id, recipient.id}:
                continue
            db.add(
                CollectionEnvValue(
                    owner_id=recipient.id,
                    collection_id=collection.id,
                    environment_name=env_row.environment_name,
                    values=dict(env_row.values or {}),
                )
            )

    db.flush()
    db.refresh(collection)
    return collection


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def diff_collections(local: Collection, source: Collection) -> dict[str, Any]:
    local_docs = {item.name: dict(item.document or {}) for item in local.scenarios}
    source_docs = {item.name: dict(item.document or {}) for item in source.scenarios}
    local_names = set(local_docs)
    source_names = set(source_docs)
    changed = sorted(
        name
        for name in local_names & source_names
        if _stable_json(local_docs[name]) != _stable_json(source_docs[name])
    )
    meta_changed: list[str] = []
    if (local.description or "") != (source.description or ""):
        meta_changed.append("description")
    if (local.selected_environment or "") != (source.selected_environment or ""):
        meta_changed.append("selected_environment")
    local_envs = (local.document or {}).get("environments") if isinstance((local.document or {}).get("environments"), dict) else {}
    source_envs = (source.document or {}).get("environments") if isinstance((source.document or {}).get("environments"), dict) else {}
    if _stable_json(local_envs) != _stable_json(source_envs):
        meta_changed.append("environments")
    local_consts = (local.document or {}).get("random_generators") if isinstance((local.document or {}).get("random_generators"), dict) else {}
    source_consts = (source.document or {}).get("random_generators") if isinstance((source.document or {}).get("random_generators"), dict) else {}
    if _stable_json(local_consts) != _stable_json(source_consts):
        meta_changed.append("random_generators")

    return {
        "local_id": local.id,
        "local_name": local.name,
        "source_id": source.id,
        "source_name": source.name,
        "added_in_source": sorted(source_names - local_names),
        "removed_in_source": sorted(local_names - source_names),
        "changed_scenarios": changed,
        "meta_changed": meta_changed,
        "identical": not (
            (source_names - local_names)
            or (local_names - source_names)
            or changed
            or meta_changed
        ),
    }


def sync_collection_from_source(db: Session, local: Collection, source: Collection) -> dict[str, Any]:
    diff = diff_collections(local, source)
    keep_name = local.name
    document = dict(source.document or {})
    document["name"] = keep_name
    document["description"] = source.description or ""
    document["selected_environment"] = source.selected_environment or document.get("selected_environment") or ""
    document["scenarios"] = [item.name for item in source.scenarios]
    document["steps"] = []

    by_name = {item.name: item for item in list(local.scenarios)}
    source_names = {item.name for item in source.scenarios}
    for scenario in list(local.scenarios):
        if scenario.name not in source_names:
            db.delete(scenario)
    db.flush()

    cloned_names: list[str] = []
    for source_scenario in source.scenarios:
        member_name = safe_filename(source_scenario.name)
        existing = by_name.get(member_name)
        payload = dict(source_scenario.document or {})
        if existing is None:
            db.add(
                Scenario(
                    owner_id=local.owner_id,
                    collection_id=local.id,
                    name=member_name,
                    document=payload,
                )
            )
        else:
            existing.document = payload
            existing.updated_at = utcnow()
        cloned_names.append(member_name)

    document["scenarios"] = cloned_names
    apply_collection_fields(local, document)
    local.source_collection_id = source.id
    db.flush()
    db.refresh(local)
    return {"status": "synced", "diff": diff, "path": workspace_collection_path(local.id), "name": local.name}


def string_env_map(values: Any) -> dict[str, str]:
    if not isinstance(values, dict):
        return {}
    cleaned: dict[str, str] = {}
    for key, value in values.items():
        name = str(key or "").strip()
        if not name:
            continue
        text = "" if value is None else str(value).strip()
        if text:
            cleaned[name] = text
    return cleaned


def materialize_workspace_run(db: Session, user: User, path: str, dest_dir: Path) -> Path:
    collection_id, filename = parse_workspace_path(path)
    collection, _permission = accessible_collection(db, user, collection_id)
    if is_collection_filename(filename) or filename == safe_filename(collection.name):
        collection_path = dest_dir / COLLECTION_FILENAME
        collection_path.write_text(json.dumps(collection_document_for_client(collection), indent=2), encoding="utf-8")
        for scenario in collection.scenarios:
            (dest_dir / scenario.name).write_text(
                json.dumps(scenario.document or {}, indent=2),
                encoding="utf-8",
            )
        return collection_path
    scenario = next((item for item in collection.scenarios if item.name == filename), None)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    scenario_path = dest_dir / scenario.name
    scenario_path.write_text(json.dumps(scenario.document or {}, indent=2), encoding="utf-8")
    return scenario_path


@router.get("/file")
def get_workspace_file(
    path: str = Query(..., min_length=1),
    user: User = Depends(current_user_required),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    collection_id, filename = parse_workspace_path(path)
    collection, permission = accessible_collection(db, user, collection_id)
    if is_collection_filename(filename):
        document = collection_document_for_client(collection)
        share_env = share_environment_enabled(db, user, collection)
        document["_access"] = {
            "permission": permission,
            "owner_id": collection.owner_id,
            "owner_name": (collection.owner.name if collection.owner else "") or "",
            "source_collection_id": collection.source_collection_id,
            "source_collection_path": (
                workspace_collection_path(collection.source_collection_id)
                if collection.source_collection_id
                else ""
            ),
            "share_environment": share_env,
        }
        return document
    scenario = next((item for item in collection.scenarios if item.name == filename), None)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    document = dict(scenario.document or {})
    document["_access"] = {
        "permission": permission,
        "owner_id": collection.owner_id,
        "share_environment": share_environment_enabled(db, user, collection),
    }
    return document


@router.post("/file")
def save_workspace_file(
    payload: dict[str, Any],
    path: str = Query(..., min_length=1),
    user: User = Depends(current_user_required),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Document must be an object")
    cleaned = {key: value for key, value in payload.items() if key != "_access"}
    collection_id, filename = parse_workspace_path(path)
    collection, _permission = accessible_collection(db, user, collection_id, write=True)
    if is_collection_filename(filename) or is_collection_document(cleaned):
        apply_collection_fields(collection, cleaned)
        return {"status": "saved", "path": workspace_collection_path(collection.id)}
    try:
        validate_scenario_document(cleaned)
    except ScenarioValidationError as exc:
        raise HTTPException(status_code=400, detail={"errors": exc.errors}) from exc
    filename = safe_filename(filename)
    scenario = next((item for item in collection.scenarios if item.name == filename), None)
    if scenario is None:
        scenario = attach_scenario(db, user, collection, filename, cleaned)
    else:
        scenario.document = cleaned
        scenario.updated_at = utcnow()
        collection.updated_at = utcnow()
    return {"status": "saved", "path": workspace_scenario_path(collection.id, scenario.name)}


@router.get("/parent-collection")
def workspace_parent_collection(
    path: str = Query(..., min_length=1),
    user: User = Depends(current_user_required),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    collection_id, filename = parse_workspace_path(path)
    collection, _permission = accessible_collection(db, user, collection_id)
    if is_collection_filename(filename):
        return {"status": "none"}
    document = collection_document_for_client(collection)
    return {
        "status": "ok",
        "path": workspace_collection_path(collection.id),
        "name": collection.name,
        "description": collection.description or "",
        "selected_environment": collection.selected_environment or "",
        "environments": document.get("environments") if isinstance(document.get("environments"), dict) else {},
        "random_generators": document.get("random_generators") if isinstance(document.get("random_generators"), dict) else {},
        "scenarios": document.get("scenarios") if isinstance(document.get("scenarios"), list) else [],
    }


@router.post("/collections")
def create_collection(
    payload: dict[str, Any] | None = None,
    user: User = Depends(current_user_required),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    body = payload if isinstance(payload, dict) else {}
    name = str(body.get("name") or "Untitled").strip() or "Untitled"
    folder = str(body.get("folder") or "").strip()
    document = (
        body.get("document")
        if isinstance(body.get("document"), dict)
        else empty_collection_document(name, str(body.get("description") or ""))
    )
    collection = Collection(
        owner_id=user.id,
        name=name,
        description=str(document.get("description") or body.get("description") or ""),
        selected_environment=str(document.get("selected_environment") or ""),
        folder=folder,
        position=next_owned_collection_position(db, user, folder),
        document=document,
    )
    db.add(collection)
    db.flush()
    apply_collection_fields(collection, {**document, "name": name})
    return {
        "status": "created",
        "id": collection.id,
        "path": workspace_collection_path(collection.id),
        "name": collection.name,
    }


@router.post("/clone")
def clone_from_library(
    payload: dict[str, Any],
    user: User = Depends(current_user_required),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    source = str(payload.get("path") or "").strip()
    if not source:
        raise HTTPException(status_code=400, detail="path is required")
    if is_workspace_path(source):
        raise HTTPException(status_code=400, detail="Path is already in your workspace")
    collection = clone_library_path(db, user, source)
    return {
        "status": "cloned",
        "id": collection.id,
        "path": workspace_collection_path(collection.id),
        "name": collection.name,
        "scenarios": [item.name for item in collection.scenarios],
    }


@router.delete("/item")
def delete_item(
    path: str = Query(..., min_length=1),
    user: User = Depends(current_user_required),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if is_workspace_path(path):
        return delete_workspace_item(db, user, path)
    return delete_library_item(path)


def delete_workspace_item(db: Session, user: User, path: str) -> dict[str, Any]:
    collection_id, filename = parse_workspace_path(path)
    if is_collection_filename(filename):
        collection = owned_collection(db, user, collection_id)
        db.delete(collection)
        return {"status": "deleted", "kind": "collection", "path": path}
    collection, _permission = accessible_collection(db, user, collection_id, write=True)
    scenario = next((item for item in collection.scenarios if item.name == filename), None)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    db.delete(scenario)
    remaining = [item.name for item in collection.scenarios if item.name != filename]
    payload = dict(collection.document or {})
    payload["scenarios"] = remaining
    apply_collection_fields(collection, payload)
    return {
        "status": "deleted",
        "kind": "scenario",
        "path": path,
        "collection_path": workspace_collection_path(collection.id),
    }


def delete_library_item(path: str) -> dict[str, Any]:
    file_path = resolve_examples_path(path)
    if not file_path.is_file() or file_path.suffix.lower() != ".json":
        raise HTTPException(status_code=404, detail="Library file not found")
    folder = file_path.parent
    if folder.resolve() == EXAMPLES_DIR.resolve():
        file_path.unlink()
        return {"status": "deleted", "kind": "file", "path": path}

    payload = _read_examples_json(file_path)
    if is_collection_filename(file_path.name) or is_collection_document(payload):
        members = payload.get("scenarios") if isinstance(payload.get("scenarios"), list) else []
        for member in members:
            if not isinstance(member, str) or not member.strip():
                continue
            child = folder / Path(member).name
            if child.is_file() and child != file_path:
                child.unlink()
        file_path.unlink()
        try:
            next(folder.iterdir())
        except StopIteration:
            folder.rmdir()
        return {"status": "deleted", "kind": "collection", "path": path}

    file_path.unlink()
    collection_path = folder / COLLECTION_FILENAME
    legacy_path = folder / LEGACY_COLLECTION_FILENAME
    doc_path = collection_path if collection_path.is_file() else legacy_path if legacy_path.is_file() else None
    if doc_path is not None:
        collection = _read_examples_json(doc_path)
        members = collection.get("scenarios") if isinstance(collection.get("scenarios"), list) else []
        collection["scenarios"] = [
            name for name in members if isinstance(name, str) and Path(name).name != file_path.name
        ]
        doc_path.write_text(json.dumps(collection, indent=2) + "\n", encoding="utf-8")
        relative = str(doc_path.relative_to(EXAMPLES_DIR)).replace("\\", "/")
    else:
        relative = str((folder / COLLECTION_FILENAME).relative_to(EXAMPLES_DIR)).replace("\\", "/")
    return {"status": "deleted", "kind": "scenario", "path": path, "collection_path": relative}


@router.get("/collections/{collection_id}/env")
def get_collection_env(
    collection_id: str,
    environment: str = Query("", alias="environment"),
    user: User = Depends(current_user_required),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    collection, _permission = accessible_collection(db, user, collection_id)
    env_name = environment or collection.selected_environment or ""
    return {
        "environment": env_name,
        "values": load_collection_env_values(db, user, collection.id, env_name),
        "share_environment": share_environment_enabled(db, user, collection),
        "env_owner_id": env_values_owner_id(db, user, collection),
    }


@router.put("/collections/{collection_id}/env")
def put_collection_env(
    collection_id: str,
    payload: dict[str, Any],
    user: User = Depends(current_user_required),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    collection, permission = accessible_collection(db, user, collection_id)
    if permission == "read":
        raise HTTPException(status_code=403, detail="Read-only share")
    env_name = str(payload.get("environment") or collection.selected_environment or "").strip()
    if not env_name:
        raise HTTPException(status_code=400, detail="environment is required")
    values = upsert_collection_env_values(db, user, collection, env_name, payload.get("values"))
    return {"status": "saved", "environment": env_name, "values": values}
