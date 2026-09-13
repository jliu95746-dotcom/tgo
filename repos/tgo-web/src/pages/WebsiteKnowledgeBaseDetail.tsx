import React, { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  ArrowLeft, RefreshCw, Globe, ExternalLink, Plus, Loader2, CheckCircle, AlertCircle,
  ChevronRight, ChevronDown, Trash2, RotateCw, ArrowDownRight, X
} from 'lucide-react';
import { KnowledgeBaseApiService, type WebsitePageResponse, type CrawlProgressSchema } from '@/services/knowledgeBaseApi';
import { transformCollectionToKnowledgeBase } from '@/utils/knowledgeBaseTransforms';
import { useToast } from '@/hooks/useToast';
import type { KnowledgeBase } from '@/types';
import { useTranslation } from 'react-i18next';
import { SourceGovernanceSection } from '@/components/knowledge/SourceGovernanceSection';
import { KnowledgeVersionsPanel } from '@/components/knowledge/KnowledgeVersionsPanel';
import { knowledgeVersionsApi } from '@/services/knowledgeVersionsApi';
import { useAuthStore } from '@/stores/authStore';

// Tree node interface for hierarchical page display
interface PageTreeNode extends WebsitePageResponse {
  children: PageTreeNode[];
  isExpanded: boolean;
  isLoading: boolean;
  hasChildren: boolean;
}

// Pure conversion: independent of page state and stable across refreshes.
const pageToTreeNode = (page: WebsitePageResponse, defaultExpanded: boolean = false): PageTreeNode => {
  const childNodes: PageTreeNode[] = page.children
    ? page.children.map(child => pageToTreeNode(child, false))
    : [];
  return {
    ...page,
    children: childNodes,
    isExpanded: defaultExpanded && childNodes.length > 0,
    isLoading: false,
    hasChildren: page.has_children ?? childNodes.length > 0,
  };
};

/**
 * Website Knowledge Base Detail Page Component
 * Displays crawled pages in a tree structure with crawl progress
 */
const WebsiteKnowledgeBaseDetail: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { showToast } = useToast();
  const { t } = useTranslation();
  const admin = useAuthStore(state => state.user?.role === 'admin');
  const [versionRefresh, setVersionRefresh] = useState(0);

  // State
  const [knowledgeBase, setKnowledgeBase] = useState<KnowledgeBase | null>(null);
  const [crawlProgress, setCrawlProgress] = useState<CrawlProgressSchema | null>(null);
  const [rootPages, setRootPages] = useState<PageTreeNode[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingPages, setIsLoadingPages] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Add page dialog state
  const [isAddPageDialogOpen, setIsAddPageDialogOpen] = useState(false);
  const [newPageUrl, setNewPageUrl] = useState('');
  const [newPageMaxDepth, setNewPageMaxDepth] = useState(0);
  const [newPageParentId, setNewPageParentId] = useState<string | null>(null);
  const [isAddingPage, setIsAddingPage] = useState(false);

  // Page operations state
  const [deletingPages, setDeletingPages] = useState<Set<string>>(new Set());
  const [recrawlingPages, setRecrawlingPages] = useState<Set<string>>(new Set());
  const [crawlingDeeperPages, setCrawlingDeeperPages] = useState<Set<string>>(new Set());

  // Flatten tree to list for parent page selector
  const flattenTree = useCallback((nodes: PageTreeNode[], prefix: string = ''): Array<{ id: string; label: string; depth: number }> => {
    const result: Array<{ id: string; label: string; depth: number }> = [];
    for (const node of nodes) {
      const label = prefix + (node.title || new URL(node.url).pathname || node.url);
      result.push({ id: node.id, label, depth: node.depth });
      if (node.children.length > 0) {
        result.push(...flattenTree(node.children, '  '.repeat(node.depth + 1)));
      }
    }
    return result;
  }, []);

  // Load knowledge base data
  const loadKnowledgeBase = useCallback(async () => {
    if (!id) return;

    try {
      const collection = await KnowledgeBaseApiService.getCollection(id);
      const kb = transformCollectionToKnowledgeBase(collection);
      setKnowledgeBase(kb);

      // Check if it's a website type, if not redirect
      if (collection.collection_type !== 'website') {
        navigate(`/knowledge/${id}`, { replace: true });
        return;
      }
    } catch (err) {
      console.error('Failed to load knowledge base:', err);
      setError(err instanceof Error ? err.message : t('knowledge.website.loadFailed'));
    }
  }, [id, navigate, t]);

  // Load crawl progress
  const loadCrawlProgress = useCallback(async () => {
    if (!id) return;
    try {
      const progress = await KnowledgeBaseApiService.getWebsiteCrawlProgress(id);
      setCrawlProgress(progress);
    } catch (err) {
      console.error('Failed to load crawl progress:', err);
    }
  }, [id]);

  // Load children of a page (for lazy loading deeper levels)
  const loadChildPages = useCallback(async (parentId: string): Promise<PageTreeNode[]> => {
    if (!id) return [];

    try {
      const response = await KnowledgeBaseApiService.getWebsitePages(id, {
        parent_page_id: parentId,
        limit: 100,
      });
      return response.data.map(page => pageToTreeNode(page, false));
    } catch (err) {
      console.error('Failed to load child pages:', err);
      return [];
    }
  }, [id]);

  // Load root pages with default expansion to level 2
  const loadRootPages = useCallback(async () => {
    if (!id) return;

    setIsLoadingPages(true);
    try {
      // Get root pages (depth=0) with their direct children (tree_depth=1)
      // This allows default expansion to show 2 levels
      const response = await KnowledgeBaseApiService.getWebsitePages(id, {
        depth: 0,
        tree_depth: 2, // Include direct children in response
        limit: 100,
      });

      // Convert root pages to tree nodes with isExpanded=true
      // pageToTreeNode will recursively convert children if present
      const rootNodes = response.data.map(page => pageToTreeNode(page, true));

      setRootPages(rootNodes);
    } catch (err) {
      console.error('Failed to load pages:', err);
      showToast('error', t('knowledge.website.loadPagesFailed'));
    } finally {
      setIsLoadingPages(false);
    }
  }, [id, showToast, t]);

  // Toggle expand/collapse of a tree node
  const toggleNodeExpand = useCallback(async (nodeId: string) => {
    // Find the node first to check its current state
    const findNode = (nodes: PageTreeNode[]): PageTreeNode | null => {
      for (const node of nodes) {
        if (node.id === nodeId) return node;
        const found = findNode(node.children);
        if (found) return found;
      }
      return null;
    };

    const node = findNode(rootPages);
    if (!node) return;

    // If already expanded, just collapse
    if (node.isExpanded) {
      const updateNodes = (nodes: PageTreeNode[]): PageTreeNode[] => {
        return nodes.map(n => {
          if (n.id === nodeId) {
            return { ...n, isExpanded: false };
          }
          if (n.children.length > 0) {
            return { ...n, children: updateNodes(n.children) };
          }
          return n;
        });
      };
      setRootPages(prev => updateNodes(prev));
      return;
    }

    // If not expanded and has no loaded children but hasChildren=true, need to load
    if (node.children.length === 0 && node.hasChildren) {
      // Set loading state
      const setLoading = (nodes: PageTreeNode[]): PageTreeNode[] => {
        return nodes.map(n => {
          if (n.id === nodeId) {
            return { ...n, isLoading: true };
          }
          if (n.children.length > 0) {
            return { ...n, children: setLoading(n.children) };
          }
          return n;
        });
      };
      setRootPages(prev => setLoading(prev));

      // Load children
      const children = await loadChildPages(nodeId);

      const updateWithChildren = (nodes: PageTreeNode[]): PageTreeNode[] => {
        return nodes.map(n => {
          if (n.id === nodeId) {
            return {
              ...n,
              children,
              isExpanded: children.length > 0,
              isLoading: false,
              hasChildren: children.length > 0
            };
          }
          if (n.children.length > 0) {
            return { ...n, children: updateWithChildren(n.children) };
          }
          return n;
        });
      };
      setRootPages(prev => updateWithChildren(prev));
    } else if (node.children.length > 0) {
      // Has loaded children, just expand
      const updateNodes = (nodes: PageTreeNode[]): PageTreeNode[] => {
        return nodes.map(n => {
          if (n.id === nodeId) {
            return { ...n, isExpanded: true };
          }
          if (n.children.length > 0) {
            return { ...n, children: updateNodes(n.children) };
          }
          return n;
        });
      };
      setRootPages(prev => updateNodes(prev));
    }
  }, [rootPages, loadChildPages]);

  // Initial load
  useEffect(() => {
    const initialize = async () => {
      setIsLoading(true);
      await loadKnowledgeBase();
      setIsLoading(false);
    };
    initialize();
  }, [loadKnowledgeBase]);

  // Load pages and progress when knowledge base is loaded
  useEffect(() => {
    if (knowledgeBase) {
      loadRootPages();
      loadCrawlProgress();
    }
  }, [knowledgeBase, loadRootPages, loadCrawlProgress]);

  // Handle refresh
  const handleRefresh = async () => {
    await Promise.all([loadRootPages(), loadCrawlProgress()]);
  };

  // Handle back navigation
  const handleBack = () => {
    navigate('/knowledge');
  };

  // Reset add page dialog state
  const resetAddPageDialog = () => {
    setNewPageUrl('');
    setNewPageMaxDepth(0);
    setNewPageParentId(null);
    setIsAddPageDialogOpen(false);
  };

  // Add page to collection
  const handleAddPage = async () => {
    if (!id || !newPageUrl.trim()) return;

    // Validate URL
    try {
      new URL(newPageUrl.trim());
    } catch {
      showToast('error', t('knowledge.validation.urlInvalid'));
      return;
    }

    setIsAddingPage(true);
    try {
      const response = await KnowledgeBaseApiService.addWebsitePage(id, {
        url: newPageUrl.trim(),
        parent_page_id: newPageParentId,
        max_depth: newPageMaxDepth,
      });
      if (response.success) {
        showToast('success', t('knowledge.website.addPage.success'), response.message);
        resetAddPageDialog();
        await Promise.all([loadRootPages(), loadCrawlProgress()]);
      } else {
        showToast('warning', t('knowledge.website.addPage.skipped'), response.message);
      }
    } catch (err) {
      console.error('Failed to add page:', err);
      showToast('error', t('knowledge.website.addPage.failed'), err instanceof Error ? err.message : undefined);
    } finally {
      setIsAddingPage(false);
    }
  };

  // Delete a page
  const handleDeletePage = async (pageId: string) => {
    setDeletingPages(prev => new Set(prev).add(pageId));
    try {
      await KnowledgeBaseApiService.deleteWebsitePage(pageId);
      showToast('success', t('knowledge.website.deletePage.success'));
      await Promise.all([loadRootPages(), loadCrawlProgress()]);
    } catch (err) {
      console.error('Failed to delete page:', err);
      showToast('error', t('knowledge.website.deletePage.failed'), err instanceof Error ? err.message : undefined);
    } finally {
      setDeletingPages(prev => {
        const next = new Set(prev);
        next.delete(pageId);
        return next;
      });
    }
  };

  // Recrawl a page
  const handleRecrawlPage = async (pageId: string) => {
    setRecrawlingPages(prev => new Set(prev).add(pageId));
    try {
      const response = await knowledgeVersionsApi.change('website', pageId, {}, admin ? 'publish' : 'submit');
      showToast(response.state === 'failed' ? 'error' : 'success', response.error || t('knowledge.versions.queued', '已提交处理，完成前继续使用原生效内容。'));
      setVersionRefresh(value => value + 1);
    } catch (err) {
      console.error('Failed to recrawl page:', err);
      showToast('error', t('knowledge.website.recrawlPage.failed'), err instanceof Error ? err.message : undefined);
    } finally {
      setRecrawlingPages(prev => {
        const next = new Set(prev);
        next.delete(pageId);
        return next;
      });
    }
  };

  // Refresh a specific node's children while preserving tree state
  const refreshNodeChildren = useCallback(async (nodeId: string) => {
    if (!id) return;

    try {
      // Load children for the specific node
      const children = await loadChildPages(nodeId);

      // Update the tree, keeping all other nodes' expand state
      const updateNodeChildren = (nodes: PageTreeNode[]): PageTreeNode[] => {
        return nodes.map(node => {
          if (node.id === nodeId) {
            return {
              ...node,
              children,
              hasChildren: children.length > 0,
              isExpanded: true, // Auto-expand to show new children
              isLoading: false,
            };
          }
          if (node.children.length > 0) {
            return { ...node, children: updateNodeChildren(node.children) };
          }
          return node;
        });
      };

      setRootPages(prev => updateNodeChildren(prev));
    } catch (err) {
      console.error('Failed to refresh node children:', err);
    }
  }, [id, loadChildPages]);

  // Crawl deeper from a page
  const handleCrawlDeeper = async (pageId: string) => {
    setCrawlingDeeperPages(prev => new Set(prev).add(pageId));
    try {
      const response = await KnowledgeBaseApiService.crawlDeeperFromPage(pageId, { max_depth: 1 });
      if (response.success) {
        showToast('success', t('knowledge.website.crawlDeeper.success'),
          t('knowledge.website.crawlDeeper.successDesc', {
            added: response.pages_added,
            found: response.links_found
          })
        );
        // Refresh only the affected node's children instead of reloading entire tree
        await Promise.all([refreshNodeChildren(pageId), loadCrawlProgress()]);
      } else {
        showToast('warning', t('knowledge.website.crawlDeeper.noNewPages'), response.message);
      }
    } catch (err) {
      console.error('Failed to crawl deeper:', err);
      showToast('error', t('knowledge.website.crawlDeeper.failed'), err instanceof Error ? err.message : undefined);
    } finally {
      setCrawlingDeeperPages(prev => {
        const next = new Set(prev);
        next.delete(pageId);
        return next;
      });
    }
  };

  // Get status badge for page
  const getPageStatusBadge = (status: string) => {
    const statusConfig: Record<string, { color: string; label: string }> = {
      pending: { color: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400', label: t('knowledge.website.pageStatus.pending') },
      crawling: { color: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400', label: t('knowledge.website.pageStatus.crawling') },
      fetched: { color: 'bg-cyan-100 text-cyan-800 dark:bg-cyan-900/30 dark:text-cyan-400', label: t('knowledge.website.pageStatus.fetched') },
      extracted: { color: 'bg-indigo-100 text-indigo-800 dark:bg-indigo-900/30 dark:text-indigo-400', label: t('knowledge.website.pageStatus.extracted') },
      processing: { color: 'bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400', label: t('knowledge.website.pageStatus.processing') },
      processed: { color: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400', label: t('knowledge.website.pageStatus.processed') },
      failed: { color: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400', label: t('knowledge.website.pageStatus.failed') },
      skipped: { color: 'bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-400', label: t('knowledge.website.pageStatus.skipped') },
    };

    const config = statusConfig[status] || statusConfig.pending;
    return (
      <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${config.color}`}>
        {config.label}
      </span>
    );
  };

  // Render a tree node recursively
  const renderTreeNode = (node: PageTreeNode, level: number = 0): React.ReactNode => {
    const isDeleting = deletingPages.has(node.id);
    const isRecrawling = recrawlingPages.has(node.id);
    const isCrawlingDeeper = crawlingDeeperPages.has(node.id);
    const canCrawlDeeper = node.status === 'processed';
    const canRecrawl = ['processed', 'failed', 'skipped'].includes(node.status);

    return (
      <div key={node.id}>
        <div
          className={`flex items-center gap-2 py-2 px-3 hover:bg-gray-50 dark:hover:bg-gray-700/50 border-b border-gray-100 dark:border-gray-700/50 ${level > 0 ? 'bg-gray-50/50 dark:bg-gray-800/50' : ''}`}
          style={{ paddingLeft: `${level * 24 + 12}px` }}
        >
          {/* Expand/Collapse button */}
          <button
            onClick={() => toggleNodeExpand(node.id)}
            className={`p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors ${!node.hasChildren ? 'invisible' : ''}`}
            disabled={node.isLoading}
          >
            {node.isLoading ? (
              <Loader2 className="w-4 h-4 animate-spin text-gray-400" />
            ) : node.isExpanded ? (
              <ChevronDown className="w-4 h-4 text-gray-500" />
            ) : (
              <ChevronRight className="w-4 h-4 text-gray-500" />
            )}
          </button>

          {/* URL */}
          <a
            href={node.url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex-1 flex items-center gap-1 text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300 text-sm truncate min-w-0"
            title={node.url}
          >
            <span className="truncate">{node.title || node.url}</span>
            <ExternalLink className="w-3 h-3 flex-shrink-0" />
          </a>

          {/* Status */}
          <div className="flex-shrink-0">
            {getPageStatusBadge(node.status)}
          </div>

          {/* Depth */}
          <span className="flex-shrink-0 text-xs text-gray-500 dark:text-gray-400 w-8 text-center">
            D{node.depth}
          </span>

          {/* Actions */}
          <div className="flex-shrink-0 flex items-center gap-1">
            {/* Recrawl */}
            <button
              onClick={() => handleRecrawlPage(node.id)}
              disabled={isRecrawling || !canRecrawl}
              className="p-1 text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              title={t('knowledge.website.recrawlPage.button')}
            >
              {isRecrawling ? <Loader2 className="w-4 h-4 animate-spin" /> : <RotateCw className="w-4 h-4" />}
            </button>

            {/* Crawl Deeper */}
            <button
              onClick={() => handleCrawlDeeper(node.id)}
              disabled={isCrawlingDeeper || !canCrawlDeeper}
              className="p-1 text-gray-400 hover:text-green-600 dark:hover:text-green-400 hover:bg-green-50 dark:hover:bg-green-900/20 rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              title={canCrawlDeeper ? t('knowledge.website.crawlDeeper.button') : t('knowledge.website.crawlDeeper.notReady')}
            >
              {isCrawlingDeeper ? <Loader2 className="w-4 h-4 animate-spin" /> : <ArrowDownRight className="w-4 h-4" />}
            </button>

            {/* Delete */}
            <button
              onClick={() => handleDeletePage(node.id)}
              disabled={isDeleting}
              className="p-1 text-gray-400 hover:text-red-600 dark:hover:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              title={t('knowledge.website.deletePage.button')}
            >
              {isDeleting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
            </button>
          </div>
        </div>

        {/* Render children if expanded */}
        {node.isExpanded && node.children.length > 0 && (
          <div>
            {node.children.map(child => renderTreeNode(child, level + 1))}
          </div>
        )}
      </div>
    );
  };

  // Render loading state
  if (isLoading) {
    return (
      <div className="flex-grow flex items-center justify-center bg-gray-100 dark:bg-gray-900">
        <div className="inline-flex items-center">
          <Loader2 className="w-5 h-5 animate-spin text-blue-500 mr-2" />
          <span className="text-gray-500 dark:text-gray-400">{t('knowledge.website.loading')}</span>
        </div>
      </div>
    );
  }

  // Render error state
  if (error || !knowledgeBase) {
    return (
      <div className="flex-grow flex items-center justify-center bg-gray-100 dark:bg-gray-900">
        <div className="text-center">
          <AlertCircle className="w-12 h-12 text-red-500 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-800 dark:text-gray-100 mb-2">
            {t('knowledge.website.loadFailed')}
          </h3>
          <p className="text-gray-500 dark:text-gray-400 mb-4">{error}</p>
          <button
            onClick={() => navigate('/knowledge')}
            className="inline-flex items-center px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600"
          >
            {t('knowledge.detail.backToList')}
          </button>
        </div>
      </div>
    );
  }

  const hasActiveProgress = crawlProgress && (crawlProgress.pages_pending > 0 || crawlProgress.pages_processing > 0 || crawlProgress.progress_percent < 100);

  return (
    <div className="flex h-full w-full bg-gray-100 dark:bg-gray-900">
      <div className="flex-grow flex flex-col overflow-hidden">
        {/* Header */}
        <div className="bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 px-6 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              <button
                onClick={handleBack}
                className="p-2 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors"
              >
                <ArrowLeft className="w-5 h-5" />
              </button>
              <div className="flex items-center gap-3">
                <Globe className="w-6 h-6 text-blue-500" />
                <div>
                  <h1 className="text-xl font-semibold text-gray-800 dark:text-gray-100">
                    {knowledgeBase.name}
                  </h1>
                  {knowledgeBase.description && (
                    <p className="text-sm text-gray-500 dark:text-gray-400">{knowledgeBase.description}</p>
                  )}
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={handleRefresh}
                className="p-2 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors"
                title={t('common.refresh')}
              >
                <RefreshCw className="w-5 h-5" />
              </button>
              <button
                onClick={() => setIsAddPageDialogOpen(true)}
                className="inline-flex items-center gap-2 px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600 transition-colors"
              >
                <Plus className="w-4 h-4" />
                {t('knowledge.website.addPage.button')}
              </button>
            </div>
          </div>
        </div>

        {/* Crawl Progress Card */}
        {crawlProgress && (
          <div className="bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 px-6 py-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-6">
                <div className="flex items-center gap-2">
                  <span className="text-sm text-gray-500 dark:text-gray-400">{t('knowledge.website.progress.title')}:</span>
                  {hasActiveProgress ? (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400">
                      <Loader2 className="w-3 h-3 animate-spin" />
                      {t('knowledge.website.progress.crawling')}
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400">
                      <CheckCircle className="w-3 h-3" />
                      {t('knowledge.website.progress.completed')}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-4 text-sm">
                  <span className="text-gray-500 dark:text-gray-400">
                    {t('knowledge.website.progress.total')}: <span className="text-gray-700 dark:text-gray-200 font-medium">{crawlProgress.total_pages}</span>
                  </span>
                  <span className="text-gray-500 dark:text-gray-400">
                    {t('knowledge.website.progress.pending')}: <span className="text-gray-700 dark:text-gray-200 font-medium">{crawlProgress.pages_pending}</span>
                  </span>
                  <span className="text-gray-500 dark:text-gray-400">
                    {t('knowledge.website.progress.crawled')}: <span className="text-gray-700 dark:text-gray-200 font-medium">{crawlProgress.pages_crawled}</span>
                  </span>
                  {crawlProgress.pages_processing > 0 && (
                    <span className="text-amber-600 dark:text-amber-400">
                      {t('knowledge.website.progress.processing')}: <span className="font-medium">{crawlProgress.pages_processing}</span>
                    </span>
                  )}
                  <span className="text-gray-500 dark:text-gray-400">
                    {t('knowledge.website.progress.processed')}: <span className="text-gray-700 dark:text-gray-200 font-medium">{crawlProgress.pages_processed}</span>
                  </span>
                  {crawlProgress.pages_failed > 0 && (
                    <span className="text-red-500 dark:text-red-400">
                      {t('knowledge.website.progress.failed')}: <span className="font-medium">{crawlProgress.pages_failed}</span>
                    </span>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-3">
                <div className="text-sm text-gray-500 dark:text-gray-400">
                  {Math.round(crawlProgress.progress_percent)}%
                </div>
                {/* Progress bar */}
                <div className="w-32 h-2 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-blue-500 transition-all duration-300"
                    style={{ width: `${Math.round(crawlProgress.progress_percent)}%` }}
                  />
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Page Tree Header */}
        <div className="bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 px-6 py-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Globe className="w-4 h-4 text-gray-400" />
              <span className="text-sm font-medium text-gray-700 dark:text-gray-200">
                {t('knowledge.website.tabs.pages')} ({crawlProgress?.total_pages || rootPages.length})
              </span>
            </div>
            <div className="flex items-center gap-4 text-xs text-gray-500 dark:text-gray-400">
              <span>{t('knowledge.website.columns.url')}</span>
              <span>{t('knowledge.website.columns.status')}</span>
              <span>{t('knowledge.website.columns.depth')}</span>
              <span>{t('knowledge.website.columns.actions')}</span>
            </div>
          </div>
        </div>

        {/* Content - Pages Tree */}
        <div className="flex-grow overflow-y-auto">
          <KnowledgeVersionsPanel collectionId={knowledgeBase.id} kind="website" refreshKey={versionRefresh} />
          <SourceGovernanceSection collectionId={knowledgeBase.id} collectionName={knowledgeBase.name} sourceType="website" />
          {isLoadingPages ? (
            <div className="flex justify-center py-8">
              <Loader2 className="w-6 h-6 animate-spin text-blue-500" />
            </div>
          ) : rootPages.length === 0 ? (
            <div className="text-center py-8 text-gray-500 dark:text-gray-400">
              {t('knowledge.website.noPages')}
            </div>
          ) : (
            <div className="bg-white dark:bg-gray-800">
              {rootPages.map(node => renderTreeNode(node, 0))}
            </div>
          )}
        </div>
      </div>

      {/* Add Page Dialog */}
      {isAddPageDialogOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-xl w-full max-w-md mx-4">
            <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 dark:border-gray-700">
              <h3 className="text-lg font-semibold text-gray-800 dark:text-gray-100">
                {t('knowledge.website.addPage.title')}
              </h3>
              <button
                onClick={resetAddPageDialog}
                className="p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 rounded"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="px-6 py-4 space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  {t('knowledge.website.addPage.urlLabel')}
                </label>
                <input
                  type="url"
                  value={newPageUrl}
                  onChange={(e) => setNewPageUrl(e.target.value)}
                  placeholder={t('knowledge.website.addPage.urlPlaceholder')}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  autoFocus
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  {t('knowledge.website.addPage.parentPageLabel')}
                </label>
                <select
                  value={newPageParentId || ''}
                  onChange={(e) => setNewPageParentId(e.target.value || null)}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                >
                  <option value="">{t('knowledge.website.addPage.noParent')}</option>
                  {flattenTree(rootPages).map(page => (
                    <option key={page.id} value={page.id}>
                      {'─'.repeat(page.depth)} {page.label}
                    </option>
                  ))}
                </select>
                <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
                  {t('knowledge.website.addPage.parentPageHint')}
                </p>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  {t('knowledge.website.addPage.maxDepthLabel')}
                </label>
                <select
                  value={newPageMaxDepth}
                  onChange={(e) => setNewPageMaxDepth(Number(e.target.value))}
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                >
                  <option value={0}>{t('knowledge.website.addPage.depthOption0')}</option>
                  <option value={1}>{t('knowledge.website.addPage.depthOption1')}</option>
                  <option value={2}>{t('knowledge.website.addPage.depthOption2')}</option>
                  <option value={3}>{t('knowledge.website.addPage.depthOption3')}</option>
                  <option value={5}>{t('knowledge.website.addPage.depthOption5')}</option>
                  <option value={10}>{t('knowledge.website.addPage.depthOption10')}</option>
                </select>
                <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
                  {t('knowledge.website.addPage.hint')}
                </p>
              </div>
            </div>
            <div className="flex justify-end gap-3 px-6 py-4 border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-700/30">
              <button
                onClick={resetAddPageDialog}
                className="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-600 rounded-lg transition-colors"
              >
                {t('common.cancel')}
              </button>
              <button
                onClick={handleAddPage}
                disabled={isAddingPage || !newPageUrl.trim()}
                className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-blue-500 hover:bg-blue-600 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isAddingPage && <Loader2 className="w-4 h-4 animate-spin" />}
                {t('knowledge.website.addPage.submit')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default WebsiteKnowledgeBaseDetail;

