# TGO RAG Service

A production-ready **Retrieval-Augmented Generation (RAG)** service built with FastAPI, providing comprehensive document processing, vector storage, and advanced semantic search capabilities for AI-powered applications.

## 🚀 Overview

The TGO RAG Service is a microservice designed to handle the complete document lifecycle for RAG operations - from file upload and processing to semantic search and context retrieval. It features a modern, scalable architecture with multi-tenant support, async processing, and enterprise-grade reliability.

### Key Capabilities

- **📄 Document Processing**: Advanced support for PDF, Word, text, markdown, and HTML files
- **🔍 Hybrid Search**: Native PostgreSQL vector search combined with full-text search using reciprocal rank fusion
- **🧠 Multi-Provider Embeddings**: Support for OpenAI and Qwen3 embedding models with seamless switching
- **⚡ Async Processing**: Celery-based task queue for scalable document processing
- **🔐 Dual Authentication**: JWT tokens and API key authentication with project-level isolation
- **📊 Production Monitoring**: Health checks, metrics, structured logging, and observability
- **🐳 Container-Ready**: Complete Docker Compose setup with one-command deployment

## 🏗️ Architecture

### Technology Stack

| Component | Technology | Purpose |
|-----------|------------|---------|
| **API Framework** | FastAPI 0.104+ | High-performance async web framework |
| **Database** | PostgreSQL 16 + pgvector | Vector storage and full-text search |
| **Task Queue** | Celery + Redis | Async document processing |
| **Embeddings** | OpenAI / Qwen3 | Vector generation for semantic search |
| **Document Processing** | LangChain + Unstructured | Multi-format document parsing |
| **Containerization** | Docker + Docker Compose | Development and deployment |

### Core Services

```mermaid
graph TB
    subgraph "TGO RAG Service"
        API[FastAPI API Server]
        WORKER[Celery Workers]
        SCHEDULER[Celery Beat Scheduler]
    end

    subgraph "Storage Layer"
        PG[(PostgreSQL + pgvector)]
        REDIS[(Redis)]
        FILES[File Storage]
    end

    subgraph "External Services"
        OPENAI[OpenAI API]
        QWEN[Qwen3 API]
    end

    API --> PG
    API --> REDIS
    API --> FILES
    WORKER --> PG
    WORKER --> REDIS
    WORKER --> FILES
    WORKER --> OPENAI
    WORKER --> QWEN
    SCHEDULER --> REDIS
```

## ⚡ Quick Start

### Prerequisites

- **Docker & Docker Compose** (recommended)
- **Python 3.11+** (for local development)
- **OpenAI API Key** or **Qwen3 API Key** for embeddings

### 1. Environment Setup

```bash
# Clone the repository
git clone <repository-url>
cd tgo-rag-service

# Copy and configure environment variables
cp .env.example .env
# Edit .env with your API keys and configuration
```

### 2. Start Services

```bash
# Start all services with Docker Compose
docker-compose up -d

# Verify services are running
docker-compose ps
```

### 3. Health Check

```bash
# Check service health
curl http://localhost:8082/health

# Access API documentation
open http://localhost:8082/docs
```

## 📚 API Reference

### Authentication

The service supports dual authentication methods:

```bash
# API Key Authentication (recommended for service-to-service)
curl -H "X-API-Key: your-api-key" http://localhost:8082/v1/collections

# JWT Authentication (for user sessions)
curl -H "Authorization: Bearer your-jwt-token" http://localhost:8082/v1/collections
```

### Core Endpoints

#### Collections Management
Collections organize documents and files for RAG operations:

```bash
# List collections
GET /v1/collections

# Create a new collection
POST /v1/collections
{
  "display_name": "Knowledge Base",
  "description": "Company documentation and policies"
}

# Get collection details with statistics
GET /v1/collections/{id}?include_stats=true

# Search within a collection (Hybrid Search)
POST /v1/collections/{id}/documents/search
{
  "query": "employee benefits policy",
  "limit": 10,
  "min_score": 0.7
}
```

#### File Management
Handle document uploads and processing:

```bash
# Upload a file for processing
POST /v1/files
Content-Type: multipart/form-data
- file: document.pdf
- collection_id: uuid
- description: "Employee handbook"

# Check file processing status
GET /v1/files/{id}

# List processed document chunks
GET /v1/files/{id}/documents
```

#### Health & Monitoring
```bash
# Health check
GET /health

# Readiness probe
GET /ready

# Prometheus metrics
GET /metrics
```
## 💡 Usage Examples

### Complete RAG Workflow

Here's a step-by-step example of the complete document processing and search workflow:

```bash
# 1. Create a collection for organizing documents
curl -X POST http://localhost:8082/v1/collections \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev" \
  -d '{
    "display_name": "Company Knowledge Base",
    "description": "Internal documentation and policies"
  }'

# Response: {"id": "collection-uuid", "display_name": "Company Knowledge Base", ...}

# 2. Upload a document for processing
curl -X POST http://localhost:8082/v1/files \
  -H "X-API-Key: dev" \
  -F "file=@employee-handbook.pdf" \
  -F "collection_id=collection-uuid" \
  -F "description=Employee handbook with policies and procedures"

# Response: {"id": "file-uuid", "status": "processing", ...}

# 3. Monitor processing status
curl -H "X-API-Key: dev" http://localhost:8082/v1/files/file-uuid

# Response: {"id": "file-uuid", "status": "completed", "document_count": 25, ...}

# 4. Perform hybrid search across the collection
curl -X POST http://localhost:8082/v1/collections/collection-uuid/documents/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev" \
  -d '{
    "query": "vacation policy and time off procedures",
    "limit": 5,
    "min_score": 0.7
  }'

# Response: {"results": [...], "search_metadata": {"search_type": "hybrid", ...}}
```

### Advanced Search Features

```bash
# Semantic search with filters
curl -X POST http://localhost:8082/v1/collections/collection-uuid/documents/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev" \
  -d '{
    "query": "machine learning algorithms",
    "limit": 10,
    "min_score": 0.8,
    "filters": {
      "content_type": "paragraph",
      "language": "en"
    }
  }'
```

## 🏛️ Architecture

The service follows a modern microservices architecture optimized for RAG operations:

### Core Components

- **FastAPI Application**: High-performance async API server with automatic OpenAPI documentation
- **PostgreSQL + pgvector**: Primary database with native vector search and full-text search capabilities
- **Redis**: Caching layer and task queue backend for Celery
- **Celery Workers**: Distributed async document processing with batch optimization
- **LangChain Integration**: Unified document loaders and embedding operations

### Recent Improvements

- **🔍 Hybrid Search Refactoring**: Migrated to PGVectorStore's native hybrid search with reciprocal rank fusion
- **🧠 Multi-Provider Embeddings**: Added Qwen3 embedding support alongside OpenAI
- **⚡ Batch Processing**: Optimized document processing with configurable batch sizes
- **🔒 Enhanced Security**: Improved authentication with project-level isolation
- **📊 Better Monitoring**: Added structured logging and comprehensive health checks

## ⚙️ Configuration

### Environment Variables

Configure the service using environment variables. Copy `.env.example` to `.env` and customize:

```bash
# Application Settings
APP_NAME="TGO RAG Service"
APP_VERSION="0.1.0"
ENVIRONMENT=development
DEBUG=true
LOG_LEVEL=INFO
HOST=0.0.0.0
PORT=8082

# Database Configuration
DATABASE_URL=postgresql+asyncpg://rag_user:rag_password@localhost:5432/rag_service
DATABASE_POOL_SIZE=20
DATABASE_MAX_OVERFLOW=30

# Redis Configuration
REDIS_URL=redis://localhost:6379/0
REDIS_PASSWORD=redis_password

# Embedding Provider (choose one)
# OpenAI Configuration
OPENAI_API_KEY=your-openai-api-key
EMBEDDING_MODEL=text-embedding-ada-002
EMBEDDING_DIMENSIONS=1536

# Qwen3 Configuration (alternative)
QWEN3_API_KEY=your-dashscope-api-key
QWEN3_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN3_MODEL=qwen3.7-text-embedding

# Document Processing
MAX_FILE_SIZE_MB=100
CHUNK_SIZE=1000
CHUNK_OVERLAP=200
BATCH_SIZE=50

# Search Configuration
DEFAULT_SEARCH_LIMIT=20
MAX_SEARCH_LIMIT=100
MIN_SIMILARITY_SCORE=0.37
```

## 🛠️ Development

### Available Commands

```bash
# Development environment
make dev          # Start development environment
make dev-stop     # Stop development environment
make dev-clean    # Clean up containers and volumes

# Database operations
make migrate      # Run database migrations
make migrate-create MESSAGE="description"  # Create new migration
make db-shell     # Open PostgreSQL shell

# Testing
make test         # Run all tests
make test-unit    # Run unit tests only
make test-cov     # Run tests with coverage

# Code quality
make lint         # Run linting tools
make format       # Format code with black and isort

# Utilities
make shell        # Open shell in API container
make logs         # Show logs from all services
make health       # Check service health
```

### Project Structure

```
tgo-rag-service/
├── src/rag_service/           # Main application code
│   ├── models/                # SQLAlchemy database models
│   │   ├── base.py           # Base model classes
│   │   ├── projects.py       # Project management
│   │   ├── collections.py    # Document collections
│   │   ├── files.py          # File metadata
│   │   └── documents.py      # Document chunks with embeddings
│   ├── schemas/               # Pydantic request/response schemas
│   │   ├── collections.py    # Collection API schemas
│   │   ├── files.py          # File API schemas
│   │   ├── search.py         # Search API schemas
│   │   └── common.py         # Shared schemas
│   ├── routers/               # FastAPI route handlers
│   │   ├── collections.py    # Collection endpoints
│   │   ├── files.py          # File management endpoints
│   │   ├── health.py         # Health check endpoints
│   │   └── monitoring.py     # Metrics and monitoring
│   ├── services/              # Business logic services
│   │   ├── embedding.py      # Multi-provider embedding service
│   │   ├── search.py         # Hybrid search service
│   │   ├── vector_store.py   # Vector database operations
│   │   └── document_processing.py # Document parsing and chunking
│   ├── tasks/                 # Celery async tasks
│   │   ├── celery_app.py     # Celery configuration
│   │   └── document_processing.py # Document processing tasks
│   ├── config.py              # Application configuration
│   ├── database.py            # Database connection and utilities
│   ├── auth.py                # Authentication and authorization
│   └── main.py                # FastAPI application factory
├── migrations/                # Alembic database migrations
├── tests/                     # Test suite
├── docs/                      # API documentation and specifications
│   ├── TGO_RAG_Service_Documentation.yaml # OpenAPI spec
│   ├── architecture.md       # System architecture
│   ├── api-workflows.md      # API usage patterns
│   └── deployment-guide.md   # Deployment instructions
├── scripts/                   # Utility scripts
├── docker-compose.yml         # Development environment
├── Dockerfile                 # Container image definition
├── pyproject.toml            # Python dependencies and project config
└── .env.example              # Environment variables template
```

### Local Development

For local development without Docker:

1. **Install dependencies**:
   ```bash
   poetry install
   ```

2. **Set up PostgreSQL with pgvector**:
   ```bash
   # Install PostgreSQL and pgvector extension
   # Create database and run migrations
   poetry run alembic upgrade head
   ```

3. **Start Redis**:
   ```bash
   redis-server
   ```

4. **Start Celery worker**:
   ```bash
   poetry run celery -A src.rag_service.tasks.celery_app worker --loglevel=info
   ```

5. **Start the API server**:
   ```bash
   poetry run uvicorn src.rag_service.main:app --reload --port 8082
   ```

### Testing

```bash
# Run all tests
make test

# Run with coverage
make test-cov

# Run specific test file
poetry run pytest tests/test_search.py -v

# Run integration tests
poetry run pytest tests/integration/ -v
```

## 🗄️ Database Schema

The service uses a multi-tenant database schema with the following core tables:

| Table | Purpose | Key Features |
|-------|---------|--------------|
| `rag_projects` | Project management and API keys | Multi-tenant isolation |
| `rag_collections` | Document organization | Hierarchical document grouping |
| `rag_files` | File metadata and processing status | Upload tracking and status |
| `rag_file_documents` | Document chunks with embeddings | Vector storage with TSV search |

### Key Features:
- **Vector Storage**: Native pgvector support for semantic search
- **Full-Text Search**: PostgreSQL TSV columns for keyword search
- **Multi-Tenancy**: Project-level data isolation
- **Soft Deletes**: Audit trail with `deleted_at` timestamps

### Database Administration

The development environment includes **Adminer** for easy database management:

- **URL**: http://localhost:8080
- **Credentials**: `rag_user` / `rag_password` / `rag_service`
- **Features**: Browse tables, execute queries, export data, schema management

## 📖 Documentation

Comprehensive documentation is available in the `docs/` directory:

- **[OpenAPI Specification](docs/TGO_RAG_Service_Documentation.yaml)** - Complete API reference
- **[Architecture Guide](docs/architecture.md)** - System design and components
- **[API Workflows](docs/api-workflows.md)** - Usage patterns and examples
- **[Deployment Guide](docs/deployment-guide.md)** - Production deployment instructions
- **[Qwen3 Integration](docs/qwen3_embedding_integration.md)** - Multi-provider embedding setup

## 🤝 Contributing

We welcome contributions! Please follow these guidelines:

1. **Fork** the repository
2. **Create** a feature branch: `git checkout -b feature/amazing-feature`
3. **Make** your changes and add comprehensive tests
4. **Run** the test suite: `make test`
5. **Check** code quality: `make lint`
6. **Commit** with conventional messages: `git commit -m 'feat: add amazing feature'`
7. **Push** to your branch: `git push origin feature/amazing-feature`
8. **Open** a Pull Request with detailed description

### Development Standards

- **Code Style**: Follow PEP 8 and use Black formatter
- **Type Safety**: Add type hints to all functions and classes
- **Testing**: Maintain >90% test coverage for new features
- **Documentation**: Update API docs and README for changes
- **Commits**: Use conventional commit format (`feat:`, `fix:`, `docs:`, etc.)

## 📄 License

This project is proprietary software owned by TGO-Tech. All rights reserved.

## 🆘 Support

For support and questions:

- **📚 Documentation**: Check the comprehensive `docs/` directory
- **🐛 Issues**: Create an issue in the repository with detailed information
- **📧 Email**: rag-support@tgo-tech.com
- **💬 Discussions**: Use GitHub Discussions for questions and ideas

---

**Built with ❤️ by the TGO-Tech Team**
