import { BaseTransformerClass, TransformUtils, TransformRegistry } from './base/BaseTransform';
import type { ToolResponse, AiTool } from '@/types';

export class ToolResponseToAiToolTransformer extends BaseTransformerClass<ToolResponse, AiTool> {
  transform(toolResponse: ToolResponse): AiTool {
    return {
      id: toolResponse.id,
      name: TransformUtils.sanitizeString(toolResponse.name),
      title: TransformUtils.createDisplayName(toolResponse.title_zh || toolResponse.title, toolResponse.name),
      description: TransformUtils.sanitizeString(toolResponse.description_zh || toolResponse.description, '暂无描述'),
      version: TransformUtils.sanitizeString(toolResponse.version, '1.0.0'),
      category: TransformUtils.transformCategory(toolResponse.category),
      tags: TransformUtils.extractTags(toolResponse.tags),
      status: TransformUtils.transformToolStatus(toolResponse.status),
      author: TransformUtils.getAuthor(toolResponse.tool_source_type),
      lastUpdated: TransformUtils.formatDate(toolResponse.updated_at),
      usageCount: TransformUtils.sanitizeNumber(toolResponse.execution_count),
      rating: TransformUtils.generateRating(toolResponse.execution_count),
      
      // Optional fields
      config: toolResponse.meta_data || undefined,
      capabilities: toolResponse.tags || undefined,
      successRate: TransformUtils.generateSuccessRate(toolResponse.execution_count),
      avgResponseTime: TransformUtils.generateAvgResponseTime(),
      input_schema: toolResponse.input_schema,
    };
  }
}

const toolResponseToAiToolTransformer = new ToolResponseToAiToolTransformer();
export const OptimizedTransforms = {
  toolResponseToAiTool: (item: ToolResponse): AiTool => toolResponseToAiToolTransformer.transform(item),
  toolResponsesToAiTools: (items: ToolResponse[]): AiTool[] => toolResponseToAiToolTransformer.transformMany(items),
};
TransformRegistry.register('toolResponseToAiTool', toolResponseToAiToolTransformer);
export { toolResponseToAiToolTransformer };
export const transformToolResponseToAiTool = OptimizedTransforms.toolResponseToAiTool;
export default OptimizedTransforms;
