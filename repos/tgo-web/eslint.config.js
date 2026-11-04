import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from '@typescript-eslint/eslint-plugin'
import tsparser from '@typescript-eslint/parser'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist', 'node_modules', '.tmp/**', '*.config.js', '*.config.ts']),
  // JavaScript files configuration
  {
    files: ['**/*.{js,jsx}'],
    extends: [
      js.configs.recommended,
      reactHooks.configs['recommended-latest'],
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: {
        ...globals.browser,
        ...globals.node,
      },
      parserOptions: {
        ecmaVersion: 'latest',
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    rules: {
      // Code Consistency Rules
      'no-unused-vars': ['error', { 
        varsIgnorePattern: '^_',
        argsIgnorePattern: '^_',
        ignoreRestSiblings: true
      }],
      
      // Production Safety Rules
      'no-console': ['warn', { allow: ['warn', 'error'] }],
      'no-debugger': 'error',
      'no-alert': 'error',
      
      // Code Quality Rules
      'prefer-const': 'error',
      'no-var': 'error',
      'no-duplicate-imports': 'error',
      'no-unused-expressions': 'error',
      'no-unreachable': 'error',
      
      // Code Style Consistency
      'object-shorthand': 'error',
      'prefer-template': 'error',
      'quote-props': ['error', 'as-needed'],
      
      // React Specific Rules
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
    },
  },
  // TypeScript files configuration
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      parser: tsparser,
      parserOptions: {
        ecmaVersion: 'latest',
        sourceType: 'module',
        ecmaFeatures: {
          jsx: true,
        },
      },
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    plugins: {
      '@typescript-eslint': tseslint,
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      // TypeScript handles these better
      'no-undef': 'off',
      'no-unused-vars': 'off',
      
      // TypeScript-specific rules
      '@typescript-eslint/no-unused-vars': ['error', { 
        varsIgnorePattern: '^_',
        argsIgnorePattern: '^_',
        ignoreRestSiblings: true
      }],
      
      // Code Quality Rules
      'prefer-const': 'error',
      'no-var': 'error',
      'no-duplicate-imports': 'error',
      'no-unused-expressions': 'error',
      'no-unreachable': 'error',
      
      // React Hooks Rules
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
      
      // Code Style
      'object-shorthand': 'error',
      'prefer-template': 'error',
      'quote-props': ['error', 'as-needed'],
      
      // Mock Data Safety Rules
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['**/data/mock*'],
              message: 'Direct mock data imports are not allowed. Use mockDataHelper utility instead for production safety.'
            }
          ]
        }
      ],
    },
  },
  // Development files - more relaxed rules
  {
    files: [
      'src/data/**/*.ts', 
      'src/**/*.test.{ts,tsx}', 
      'src/**/*Test.{ts,tsx}',
      'scripts/**/*.js'
    ],
    rules: {
      'no-console': 'off',
      'no-restricted-imports': 'off',
      'prefer-template': 'off',
      'object-shorthand': 'off',
    },
  },
])
