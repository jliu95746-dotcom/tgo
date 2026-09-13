/**
 * SkillDetailModal
 *
 * Shows the full detail of a skill with a file explorer on the left
 * and content viewer on the right.
 */

import React, { useState, useEffect, useMemo, useCallback } from 'react';
import {
  X,
  Zap,
  ShieldCheck,
  Loader2,
  ChevronRight,
  ChevronDown,
  Folder,
  File,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import SkillsApiService, { type SkillDetail } from '@/services/skillsApi';
import hljs from 'highlight.js';
import 'highlight.js/styles/github-dark.css';
import yaml from 'js-yaml';
import MarkdownContent from '@/components/chat/MarkdownContent';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Strip YAML frontmatter (--- ... ---) from markdown text. */
function stripFrontmatter(text: string): { frontmatter: Record<string, unknown> | null; body: string } {
  const match = text.match(/^---\s*\n([\s\S]*?)\n---\s*\n?([\s\S]*)$/);
  if (!match) return { frontmatter: null, body: text };

  let frontmatter: Record<string, unknown> | null = null;
  try {
    frontmatter = yaml.load(match[1]) as Record<string, unknown>;
  } catch {
    frontmatter = null;
  }
  return { frontmatter, body: match[2] };
}

/** Remove null/undefined values from an object for cleaner YAML display. */
function cleanObject(obj: Record<string, unknown>): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj)) {
    if (v != null && !(Array.isArray(v) && v.length === 0) && v !== '') {
      result[k] = v;
    }
  }
  return result;
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface SkillDetailModalProps {
  isOpen: boolean;
  onClose: () => void;
  skillName: string | null;
}

interface FileNode {
  name: string;
  path: string;
  type: 'file' | 'directory';
  children?: FileNode[];
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const SkillDetailModal: React.FC<SkillDetailModalProps> = ({
  isOpen,
  onClose,
  skillName,
}) => {
  const { t } = useTranslation();
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // File explorer state
  const [selectedPath, setSelectedPath] = useState<string>('SKILL.md');
  const [fileContent, setFileContent] = useState<string>('');
  const [isFileLoading, setIsFileLoading] = useState(false);
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(
    new Set(['scripts', 'references']),
  );

  // -----------------------------------------------------------------------
  // Fetch skill detail
  // -----------------------------------------------------------------------
  useEffect(() => {
    if (!isOpen || !skillName) {
      setDetail(null);
      setError(null);
      setSelectedPath('SKILL.md');
      setFileContent('');
      return;
    }

    let cancelled = false;
    const fetchDetail = async () => {
      setIsLoading(true);
      setError(null);
      try {
        const data = await SkillsApiService.getSkill(skillName);
        if (!cancelled) {
          setDetail(data);
          setFileContent(data.instructions);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    };
    fetchDetail();
    return () => {
      cancelled = true;
    };
  }, [isOpen, skillName]);

  // -----------------------------------------------------------------------
  // Fetch file content when selectedPath changes (NOT for SKILL.md)
  // -----------------------------------------------------------------------
  useEffect(() => {
    if (!isOpen || !skillName || !selectedPath) return;

    // SKILL.md is served from detail.instructions – no API call needed
    if (selectedPath === 'SKILL.md') {
      if (detail) setFileContent(detail.instructions);
      return;
    }

    let cancelled = false;
    const fetchFile = async () => {
      setIsFileLoading(true);
      try {
        const content = await SkillsApiService.getSkillFile(
          skillName,
          selectedPath,
        );
        if (!cancelled) setFileContent(content);
      } catch (err) {
        if (!cancelled)
          setFileContent(`Error loading file: ${err}`);
      } finally {
        if (!cancelled) setIsFileLoading(false);
      }
    };

    fetchFile();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, skillName, selectedPath]);

  // When detail arrives and SKILL.md is selected, update fileContent
  useEffect(() => {
    if (detail && selectedPath === 'SKILL.md') {
      setFileContent(detail.instructions);
    }
  }, [detail, selectedPath]);

  // -----------------------------------------------------------------------
  // Build file tree
  // -----------------------------------------------------------------------
  const fileTree = useMemo(() => {
    if (!detail) return [];

    const root: FileNode[] = [
      { name: 'SKILL.md', path: 'SKILL.md', type: 'file' },
    ];

    const buildTree = (paths: string[], dirName: string) => {
      if (paths.length === 0) return null;
      const dirNode: FileNode = {
        name: dirName,
        path: dirName,
        type: 'directory',
        children: [],
      };
      paths.forEach((p) => {
        const parts = p.split('/');
        let currentLevel = dirNode.children!;
        const subParts = parts[0] === dirName ? parts.slice(1) : parts;

        subParts.forEach((part, idx) => {
          const isLast = idx === subParts.length - 1;
          let node = currentLevel.find((n) => n.name === part);
          if (!node) {
            node = {
              name: part,
              path: p,
              type: isLast ? 'file' : 'directory',
              children: isLast ? undefined : [],
            };
            currentLevel.push(node);
          }
          if (node.children) {
            currentLevel = node.children;
          }
        });
      });
      return dirNode;
    };

    const scriptsNode = buildTree(detail.scripts, 'scripts');
    if (scriptsNode) root.push(scriptsNode);

    const refsNode = buildTree(detail.references, 'references');
    if (refsNode) root.push(refsNode);

    return root;
  }, [detail]);

  // -----------------------------------------------------------------------
  // Directory toggle
  // -----------------------------------------------------------------------
  const toggleDir = useCallback((path: string) => {
    setExpandedDirs((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  // -----------------------------------------------------------------------
  // File tree renderer
  // -----------------------------------------------------------------------
  const renderFileTree = (nodes: FileNode[], level = 0) => (
    <ul className="space-y-0.5">
      {nodes.map((node) => {
        const isExpanded = expandedDirs.has(node.path);
        const isSelected = selectedPath === node.path;

        return (
          <li key={node.path}>
            <div
              className={`
                flex items-center gap-1.5 px-2 py-1 rounded-md cursor-pointer text-sm transition-colors
                ${isSelected ? 'bg-blue-50 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400' : 'hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-700 dark:text-gray-300'}
              `}
              style={{ paddingLeft: `${level * 12 + 8}px` }}
              onClick={() => {
                if (node.type === 'directory') {
                  toggleDir(node.path);
                } else {
                  setSelectedPath(node.path);
                }
              }}
            >
              {node.type === 'directory' ? (
                <>
                  {isExpanded ? (
                    <ChevronDown className="w-3.5 h-3.5" />
                  ) : (
                    <ChevronRight className="w-3.5 h-3.5" />
                  )}
                  <Folder
                    className={`w-4 h-4 ${isExpanded ? 'text-blue-500' : 'text-gray-400'}`}
                  />
                </>
              ) : (
                <File
                  className={`w-4 h-4 ${isSelected ? 'text-blue-500' : 'text-gray-400'}`}
                />
              )}
              <span className="truncate">{node.name}</span>
            </div>
            {node.type === 'directory' && isExpanded && node.children && (
              <div>{renderFileTree(node.children, level + 1)}</div>
            )}
          </li>
        );
      })}
    </ul>
  );

  // -----------------------------------------------------------------------
  // Render YAML frontmatter block
  // -----------------------------------------------------------------------
  const renderYamlBlock = (data: Record<string, unknown>) => {
    const cleaned = cleanObject(data);
    if (Object.keys(cleaned).length === 0) return null;

    const yamlStr = yaml.dump(cleaned, {
      lineWidth: -1,
      noCompatMode: true,
    });

    return (
      <div className="mb-6 rounded-xl overflow-hidden border border-gray-200 dark:border-gray-800">
        <div className="bg-gray-50 dark:bg-gray-800/50 px-4 py-2 text-xs font-mono text-gray-500 border-b border-gray-200 dark:border-gray-800">
          YAML
        </div>
        <pre className="p-4 bg-[#1e1e1e] text-gray-300 text-xs font-mono overflow-auto">
          <code
            className="hljs language-yaml"
            dangerouslySetInnerHTML={{
              __html: hljs.highlight(yamlStr, { language: 'yaml' }).value,
            }}
          />
        </pre>
      </div>
    );
  };

  // -----------------------------------------------------------------------
  // Render markdown content (reuses the project's MarkdownContent component)
  // -----------------------------------------------------------------------

  // -----------------------------------------------------------------------
  // Main content renderer
  // -----------------------------------------------------------------------
  const renderContent = () => {
    if (isFileLoading) {
      return (
        <div className="flex items-center justify-center h-full">
          <Loader2 className="w-6 h-6 animate-spin text-blue-500" />
        </div>
      );
    }

    // --- SKILL.md: YAML header + rendered Markdown body ---
    if (selectedPath === 'SKILL.md' && detail) {
      const frontmatter = cleanObject({
        name: detail.name,
        description: detail.description,
        author: detail.author,
        version: detail.version,
        license: detail.license,
        tags: detail.tags,
        ...detail.metadata,
      });

      return (
        <div className="p-6">
          {renderYamlBlock(frontmatter)}
          <MarkdownContent content={fileContent} />
        </div>
      );
    }

    // --- Other .md files: strip frontmatter + render Markdown ---
    const ext = selectedPath.split('.').pop()?.toLowerCase();
    if (ext === 'md') {
      const { frontmatter, body } = stripFrontmatter(fileContent);
      return (
        <div className="p-6">
          {frontmatter && renderYamlBlock(frontmatter)}
          <MarkdownContent content={body} />
        </div>
      );
    }

    // --- Code files: syntax highlighting ---
    let lang = 'text';
    if (ext === 'py') lang = 'python';
    else if (ext === 'js' || ext === 'ts') lang = 'javascript';
    else if (ext === 'json') lang = 'json';
    else if (ext === 'sh' || ext === 'bash') lang = 'bash';
    else if (ext === 'yml' || ext === 'yaml') lang = 'yaml';
    else if (ext === 'txt') lang = 'text';

    let highlighted: string;
    try {
      highlighted =
        lang !== 'text'
          ? hljs.highlight(fileContent, { language: lang }).value
          : hljs.highlightAuto(fileContent).value;
    } catch {
      highlighted = fileContent;
    }

    return (
      <div className="h-full flex flex-col">
        <div className="flex-1 overflow-auto bg-[#1e1e1e] p-6">
          <pre className="text-xs font-mono leading-relaxed">
            <code
              className={`hljs language-${lang}`}
              dangerouslySetInnerHTML={{ __html: highlighted }}
            />
          </pre>
        </div>
      </div>
    );
  };

  // -----------------------------------------------------------------------
  // Render
  // -----------------------------------------------------------------------
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-black/40 backdrop-blur-sm"
        onClick={onClose}
      />

      <div className="relative w-full max-w-6xl h-[85vh] bg-white dark:bg-gray-900 rounded-2xl shadow-2xl flex flex-col overflow-hidden animate-in fade-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-3 border-b border-gray-200 dark:border-gray-800 shrink-0">
          <div className="flex items-center gap-3">
            <div className="p-1.5 bg-blue-50 dark:bg-blue-900/30 rounded-lg">
              <Zap className="w-4 h-4 text-blue-600" />
            </div>
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-bold text-gray-900 dark:text-gray-100">
                {detail?.display_name || detail?.name || skillName}
              </h2>
              <span className="text-[10px] text-gray-400 bg-gray-100 dark:bg-gray-800 px-1.5 py-0.5 rounded">
                SKILL
              </span>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
          >
            <X className="w-4 h-4 text-gray-500" />
          </button>
        </div>

        {/* Main Content */}
        <div className="flex-1 flex overflow-hidden">
          {/* Sidebar – File Explorer */}
          <div className="w-64 border-r border-gray-200 dark:border-gray-800 flex flex-col bg-gray-50/50 dark:bg-gray-900/50">
            <div className="p-3">
              <div className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-2 px-2">
                EXPLORER
              </div>
              {isLoading ? (
                <div className="flex justify-center py-8">
                  <Loader2 className="w-4 h-4 animate-spin text-gray-400" />
                </div>
              ) : (
                <div className="overflow-y-auto custom-scrollbar">
                  {renderFileTree(fileTree)}
                </div>
              )}
            </div>
          </div>

          {/* Content Viewer */}
          <div className="flex-1 flex flex-col overflow-hidden bg-white dark:bg-gray-900">
            {isLoading ? (
              <div className="flex-1 flex items-center justify-center">
                <Loader2 className="w-8 h-8 animate-spin text-blue-500" />
              </div>
            ) : error ? (
              <div className="flex-1 flex items-center justify-center text-red-500 p-6 text-center">
                {error}
              </div>
            ) : (
              <div className="flex-1 overflow-y-auto custom-scrollbar">
                {renderContent()}
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-6 py-3 border-t border-gray-200 dark:border-gray-800 shrink-0 bg-gray-50/50 dark:bg-gray-900/50">
          <div className="flex items-center gap-4 text-[11px] text-gray-500">
            {detail?.is_official && (
              <span className="flex items-center gap-1 text-blue-600 dark:text-blue-400">
                <ShieldCheck className="w-3 h-3" />
                {t('common.official', 'Official')}
              </span>
            )}
            {detail?.author && (
              <span>
                {t('skills.form.author', 'Author')}: {detail.author}
              </span>
            )}
            {detail?.version && <span>Version: {detail.version}</span>}
          </div>
          <button
            onClick={onClose}
            className="px-4 py-1.5 text-xs font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-800 rounded-lg transition-colors"
          >
            {t('common.close', 'Close')}
          </button>
        </div>
      </div>
    </div>
  );
};

export default SkillDetailModal;
