/**
 * Base Data Transformation Utilities
 * Provides common transformation patterns and utilities
 */

import type { ToolStatus, ToolSourceType, AiToolStatus, ToolCategory } from '@/types';
import i18n from '@/i18n';


/**
 * Base transformation interface
 */
export interface BaseTransformer<TInput, TOutput> {
  transform(input: TInput): TOutput;
  transformMany(inputs: TInput[]): TOutput[];
}

/**
 * Abstract base transformer class
 */
export abstract class BaseTransformerClass<TInput, TOutput> implements BaseTransformer<TInput, TOutput> {
  abstract transform(input: TInput): TOutput;

  transformMany(inputs: TInput[]): TOutput[] {
    return inputs.map(input => this.transform(input));
  }
}

/**
 * Common transformation utilities
 */
export class TransformUtils {
  /**
   * Transform API ToolStatus to component AiToolStatus
   */
  static transformToolStatus(apiStatus: ToolStatus): AiToolStatus {
    switch (apiStatus) {
      case 'ACTIVE':
        return 'active';
      case 'INACTIVE':
        return 'inactive';
      case 'DEPRECATED':
        return 'error'; // Map deprecated to error for UI purposes
      default:
        return 'inactive';
    }
  }

  /**
   * Transform API category to component category
   */
  static transformCategory(apiCategory: string | null): ToolCategory {
    if (!apiCategory) return 'integration';

    const category = apiCategory.toLowerCase();

    // Map common categories
    if (['productivity', 'office', 'document'].includes(category)) return 'productivity';
    if (['communication', 'chat', 'message', 'email'].includes(category)) return 'communication';
    if (['data', 'database', 'analytics', 'analysis'].includes(category)) return 'data';
    if (['ai', 'ml', 'nlp', 'artificial'].includes(category)) return 'ai';
    if (['integration', 'api', 'webhook', 'service'].includes(category)) return 'integration';

    return 'integration'; // Default fallback
  }

  /**
   * Transform API ToolSourceType to readable string
   */
  static transformSourceType(sourceType: ToolSourceType): string {
    switch (sourceType) {
      case 'MARKETPLACE':
        return i18n.t('tools.source.marketplace', { defaultValue: '工具市场' });
      case 'CUSTOM':
        return i18n.t('tools.source.custom', { defaultValue: '自定义' });
      default:
        return i18n.t('tools.source.unknown', { defaultValue: '未知' });
    }
  }

  /**
   * Generate author name from source type
   */
  static getAuthor(sourceType: ToolSourceType): string {
    switch (sourceType) {
      case 'MARKETPLACE':
        return i18n.t('tools.author.marketplace', { defaultValue: '工具市场' });
      case 'CUSTOM':
        return i18n.t('tools.author.customUser', { defaultValue: '用户自定义' });
      default:
        return i18n.t('tools.author.unknown', { defaultValue: '未知来源' });
    }
  }

  /**
   * Generate rating based on execution count
   */
  static generateRating(executionCount: number | null): number {
    if (!executionCount || executionCount === 0) return 4.0; // Default rating

    // Generate rating based on usage (more usage = higher rating, with some randomness)
    const baseRating = 3.5;
    const usageBonus = Math.min((executionCount / 1000) * 0.5, 1.0); // Max 1.0 bonus
    const randomFactor = (Math.random() - 0.5) * 0.3; // ±0.15 randomness

    return Math.min(Math.max(baseRating + usageBonus + randomFactor, 3.0), 5.0);
  }

  /**
   * Generate success rate based on execution count
   */
  static generateSuccessRate(executionCount: number | null): number {
    if (!executionCount || executionCount === 0) return 0.95; // Default high success rate
    // Simulate success rate based on usage (higher usage = more stable)
    return Math.min(0.95 + (executionCount / 10000) * 0.05, 0.99);
  }

  /**
   * Generate average response time
   */
  static generateAvgResponseTime(): string {
    // Generate a realistic response time between 100ms and 2s
    const timeMs = Math.floor(Math.random() * 1900) + 100;
    return timeMs < 1000 ? `${timeMs}ms` : `${(timeMs / 1000).toFixed(1)}s`;
  }

  /**
   * Generate consistent mock data based on ID
   */
  static generateMockData(id: string) {
    // Generate consistent mock data based on tool ID
    const seed = id.split('').reduce((acc, char) => acc + char.charCodeAt(0), 0);

    return {
      rating: 3.5 + (seed % 15) / 10, // 3.5 - 5.0
      usageCount: 100 + (seed % 4900), // 100 - 5000
      successRate: 85 + (seed % 15), // 85% - 99%
      responseTime: 50 + (seed % 1950), // 50ms - 2000ms
      author: this.getAuthor('MARKETPLACE'),
    };
  }

  /**
   * Safe field extraction with fallback
   */
  static safeExtract<T>(obj: any, path: string, fallback: T): T {
    try {
      const keys = path.split('.');
      let current = obj;

      for (const key of keys) {
        if (current == null || typeof current !== 'object') {
          return fallback;
        }
        current = current[key];
      }

      return current != null ? current : fallback;
    } catch {
      return fallback;
    }
  }

  /**
   * Format date to locale string
   */
  static formatDate(dateString: string, locale: string = i18n.language): string {
    try {
      return new Date(dateString).toLocaleDateString(locale);
    } catch {
      return dateString;
    }
  }

  /**
   * Sanitize and validate string fields
   */
  static sanitizeString(value: any, fallback: string = ''): string {
    if (typeof value === 'string') {
      return value.trim();
    }
    return fallback;
  }

  /**
   * Sanitize and validate array fields
   */
  static sanitizeArray<T>(value: any, fallback: T[] = []): T[] {
    if (Array.isArray(value)) {
      return value;
    }
    return fallback;
  }

  /**
   * Sanitize and validate number fields
   */
  static sanitizeNumber(value: any, fallback: number = 0): number {
    if (typeof value === 'number' && !isNaN(value)) {
      return value;
    }
    if (typeof value === 'string') {
      const parsed = parseFloat(value);
      if (!isNaN(parsed)) {
        return parsed;
      }
    }
    return fallback;
  }

  /**
   * Create display name with fallback
   */
  static createDisplayName(title?: string, name?: string, fallback: string = 'Untitled'): string {
    return this.sanitizeString(title) || this.sanitizeString(name) || fallback;
  }

  /**
   * Extract tags from various formats
   */
  static extractTags(tags: any): string[] {
    if (Array.isArray(tags)) {
      return tags
        .map(tag => typeof tag === 'string' ? tag : tag?.name || tag?.label)
        .filter(Boolean);
    }
    if (typeof tags === 'string') {
      return tags.split(',').map(tag => tag.trim()).filter(Boolean);
    }
    return [];
  }

  /**
   * Create schema from method parameters
   */
  static createSchemaFromMethods(methods: any[]): Record<string, any> | undefined {
    if (!methods || methods.length === 0) return undefined;

    const firstMethod = methods[0];
    if (!firstMethod?.parameters) return undefined;

    return {
      type: 'object',
      properties: firstMethod.parameters.reduce((acc: any, param: any) => {
        acc[param.name] = {
          type: param.type,
          description: param.description,
        };
        return acc;
      }, {}),
      required: firstMethod.parameters
        .filter((param: any) => param.required)
        .map((param: any) => param.name),
    };
  }
}

/**
 * Transformation registry for managing transformers
 */
export class TransformRegistry {
  private static transformers = new Map<string, BaseTransformerClass<any, any>>();

  static register<TInput, TOutput>(
    key: string,
    transformer: BaseTransformerClass<TInput, TOutput>
  ): void {
    this.transformers.set(key, transformer);
  }

  static get<TInput, TOutput>(key: string): BaseTransformerClass<TInput, TOutput> | undefined {
    return this.transformers.get(key);
  }

  static transform<TInput, TOutput>(key: string, input: TInput): TOutput {
    const transformer = this.get<TInput, TOutput>(key);
    if (!transformer) {
      throw new Error(`Transformer not found: ${key}`);
    }
    return transformer.transform(input);
  }

  static transformMany<TInput, TOutput>(key: string, inputs: TInput[]): TOutput[] {
    const transformer = this.get<TInput, TOutput>(key);
    if (!transformer) {
      throw new Error(`Transformer not found: ${key}`);
    }
    return transformer.transformMany(inputs);
  }
}

export default TransformUtils;
