import * as yaml from 'js-yaml';

/**
 * Enhanced schema information for parameters
 */
export interface ParameterSchema {
  type: string;
  format?: string;
  enum?: any[];
  default?: any;
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  pattern?: string;
  items?: ParameterSchema; // for arrays
  properties?: Record<string, ParameterSchema>; // for objects
  additionalProperties?: boolean | ParameterSchema;
  required?: string[]; // for object properties
  example?: any;
  examples?: any[];
  title?: string;
  description?: string;
  nullable?: boolean;
  readOnly?: boolean;
  writeOnly?: boolean;
}

/**
 * Represents a parameter from OpenAPI schema with enhanced information
 */
export interface ParsedParameter {
  name: string;
  description?: string;
  type: 'query' | 'path' | 'header' | 'body';
  required: boolean;
  schema: ParameterSchema;
  example?: any;
  examples?: Record<string, any>;
  deprecated?: boolean;
  allowEmptyValue?: boolean;
  style?: string;
  explode?: boolean;
}

/**
 * Request body information
 */
export interface ParsedRequestBody {
  description?: string;
  required?: boolean;
  content: Record<string, {
    schema: ParameterSchema;
    example?: any;
    examples?: Record<string, any>;
  }>;
}

/**
 * Response information
 */
export interface ParsedResponse {
  description: string;
  content?: Record<string, {
    schema: ParameterSchema;
    example?: any;
    examples?: Record<string, any>;
  }>;
  headers?: Record<string, {
    description?: string;
    schema: ParameterSchema;
  }>;
}

/**
 * Security scheme types from OpenAPI
 */
export interface SecurityScheme {
  type: 'apiKey' | 'http' | 'oauth2' | 'openIdConnect';
  name?: string; // for apiKey
  in?: 'query' | 'header' | 'cookie'; // for apiKey
  scheme?: string; // for http (basic, bearer, etc.)
  bearerFormat?: string; // for http bearer
  description?: string;
}

/**
 * Parsed security requirement
 */
export interface ParsedSecurity {
  name: string;
  scheme: SecurityScheme;
  scopes?: string[];
}

/**
 * Auto-detected authentication configuration
 */
export interface AutoDetectedAuth {
  type: 'none' | 'header' | 'query';
  headerPrefix?: 'basic' | 'bearer' | 'custom';
  key?: string;
  description?: string;
  source: 'global' | 'operation';
  schemeName: string;
}

/**
 * Represents a parsed API endpoint from OpenAPI schema with enhanced information
 */
export interface ParsedEndpoint {
  name: string;
  description: string;
  summary?: string;
  method: string;
  path: string;
  parameters: ParsedParameter[];
  requestBody?: ParsedRequestBody;
  responses: Record<string, ParsedResponse>;
  tags?: string[];
  operationId?: string;
  deprecated?: boolean;
  security?: ParsedSecurity[];
  servers?: any[];
  autoDetectedAuth?: AutoDetectedAuth;
}

/**
 * Parse security schemes from OpenAPI components
 */
const parseSecuritySchemes = (components: any): Record<string, SecurityScheme> => {
  const schemes: Record<string, SecurityScheme> = {};

  if (!components?.securitySchemes) {
    return schemes;
  }

  Object.entries(components.securitySchemes).forEach(([name, scheme]: [string, any]) => {
    if (scheme.$ref) {
      // Skip $ref for now - would need resolution
      return;
    }

    schemes[name] = {
      type: scheme.type,
      name: scheme.name,
      in: scheme.in,
      scheme: scheme.scheme,
      bearerFormat: scheme.bearerFormat,
      description: scheme.description,
    };
  });

  return schemes;
};

/**
 * Parse security requirements and convert to ParsedSecurity
 */
const parseSecurityRequirements = (
  securityRequirements: any[],
  securitySchemes: Record<string, SecurityScheme>
): ParsedSecurity[] => {
  const parsedSecurity: ParsedSecurity[] = [];

  securityRequirements.forEach(requirement => {
    Object.entries(requirement).forEach(([schemeName, scopes]: [string, any]) => {
      const scheme = securitySchemes[schemeName];
      if (scheme) {
        parsedSecurity.push({
          name: schemeName,
          scheme,
          scopes: Array.isArray(scopes) ? scopes : [],
        });
      }
    });
  });

  return parsedSecurity;
};

/**
 * Auto-detect authentication configuration from security requirements
 */
const autoDetectAuth = (
  security: ParsedSecurity[],
  source: 'global' | 'operation'
): AutoDetectedAuth | undefined => {
  if (!security || security.length === 0) {
    return undefined;
  }

  // Use the first security requirement for auto-detection
  const firstSecurity = security[0];
  const scheme = firstSecurity.scheme;

  switch (scheme.type) {
    case 'apiKey':
      if (scheme.in === 'header') {
        return {
          type: 'header',
          headerPrefix: 'custom',
          key: scheme.name || 'Authorization',
          description: scheme.description,
          source,
          schemeName: firstSecurity.name,
        };
      } else if (scheme.in === 'query') {
        return {
          type: 'query',
          key: scheme.name || 'api_key',
          description: scheme.description,
          source,
          schemeName: firstSecurity.name,
        };
      }
      break;

    case 'http':
      if (scheme.scheme === 'bearer') {
        return {
          type: 'header',
          headerPrefix: 'bearer',
          key: 'Authorization',
          description: scheme.description,
          source,
          schemeName: firstSecurity.name,
        };
      } else if (scheme.scheme === 'basic') {
        return {
          type: 'header',
          headerPrefix: 'basic',
          key: 'Authorization',
          description: scheme.description,
          source,
          schemeName: firstSecurity.name,
        };
      }
      break;

    // OAuth2 and OpenID Connect would need more complex handling
    case 'oauth2':
    case 'openIdConnect':
      return {
        type: 'header',
        headerPrefix: 'bearer',
        key: 'Authorization',
        description: scheme.description || 'OAuth2/OpenID Connect authentication',
        source,
        schemeName: firstSecurity.name,
      };
  }

  return undefined;
};

/**
 * Convert OpenAPI schema to our ParameterSchema format
 */
const convertSchema = (schema: any): ParameterSchema => {
  if (!schema) {
    return { type: 'string' };
  }

  const converted: ParameterSchema = {
    type: schema.type || 'string',
    format: schema.format,
    enum: schema.enum,
    default: schema.default,
    minimum: schema.minimum,
    maximum: schema.maximum,
    minLength: schema.minLength,
    maxLength: schema.maxLength,
    pattern: schema.pattern,
    example: schema.example,
    examples: schema.examples,
    title: schema.title,
    description: schema.description,
    nullable: schema.nullable,
    readOnly: schema.readOnly,
    writeOnly: schema.writeOnly,
  };

  // Handle arrays
  if (schema.items) {
    converted.items = convertSchema(schema.items);
  }

  // Handle objects
  if (schema.properties) {
    converted.properties = {};
    Object.entries(schema.properties).forEach(([key, value]) => {
      converted.properties![key] = convertSchema(value);
    });
    converted.required = schema.required;
  }

  // Handle additionalProperties
  if (schema.additionalProperties !== undefined) {
    if (typeof schema.additionalProperties === 'boolean') {
      converted.additionalProperties = schema.additionalProperties;
    } else {
      converted.additionalProperties = convertSchema(schema.additionalProperties);
    }
  }

  return converted;
};

/**
 * Enhanced parameter parsing from OpenAPI operation
 */
const parseOperationParameters = (operation: any): ParsedParameter[] => {
  const parameters: ParsedParameter[] = [];

  // Parse URL/query/header parameters
  if (operation.parameters && Array.isArray(operation.parameters)) {
    operation.parameters.forEach((param: any) => {
      // Skip $ref parameters for now (would need resolution)
      if (param.$ref) return;

      const paramType = param.in === 'cookie' ? 'header' : param.in;
      parameters.push({
        name: param.name,
        description: param.description,
        type: paramType as 'query' | 'path' | 'header' | 'body',
        required: param.required || param.in === 'path', // path params are always required
        schema: convertSchema(param.schema || { type: 'string' }),
        example: param.example,
        examples: param.examples,
        deprecated: param.deprecated,
        allowEmptyValue: param.allowEmptyValue,
        style: param.style,
        explode: param.explode,
      });
    });
  }

  // Parse request body parameters
  if (operation.requestBody && !operation.requestBody.$ref) {
    const requestBody = operation.requestBody;

    // Handle different content types
    if (requestBody.content) {
      Object.entries(requestBody.content).forEach(([contentType, mediaType]: [string, any]) => {
        if (mediaType.schema) {
          const schema = convertSchema(mediaType.schema);

          // For JSON content, extract properties as body parameters
          if (contentType.includes('json') && schema.properties) {
            Object.entries(schema.properties).forEach(([propName, propSchema]) => {
              parameters.push({
                name: propName,
                description: propSchema.description,
                type: 'body',
                required: schema.required?.includes(propName) || false,
                schema: propSchema,
                example: propSchema.example || mediaType.example,
                examples: mediaType.examples,
              });
            });
          } else {
            // For other content types, add as single body parameter
            parameters.push({
              name: 'body',
              description: requestBody.description,
              type: 'body',
              required: requestBody.required || false,
              schema,
              example: mediaType.example,
              examples: mediaType.examples,
            });
          }
        }
      });
    }
  }

  return parameters;
};

/**
 * Parse responses from OpenAPI operation
 */
const parseOperationResponses = (responses: any): Record<string, ParsedResponse> => {
  const parsedResponses: Record<string, ParsedResponse> = {};

  if (!responses || typeof responses !== 'object') {
    return { 200: { description: 'Success' } };
  }

  Object.entries(responses).forEach(([statusCode, response]: [string, any]) => {
    if (response.$ref) {
      // Skip $ref responses for now
      return;
    }

    const parsedResponse: ParsedResponse = {
      description: response.description || `Response ${statusCode}`,
    };

    // Parse response content
    if (response.content) {
      parsedResponse.content = {};
      Object.entries(response.content).forEach(([contentType, mediaType]: [string, any]) => {
        parsedResponse.content![contentType] = {
          schema: convertSchema(mediaType.schema),
          example: mediaType.example,
          examples: mediaType.examples,
        };
      });
    }

    // Parse response headers
    if (response.headers) {
      parsedResponse.headers = {};
      Object.entries(response.headers).forEach(([headerName, header]: [string, any]) => {
        if (!header.$ref) {
          parsedResponse.headers![headerName] = {
            description: header.description,
            schema: convertSchema(header.schema),
          };
        }
      });
    }

    parsedResponses[statusCode] = parsedResponse;
  });

  return parsedResponses;
};





/**
 * Generate operation name from HTTP method and path
 */
const generateOperationName = (method: string, path: string): string => {
  // Remove path parameters and convert to camelCase
  const cleanPath = path
    .replace(/\{[^}]+\}/g, '') // Remove {param}
    .replace(/^\//, '') // Remove leading slash
    .replace(/\/$/, '') // Remove trailing slash
    .split('/')
    .filter(Boolean)
    .map((segment, index) =>
      index === 0 ? segment : segment.charAt(0).toUpperCase() + segment.slice(1)
    )
    .join('');

  const methodName = method.toLowerCase();

  if (cleanPath) {
    return `${methodName}${cleanPath.charAt(0).toUpperCase()}${cleanPath.slice(1)}`;
  }

  return `${methodName}Operation`;
};

/**
 * Enhanced OpenAPI schema parser (browser-compatible)
 */
export const parseOpenAPISchema = async (schemaContent: string): Promise<ParsedEndpoint[]> => {
  if (!schemaContent.trim()) {
    return [];
  }

  try {
    let parsedSchema: any;

    // Try to parse as JSON first
    try {
      parsedSchema = JSON.parse(schemaContent);
    } catch {
      // If JSON parsing fails, try YAML
      try {
        parsedSchema = yaml.load(schemaContent);
      } catch {
        // If both fail, return empty array
        return [];
      }
    }

    // Validate that it's an OpenAPI schema
    if (!parsedSchema || (!parsedSchema.openapi && !parsedSchema.swagger)) {
      return [];
    }

    // Parse security schemes
    const securitySchemes = parseSecuritySchemes(parsedSchema.components);

    // Parse global security requirements
    const globalSecurity = parsedSchema.security
      ? parseSecurityRequirements(parsedSchema.security, securitySchemes)
      : [];

    const globalAutoAuth = autoDetectAuth(globalSecurity, 'global');

    // Extract endpoints from paths
    const endpoints: ParsedEndpoint[] = [];
    const paths = parsedSchema.paths || {};

    Object.entries(paths).forEach(([path, pathItem]: [string, any]) => {
      if (!pathItem) return;

      // Supported HTTP methods
      const methods = ['get', 'post', 'put', 'delete', 'patch', 'head', 'options', 'trace'];

      methods.forEach(method => {
        const operation = pathItem[method];
        if (!operation) return;

        const name = operation.operationId || generateOperationName(method, path);
        const description = operation.description || operation.summary || `${method.toUpperCase()} ${path}`;
        const parameters = parseOperationParameters(operation);
        const responses = parseOperationResponses(operation.responses || {});

        // Parse operation-level security
        let operationSecurity: ParsedSecurity[] = [];
        let autoDetectedAuth: AutoDetectedAuth | undefined;

        if (operation.security) {
          // Operation has its own security requirements
          operationSecurity = parseSecurityRequirements(operation.security, securitySchemes);
          autoDetectedAuth = autoDetectAuth(operationSecurity, 'operation');
        } else if (globalSecurity.length > 0) {
          // Use global security requirements
          operationSecurity = globalSecurity;
          autoDetectedAuth = globalAutoAuth;
        }

        // Parse request body
        let requestBody: ParsedRequestBody | undefined;
        if (operation.requestBody && !operation.requestBody.$ref) {
          const reqBody = operation.requestBody;
          requestBody = {
            description: reqBody.description,
            required: reqBody.required,
            content: {},
          };

          if (reqBody.content) {
            Object.entries(reqBody.content).forEach(([contentType, mediaType]: [string, any]) => {
              requestBody!.content[contentType] = {
                schema: convertSchema(mediaType.schema),
                example: mediaType.example,
                examples: mediaType.examples,
              };
            });
          }
        }

        endpoints.push({
          name,
          description,
          summary: operation.summary,
          method: method.toUpperCase(),
          path,
          parameters,
          requestBody,
          responses,
          tags: operation.tags,
          operationId: operation.operationId,
          deprecated: operation.deprecated,
          security: operationSecurity,
          servers: operation.servers,
          autoDetectedAuth,
        });
      });
    });

    return endpoints;
  } catch (error) {
    console.error('Error parsing OpenAPI schema:', error);
    // Fallback to basic parsing for backward compatibility
    return parseOpenAPISchemaBasic(schemaContent);
  }
};

/**
 * Basic fallback parser for backward compatibility
 */
const parseOpenAPISchemaBasic = (schemaContent: string): ParsedEndpoint[] => {
  try {
    let parsedSchema: any;

    // Try to parse as JSON first
    try {
      parsedSchema = JSON.parse(schemaContent);
    } catch {
      // If JSON parsing fails, try YAML
      try {
        parsedSchema = yaml.load(schemaContent);
      } catch {
        return [];
      }
    }

    // Validate that it's an OpenAPI schema
    if (!parsedSchema || (!parsedSchema.openapi && !parsedSchema.swagger)) {
      return [];
    }

    // Extract endpoints from paths
    const endpoints: ParsedEndpoint[] = [];
    const paths = parsedSchema.paths || {};

    Object.entries(paths).forEach(([path, pathItem]: [string, any]) => {
      if (!pathItem) return;

      const methods = ['get', 'post', 'put', 'delete', 'patch', 'head', 'options', 'trace'];

      methods.forEach(method => {
        const operation = pathItem[method];
        if (!operation) return;

        const name = operation.operationId || generateOperationName(method, path);
        const description = operation.summary || operation.description || `${method.toUpperCase()} ${path}`;

        // Use the enhanced parameter parsing
        const parameters = parseOperationParameters(operation);
        const responses = parseOperationResponses(operation.responses || {});

        endpoints.push({
          name,
          description,
          method: method.toUpperCase(),
          path,
          parameters,
          responses,
          security: [],
        });
      });
    });

    return endpoints;
  } catch (error) {
    console.error('Error in basic OpenAPI parsing:', error);
    return [];
  }
};

/**
 * Get HTTP method color class for styling
 */
export const getMethodColorClass = (method: string): string => {
  const methodLower = method.toLowerCase();
  
  switch (methodLower) {
    case 'get':
      return 'text-blue-600';
    case 'post':
      return 'text-green-600';
    case 'put':
      return 'text-orange-600';
    case 'delete':
      return 'text-red-600';
    case 'patch':
      return 'text-purple-600';
    default:
      return 'text-gray-600';
  }
};

/**
 * Validate if content appears to be OpenAPI schema
 */
export const isValidOpenAPISchema = (content: string): boolean => {
  if (!content.trim()) return false;
  
  try {
    let parsed: any;
    
    try {
      parsed = JSON.parse(content);
    } catch {
      try {
        parsed = yaml.load(content);
      } catch {
        return false;
      }
    }
    
    return !!(parsed && (parsed.openapi || parsed.swagger));
  } catch {
    return false;
  }
};
