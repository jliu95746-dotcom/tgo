"""Staff file knowledge is usable after parsing without channel/approval setup."""
import os
from datetime import UTC,datetime
from uuid import uuid4
import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import create_async_engine
from src.rag_service.models import Collection,File,FileDocument,QAPair
from src.rag_service.models.knowledge_governance import KnowledgeGovernanceRecord
from src.rag_service.schemas.knowledge_governance import KnowledgeChannel
from src.rag_service.services.knowledge_governance import KnowledgeGovernancePolicy

def test_default_file_path_keeps_completion_and_tenant_guards():
    statement=KnowledgeGovernancePolicy.eligible_document_ids_statement(project_id=uuid4(),at=datetime.now(UTC),channel=KnowledgeChannel.WECOM_KF)
    sql=str(statement.compile(dialect=postgresql.dialect(),compile_kwargs={'literal_binds':True}))
    assert "collection_type = 'file'" in sql
    assert "status = 'completed'" in sql
    assert 'NOT (EXISTS' in sql and "source_origin = 'customer'" in sql
    assert 'rag_collections.deleted_at IS NULL' in sql

async def verify_database_policy(url):
    engine=create_async_engine(url)
    checks=[]
    try:
      async with engine.connect() as connection:
       transaction=await connection.begin()
       try:
        tables={
          FileDocument:'id uuid,project_id uuid,file_id uuid,collection_id uuid',
          File:'id uuid,project_id uuid,collection_id uuid,status text,deleted_at timestamptz',
          Collection:'id uuid,project_id uuid,collection_type collection_type_enum,deleted_at timestamptz',
          KnowledgeGovernanceRecord:'id uuid,project_id uuid,file_id uuid,qa_pair_id uuid,channels varchar[],review_status text,allow_automatic_reply boolean,source_origin text,effective_at timestamptz,expires_at timestamptz,deleted_at timestamptz',
          QAPair:'id uuid,project_id uuid,document_id uuid,deleted_at timestamptz',
        }
        for model,columns in tables.items():
          await connection.execute(text(f'CREATE TEMP TABLE {model.__tablename__} ({columns}) ON COMMIT DROP'))
        cases=[('completed',{},True),('no approval',{'governance':True},True),('pending',{'status':'pending'},False),('failed',{'status':'failed'},False),('deleted file',{'deleted_file':True},False),('deleted collection',{'deleted_collection':True},False),('other tenant file',{'other_file':True},False),('other tenant collection',{'other_collection':True},False),('customer material',{'customer':True},False),('unscoped attachment',{'attachment':True},False),('website requires review',{'kind':'website'},False)]
        for label,case,expected in cases:
          for model in tables:await connection.execute(text(f'TRUNCATE pg_temp.{model.__tablename__}'))
          project,other,collection,file,document=[uuid4() for _ in range(5)]
          await connection.execute(text(f'INSERT INTO {Collection.__tablename__} VALUES (:id,:project,:kind,:deleted)'),dict(id=collection,project=other if case.get('other_collection') else project,kind=case.get('kind','file'),deleted=datetime.now(UTC) if case.get('deleted_collection') else None))
          await connection.execute(text(f'INSERT INTO {File.__tablename__} VALUES (:id,:project,:collection,:status,:deleted)'),dict(id=file,project=other if case.get('other_file') else project,collection=None if case.get('attachment') else collection,status=case.get('status','completed'),deleted=datetime.now(UTC) if case.get('deleted_file') else None))
          await connection.execute(text(f'INSERT INTO {FileDocument.__tablename__} VALUES (:id,:project,:file,:collection)'),dict(id=document,project=project,file=file,collection=collection))
          if case.get('governance') or case.get('customer'):
            await connection.execute(text(f"INSERT INTO {KnowledgeGovernanceRecord.__tablename__} (id,project_id,file_id,channels,review_status,allow_automatic_reply,source_origin,effective_at) VALUES (:id,:project,:file,ARRAY['web'],'draft',false,:origin,now())"),dict(id=uuid4(),project=project,file=file,origin='customer' if case.get('customer') else 'internal'))
          for channel in KnowledgeChannel:
            result=(await connection.execute(KnowledgeGovernancePolicy.eligible_document_ids_statement(project_id=project,at=datetime.now(UTC),channel=channel))).scalars().all()
            assert (document in result)==expected,(label,channel,result)
            foreign=(await connection.execute(KnowledgeGovernancePolicy.eligible_document_ids_statement(project_id=other,at=datetime.now(UTC),channel=channel))).scalars().all()
            assert not foreign,(label,'tenant leak')
          checks.append(label)
       finally:await transaction.rollback()
    finally:await engine.dispose()
    return checks

@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv('SAAS_TEST_DATABASE_URL'),reason='Opt-in isolated PostgreSQL temporary tables')
async def test_ready_file_admission_against_postgresql():
    assert len(await verify_database_policy(os.environ['SAAS_TEST_DATABASE_URL']))==11
