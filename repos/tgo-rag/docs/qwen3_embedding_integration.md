# Qwen3-Embedding Integration Guide

## Overview

The RAG service now supports **Qwen3-Embedding** (text-embedding-v4) from Alibaba Cloud DashScope as an alternative to OpenAI embeddings. This integration provides a multi-provider embedding architecture while maintaining full backward compatibility.

## Features

### ✅ **Multi-Provider Support**
- **OpenAI Embeddings**: text-embedding-ada-002, text-embedding-3-small, etc.
- **Qwen3-Embedding**: text-embedding-v4 via Alibaba Cloud DashScope
- **Seamless Switching**: Change providers via environment variables

### ✅ **OpenAI Compatibility**
- Uses OpenAI-compatible API format
- Leverages existing LangChain OpenAI integration
- Consistent interface across providers

### ✅ **Configuration Flexibility**
- Environment variable configuration
- Provider-specific settings
- Automatic dimension detection

## Configuration

### Environment Variables

#### **Provider Selection**
```bash
# Set embedding provider (default: openai)
EMBEDDING_PROVIDER=qwen3  # or "openai"
```

#### **OpenAI Configuration** (default provider)
```bash
OPENAI_API_KEY=your_openai_api_key
EMBEDDING_MODEL=text-embedding-ada-002
EMBEDDING_DIMENSIONS=1536
```

#### **Qwen3-Embedding Configuration**
```bash
EMBEDDING_PROVIDER=qwen3
QWEN3_API_KEY=your_dashscope_api_key
QWEN3_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN3_MODEL=text-embedding-v4
QWEN3_DIMENSIONS=1536
```

### Configuration File (.env)
```env
# Embedding Provider Configuration
EMBEDDING_PROVIDER=qwen3

# Qwen3-Embedding Settings
QWEN3_API_KEY=sk-your-dashscope-api-key
QWEN3_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN3_MODEL=text-embedding-v4
QWEN3_DIMENSIONS=1536

# Common Settings
EMBEDDING_BATCH_SIZE=100
```

## API Key Setup

### **Alibaba Cloud DashScope API Key**

1. **Create Account**: Sign up at [Alibaba Cloud](https://www.alibabacloud.com/)
2. **Access DashScope**: Navigate to DashScope console
3. **Generate API Key**: Create a new API key for text-embedding-v4
4. **Set Environment Variable**: 
   ```bash
   export QWEN3_API_KEY=sk-your-dashscope-api-key
   ```

### **API Key Format**
- Qwen3 API keys typically start with `sk-`
- Keep your API key secure and never commit it to version control

## Usage Examples

### **Basic Usage**
```python
from src.rag_service.services.embedding import get_embedding_service

# Get embedding service (uses configured provider)
service = get_embedding_service()

# Generate single embedding
embedding = await service.generate_embedding("Hello, world!")

# Generate batch embeddings
embeddings = await service.generate_embeddings_batch([
    "First document",
    "Second document",
    "Third document"
])

# Get provider information
provider = service.get_embedding_provider()  # "qwen3" or "openai"
model = service.get_embedding_model()        # "text-embedding-v4"
dimensions = service.get_embedding_dimensions()  # 1536
```

### **Provider-Specific Configuration**
```python
import os
from src.rag_service.services.embedding import get_embedding_service

# Switch to Qwen3 provider
os.environ['EMBEDDING_PROVIDER'] = 'qwen3'
os.environ['QWEN3_API_KEY'] = 'your-api-key'

service = get_embedding_service()
print(f"Using provider: {service.get_embedding_provider()}")
print(f"Model: {service.get_embedding_model()}")
print(f"Dimensions: {service.get_embedding_dimensions()}")
```

## Model Specifications

### **Qwen3-Embedding (text-embedding-v4)**
- **Provider**: Alibaba Cloud DashScope
- **Dimensions**: TGO phase 1 uses 1536 because the existing pgvector column is fixed at 1536 dimensions
- **Context Length**: Long text support
- **Languages**: Multi-language (English, Chinese, etc.)
- **API Format**: OpenAI-compatible

### **OpenAI Embeddings**
- **Provider**: OpenAI
- **Models**: text-embedding-ada-002, text-embedding-3-small, etc.
- **Dimensions**: 1536 (ada-002), varies by model
- **API Format**: Native OpenAI API

## Architecture

### **Multi-Provider Design**
```
EmbeddingService
├── BaseEmbeddingClient (Abstract)
├── OpenAIEmbeddingClient
└── Qwen3EmbeddingClient
```

### **Provider Selection Logic**
1. Read `EMBEDDING_PROVIDER` environment variable
2. Create appropriate client based on provider
3. Validate required API keys
4. Initialize client with provider-specific settings

### **Error Handling**
- **Missing API Key**: Clear error messages for missing credentials
- **API Failures**: Proper exception handling with context
- **Provider Switching**: Graceful fallback and error reporting

## Integration Points

### **Document Processing Pipeline**
- Seamless integration with existing document processing
- Automatic embedding generation during file processing
- Provider-agnostic vector storage

### **Vector Store Integration**
- Compatible with existing pgvector storage
- Consistent embedding dimensions handling
- Automatic metadata management

### **Search Functionality**
- Works with existing semantic search
- Maintains search quality across providers
- Consistent similarity scoring

## Testing

### **Run Integration Tests**
```bash
# Test with current provider
poetry run python test_qwen3_embedding_integration.py

# Test with specific provider
EMBEDDING_PROVIDER=qwen3 poetry run python test_qwen3_embedding_integration.py
```

### **Manual Testing**
```bash
# Test configuration
poetry run python -c "
from src.rag_service.services.embedding import get_embedding_service
service = get_embedding_service()
print(f'Provider: {service.get_embedding_provider()}')
print(f'Model: {service.get_embedding_model()}')
"

# Test embedding generation (requires API key)
poetry run python -c "
import asyncio
from src.rag_service.services.embedding import get_embedding_service

async def test():
    service = get_embedding_service()
    embedding = await service.generate_embedding('Test text')
    print(f'Generated embedding with {len(embedding)} dimensions')

asyncio.run(test())
"
```

## Troubleshooting

### **Common Issues**

#### **"Qwen3 API key is required"**
- Set `QWEN3_API_KEY` environment variable
- Verify API key format (should start with `sk-`)
- Check API key permissions in DashScope console

#### **"Unsupported embedding provider"**
- Verify `EMBEDDING_PROVIDER` is set to `openai` or `qwen3`
- Check for typos in provider name

#### **Connection Errors**
- Verify `QWEN3_BASE_URL` is correct
- Check network connectivity to DashScope API
- Validate API key permissions

### **Debug Mode**
```bash
# Enable debug logging
export LOG_LEVEL=DEBUG

# Check service logs
docker-compose logs celery-worker | grep -i embedding
```

## Migration Guide

### **From OpenAI to Qwen3**
1. **Get Qwen3 API Key**: Sign up for Alibaba Cloud DashScope
2. **Update Configuration**: Set environment variables
3. **Test Integration**: Run integration tests
4. **Deploy Changes**: Update production environment

### **Switching Back to OpenAI**
```bash
# Simply change the provider
export EMBEDDING_PROVIDER=openai
export OPENAI_API_KEY=your_openai_key
```

## Performance Considerations

### **Qwen3-Embedding Benefits**
- **Cost Effective**: Competitive pricing
- **Multi-language**: Better support for Chinese text
- **Performance**: High-quality embeddings

### **Batch Processing**
- Both providers support batch processing
- Configurable batch sizes via `EMBEDDING_BATCH_SIZE`
- Automatic batching in document processing pipeline

## Security

### **API Key Management**
- Store API keys in environment variables
- Never commit API keys to version control
- Use secure key management in production
- Rotate API keys regularly

### **Network Security**
- All API calls use HTTPS
- Validate SSL certificates
- Monitor API usage and rate limits

## Support

### **Documentation**
- [Alibaba Cloud DashScope](https://help.aliyun.com/zh/dashscope/)
- [OpenAI Embeddings API](https://platform.openai.com/docs/guides/embeddings)

### **Monitoring**
- Check embedding service logs
- Monitor API usage and costs
- Track embedding generation performance

## Conclusion

The Qwen3-Embedding integration provides a robust, flexible embedding solution that expands the RAG service's capabilities while maintaining full backward compatibility. The multi-provider architecture ensures you can choose the best embedding model for your specific use case and requirements.
