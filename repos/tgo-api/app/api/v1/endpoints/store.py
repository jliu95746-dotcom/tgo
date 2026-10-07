from typing import Any, List, Optional
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.database import get_db
from app.core.config import settings
from app.core.security import get_current_active_user
from app.core.logging import get_logger
from app.models.store_credential import StoreCredential
from app.models.staff import Staff
from app.schemas.store import (
    StoreCredential as StoreCredentialSchema, 
    StoreInstallRequest,
    StoreBindRequest,
)
from app.utils.crypto import encrypt_str, decrypt_str
from app.services.store_client import store_client
from app.services.ai_provider_sync import sync_provider_with_retry_and_update

logger = get_logger("endpoints.store")

router = APIRouter()


@router.post("/bind", response_model=StoreCredentialSchema)
async def bind_store(
    bind_in: StoreBindRequest,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(get_current_active_user),
) -> Any:
    """
    绑定商店到当前项目
    1. 用 access_token 调用商店 /auth/api-key 获取 api_key
    2. 存储到 api_store_credentials
    """
    project_id = current_user.project_id
    # 1. 调用商店获取 api_key
    try:
        result = await store_client.get_api_key(bind_in.access_token)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch API Key from Store: {str(e)}"
        )

    # 2. 检查是否已存在凭证
    credential = db.scalar(
        select(StoreCredential).where(StoreCredential.project_id == project_id)
    )
    
    if credential:
        credential.store_user_id = result["user_id"]
        credential.store_email = result["email"]
        credential.api_key_encrypted = encrypt_str(result["api_key"])
    else:
        credential = StoreCredential(
            project_id=project_id,
            store_user_id=result["user_id"],
            store_email=result["email"],
            api_key_encrypted=encrypt_str(result["api_key"]),
        )
        db.add(credential)
    
    db.commit()
    db.refresh(credential)
    return credential


async def _install_model_internal(resource_id: str, project_id: UUID, api_key: str, db: Session) -> Any:
    """Internal helper to install a model from store"""
    # 1. 调用商店 API 获取模型详情
    try:
        model_detail = await store_client.get_model(resource_id, api_key)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch model from Store: {str(e)}"
        )

    # 2. 确保本地有一个 "Store" 类型的 LLMProvider
    from app.models import AIProvider
    provider_name = f"Store-{model_detail.provider.name}"
    local_model_id = model_detail.name

    provider = db.scalar(
        select(AIProvider).where(
            AIProvider.project_id == project_id,
            AIProvider.name == provider_name,
            AIProvider.deleted_at.is_(None)
        )
    )
    
    if not provider:
        provider = AIProvider(
            project_id=project_id,
            provider="openai_compatible",
            name=provider_name,
            api_base_url=f"{settings.STORE_SERVICE_URL.rstrip('/')}/api/v1",
            api_key=encrypt_str(api_key),
            is_active=True,
            default_model=local_model_id,
            is_from_store=True,
            store_resource_id=model_detail.provider.id
        )
        db.add(provider)
    else:
        provider.is_from_store = True
        provider.store_resource_id = model_detail.provider.id
        provider.api_base_url = f"{settings.STORE_SERVICE_URL.rstrip('/')}/api/v1"
        if not provider.default_model:
            provider.default_model = local_model_id
        db.add(provider)
    
    db.flush()

    # 3. 创建本地模型记录
    from app.models import AIModel
    existing_model = db.scalar(
        select(AIModel).where(
            AIModel.provider_id == provider.id,
            AIModel.model_id == local_model_id,
            AIModel.deleted_at.is_(None)
        )
    )
    
    if not existing_model:
        existing_model = AIModel(
            provider_id=provider.id,
            provider="openai_compatible",
            model_id=local_model_id,
            model_name=model_detail.title_zh or model_detail.name,
            model_type=model_detail.model_type,
            description=model_detail.description_zh,
            is_active=True,
            capabilities=model_detail.config.get("capabilities", {}) if model_detail.config else {},
            store_resource_id=resource_id
        )
        db.add(existing_model)
        # Ensure it's associated in the relationship for immediate sync
        if existing_model not in provider.models:
            provider.models.append(existing_model)
    else:
        existing_model.model_name = model_detail.title_zh or model_detail.name
        existing_model.model_type = model_detail.model_type
        existing_model.description = model_detail.description_zh
        existing_model.capabilities = model_detail.config.get("capabilities", {}) if model_detail.config else {}
        existing_model.store_resource_id = resource_id
        db.add(existing_model)
    
    # 记录商店安装
    try:
        await store_client.install_model(resource_id, api_key)
    except Exception as e:
        logger.warning(f"Failed to record installation in store: {str(e)}")
    
    db.commit()
    db.refresh(provider)

    # Sync provider to tgo-ai service
    ok, err = await sync_provider_with_retry_and_update(db, provider)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to sync provider to AI service: {err}"
        )

    return {"success": True, "model_id": local_model_id, "provider": provider_name}


# --- Model Installation ---

@router.post("/install-model", response_model=Any)
async def install_model_from_store(
    install_in: StoreInstallRequest,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(get_current_active_user),
) -> Any:
    """从商店安装模型到项目"""
    from app.services.shared_models import shared_models_active
    if shared_models_active(db):
        raise HTTPException(403, "模型由平台统一配置，请联系平台管理员")
    project_id = current_user.project_id
    # 1. 获取项目绑定的商店凭证
    credential = db.scalar(
        select(StoreCredential).where(StoreCredential.project_id == project_id)
    )
    if not credential:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Project not bound to Store. Please bind credentials first."
        )
    
    api_key = decrypt_str(credential.api_key_encrypted)
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to decrypt Store API Key"
        )

    return await _install_model_internal(install_in.resource_id, project_id, api_key, db)


@router.delete("/uninstall-model/{resource_id}")
async def uninstall_model_from_store(
    resource_id: str,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(get_current_active_user),
) -> Any:
    """从本地项目卸载商店模型"""
    from app.services.shared_models import shared_models_active
    if shared_models_active(db):
        raise HTTPException(403, "模型由平台统一配置，请联系平台管理员")
    project_id = current_user.project_id
    
    # 1. 获取项目绑定的商店凭证
    credential = db.scalar(
        select(StoreCredential).where(StoreCredential.project_id == project_id)
    )
    if not credential:
        raise HTTPException(status_code=400, detail="Project not bound to Store")
        
    api_key = decrypt_str(credential.api_key_encrypted)
    if not api_key:
        raise HTTPException(status_code=500, detail="Failed to decrypt API Key")

    # 2. 调用商店 API 获取模型详情以获取本地标识符 (model_name)
    try:
        model_detail = await store_client.get_model(resource_id, api_key)
        local_model_id = model_detail.name
    except Exception as e:
        logger.error(f"Failed to fetch model detail from store during uninstall: {str(e)}")
        # 如果商店查不到，尝试从 resource_id 推断（这可能不准确，因为我们现在用 name 存）
        # 或者我们可以尝试在本地数据库找有没有前缀匹配的模型，但这不靠谱
        raise HTTPException(status_code=502, detail="Failed to fetch model info from store")

    # 3. 调用商店 API 记录卸载
    try:
        await store_client.uninstall_model(resource_id, api_key)
    except Exception as e:
        logger.warning(f"Failed to record uninstallation in store: {str(e)}")

    # 4. 从 AIProvider 关联的模型记录中移除
    from app.models import AIProvider, AIModel
    # 查找该项目的 Store Providers
    providers = db.scalars(
        select(AIProvider).where(
            AIProvider.project_id == project_id,
            AIProvider.name.like("Store-%"),
            AIProvider.deleted_at.is_(None)
        )
    ).all()
    
    for provider in providers:
        # 查找该 Provider 下的匹配模型
        model = db.scalar(
            select(AIModel).where(
                AIModel.provider_id == provider.id,
                AIModel.model_id == local_model_id,
                AIModel.deleted_at.is_(None)
            )
        )
        if model:
            db.delete(model)
            
            # 如果删掉的是默认模型，重新选一个或置空
            if provider.default_model == local_model_id:
                db.flush() # 确保 delete 已同步
                remaining = db.scalar(
                    select(AIModel.model_id).where(
                        AIModel.provider_id == provider.id,
                        AIModel.deleted_at.is_(None)
                    ).limit(1)
                )
                provider.default_model = remaining
                db.add(provider)
            
            # 记录需要同步的 Provider
            db.commit() # 先提交模型删除
            try:
                await sync_provider_with_retry_and_update(db, provider)
            except Exception as e:
                logger.warning(f"Failed to sync provider to AI service after model uninstall: {str(e)}")

    return {"success": True}


@router.get("/installed-models", response_model=List[str])
async def list_installed_models(
    db: Session = Depends(get_db),
    current_user: Staff = Depends(get_current_active_user),
) -> Any:
    """列出当前项目已安装的所有商店模型 (仅返回标识符列表)"""
    project_id = current_user.project_id
    
    # 通过关联 Provider 查找已安装模型
    try:
        from app.models import AIProvider, AIModel
        
        # 我们只需要 model_id (即商店中的 name)
        # 显式转换为字符串列表，防止 Row 对象泄露
        model_ids = db.scalars(
            select(AIModel.model_id)
            .join(AIProvider, AIModel.provider_id == AIProvider.id)
            .where(
                AIProvider.project_id == project_id,
                AIProvider.name.like("Store-%"),
                AIModel.deleted_at.is_(None),
                AIProvider.deleted_at.is_(None)
            )
        ).all()
        return [str(mid) for mid in model_ids]
    except Exception as e:
        logger.error(f"Failed to list installed models: {str(e)}")
        return []


@router.api_route("/proxy/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_store_request(
    path: str,
    request: Request,
    current_user: Staff = Depends(get_current_active_user),
):
    """
    透明代理 Store API 请求，不解析参数，直接转发。
    支持 GET, POST, PUT, DELETE, PATCH 等方法。
    
    认证处理:
    - 如果请求头包含 X-Store-Authorization，则使用它作为商店的 Authorization 头
    - 否则移除 TGO 的 Authorization 头（商店不认识）
    """
    if path.strip('/').split('/')[0] in ('agents', 'tools') or path.strip('/').startswith('install/tool/'):
        raise HTTPException(status_code=410, detail="This store feature is no longer available")

    # 1. 构建目标 URL
    target_url = f"{settings.STORE_SERVICE_URL.rstrip('/')}/api/v1/{path}"
    if request.query_params:
        target_url = f"{target_url}?{request.query_params}"

    # 2. 准备请求头
    # 移除可能导致远程服务器 403 的头部
    excluded_headers = (
        "host", 
        "content-length", 
        "authorization", 
        "origin", 
        "referer", 
        "connection",
        "accept-encoding"
    )
    headers = {k: v for k, v in request.headers.items() if k.lower() not in excluded_headers}
    
    # 伪装浏览器 User-Agent，防止被 WAF 拦截
    headers["user-agent"] = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    
    # 3. 处理商店认证
    # 如果前端传了 X-Store-Authorization，转换为 Authorization 头发给商店
    store_auth = request.headers.get("x-store-authorization")
    if store_auth:
        headers["authorization"] = store_auth
    
    # 4. 获取请求体
    body = await request.body()

    # 5. 发送请求
    async with httpx.AsyncClient(timeout=settings.STORE_TIMEOUT) as client:
        try:
            # 使用流式响应以处理大文件或保持性能
            # 注意：这里为了健壮性，不解析 body，直接转发
            proxy_response = await client.request(
                method=request.method,
                url=target_url,
                headers=headers,
                content=body,
                follow_redirects=True
            )
            
            # 5. 返回响应
            return Response(
                content=proxy_response.content,
                status_code=proxy_response.status_code,
                headers={k: v for k, v in proxy_response.headers.items() if k.lower() not in ("content-encoding", "transfer-encoding", "content-length")}
            )
        except httpx.RequestError as e:
            logger.error(f"Store proxy connection error: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to connect to Store service: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Store proxy unexpected error: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Internal error during Store proxy: {str(e)}"
            )


@router.get("/config")
async def get_store_config(
    current_user: Staff = Depends(get_current_active_user),
):
    """返回 Store 服务配置（Web URL 等）"""
    return {
        "store_web_url": settings.STORE_WEB_URL,
        "store_api_url": settings.STORE_SERVICE_URL,
    }
